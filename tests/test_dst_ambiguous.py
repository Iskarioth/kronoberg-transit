"""Tests for the dst_ambiguous stop-event status and schedule-mismatch/
unmatched-realtime-trip counting (D-021), against small real fixtures cut
from the cached daylight-saving archives.

Coverage:
- a 26:xx departure on 2026-03-28 becomes dst_ambiguous
- a 25:xx departure on 2026-03-28 does not
- a departure set to 02:30:00 on the change date itself (2026-03-29) becomes
  dst_ambiguous
- precedence: a stop marked SKIPPED in the window stays skipped, not
  dst_ambiguous
- schedule_mismatch_stop_events never counts a trip that matches no
  scheduled trip on D (it counts matched trips only, by definition)
- unmatched_realtime_trips counts the mislabelled 2025-10-25 trip instead,
  and that trip has no stop_events row for 2025-10-25
"""

from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

from kronoberg_transit.transform import duckdb_list_literal, render_sql

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "transform"
EMPTY_REALTIME = FIXTURES / "realtime_feed_gaps_2026-09-07_next_day.parquet"

TRIP_26 = "76110000030471926"  # real trip, all stops 26:20:00-26:50:00 on 2026-03-28
TRIP_25 = "76110000041829974"  # real trip, stops 1-28 at 25:15:00-25:54:19 on 2026-03-28
MISMATCH_TRIP = "76110000036776160"  # real trip, recurs identically in 2025-10-24/25 statics
TRIP_0230 = "76110000039214042"  # real trip, genuinely scheduled on 2026-03-29; stop 2 edited
SKIPPED_REALTIME = FIXTURES / "realtime_dst_2026-03-28_skipped.parquet"
MISMATCH_REALTIME = FIXTURES / "realtime_dst_2025-10-25_own.parquet"


def build_pipeline(svc_date: str, static_dirname: str, own_parquet: Path = EMPTY_REALTIME):
    svc_date_obj = date.fromisoformat(svc_date)
    date_int = int(svc_date.replace("-", ""))
    date_str_compact = svc_date.replace("-", "")
    weekday_col = svc_date_obj.strftime("%A").lower()

    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute(
        render_sql(
            "static_schedule.sql",
            static_dir=(FIXTURES / static_dirname).as_posix(),
            date_int=date_int,
            weekday_col=weekday_col,
        )
    )
    con.execute(
        render_sql(
            "realtime_dedup.sql",
            glob_list=duckdb_list_literal([own_parquet, EMPTY_REALTIME]),
        )
    )
    from kronoberg_transit.time_utils import is_offset_change_date
    from kronoberg_transit.time_utils import scheduled_time_utc as _scheduled_time_utc_py

    con.execute(
        render_sql(
            "feed_gaps.sql",
            svc_date=svc_date,
            feed="TripUpdates",
            window_start_utc=_scheduled_time_utc_py(svc_date_obj, "00:00:00"),
            window_end_utc=_scheduled_time_utc_py(svc_date_obj, "28:00:00"),
        )
    )
    con.execute(render_sql("held_values.sql", date_str=date_str_compact))

    con.create_function(
        "scheduled_time_utc",
        lambda hms: _scheduled_time_utc_py(svc_date_obj, hms) if hms else None,
        ["VARCHAR"],
        "BIGINT",
    )
    con.execute(render_sql("trips.sql", svc_date=svc_date))
    con.execute(
        render_sql(
            "stop_events.sql",
            svc_date=svc_date,
            s_is_change_date=str(is_offset_change_date(svc_date_obj)).lower(),
            s_plus_1_is_change_date=str(
                is_offset_change_date(svc_date_obj + timedelta(days=1))
            ).lower(),
        )
    )
    return con


def stop_events_for(con, trip_id):
    return con.execute(
        "SELECT stop_sequence, status, delay_s FROM stop_events "
        "WHERE trip_id = ? ORDER BY stop_sequence",
        [trip_id],
    ).fetchall()


@pytest.fixture(scope="module")
def con_2026_03_28():
    con = build_pipeline("2026-03-28", "static_dst_2026-03-28")
    yield con
    con.close()


def test_26xx_departure_on_2026_03_28_is_dst_ambiguous(con_2026_03_28):
    # Real trip, all non-final stops scheduled 26:20:00-26:47:27 (nominal
    # hour 2, nominal date 2026-03-29 - the real spring-forward day).
    rows = stop_events_for(con_2026_03_28, TRIP_26)
    assert rows, "fixture trip missing from stop_events"
    non_final = rows[:-1]  # last row is the trip's final stop -> status NULL
    assert non_final, "need at least one non-final stop"
    for _seq, status, delay_s in non_final:
        assert status == "dst_ambiguous"
        assert delay_s is None


def test_25xx_departure_on_2026_03_28_is_not_dst_ambiguous(con_2026_03_28):
    # Real trip, stops 1-28 scheduled 25:15:00-25:54:19 (nominal hour 1,
    # not in {2, 3}) - must not be marked dst_ambiguous.
    rows = stop_events_for(con_2026_03_28, TRIP_25)
    assert rows
    non_final = rows[:-1]
    assert non_final
    for _seq, status, _delay_s in non_final:
        assert status != "dst_ambiguous"
        # No realtime data in this fixture, so the real status is unobserved.
        assert status == "unobserved"


def test_hard_checks_pass_on_2026_03_28_fixture(con_2026_03_28):
    from kronoberg_transit.transform import run_hard_checks

    run_hard_checks(con_2026_03_28, s_is_change_date=False, s_plus_1_is_change_date=True)


@pytest.fixture(scope="module")
def con_2025_10_25():
    con = build_pipeline("2025-10-25", "static_dst_2025-10-25", own_parquet=MISMATCH_REALTIME)
    con.execute(render_sql("feed_quality_dst.sql"))
    yield con
    con.close()


def test_schedule_mismatch_stop_events_excludes_an_unmatched_trip(con_2025_10_25):
    # Trip 76110000036776160's static service_id ('4') is active on Fridays
    # (2025-10-24, 2025-10-31, ...) per the real calendar_dates.txt - NOT on
    # 2025-10-25 (a Saturday). Its trip_id recurs with byte-identical
    # departure_time values across the whole schedule, so it also has a row
    # in the 2025-10-25 archive's stop_times.txt, but that row is never
    # "scheduled" for 2025-10-25 by the GTFS calendar. Its real realtime row
    # is nonetheless tagged start_date="20251025" by the feed (confirmed
    # against the real cached archive) - the day-mislabelling D-021's own
    # reason section describes.
    #
    # schedule_mismatch_stop_events counts matched trips only, by
    # definition (D-021): it INNER JOINs realtime_trip_rows to stop_events
    # on (trip_id, stop_sequence). Since this trip has no stop_events row
    # for 2025-10-25 (scheduled_trips, calendar-filtered, never includes
    # it), it correctly finds no match here - unmatched_realtime_trips
    # (tested below) is what catches this case instead.
    n_trip_rows = con_2025_10_25.execute(
        "SELECT COUNT(*) FROM trips WHERE trip_id = ?", [MISMATCH_TRIP]
    ).fetchone()[0]
    assert n_trip_rows == 0, "fixture no longer reproduces the mislabelled-day case"

    schedule_mismatch_updates, schedule_mismatch_stop_events = con_2025_10_25.execute(
        "SELECT schedule_mismatch_updates, schedule_mismatch_stop_events FROM schedule_mismatch_summary"
    ).fetchone()
    assert schedule_mismatch_stop_events == 0
    assert schedule_mismatch_updates == 0


def test_unmatched_realtime_trips_counts_the_mislabelled_trip(con_2025_10_25):
    unmatched = con_2025_10_25.execute(
        "SELECT unmatched_realtime_trips FROM unmatched_realtime_trips_summary"
    ).fetchone()[0]
    assert unmatched == 1

    # And it is not, and cannot be, in stop_events for this date.
    n_stop_events = con_2025_10_25.execute(
        "SELECT COUNT(*) FROM stop_events WHERE trip_id = ?", [MISMATCH_TRIP]
    ).fetchone()[0]
    assert n_stop_events == 0


@pytest.fixture(scope="module")
def con_2026_03_29_0230():
    con = build_pipeline("2026-03-29", "static_dst_2026-03-29")
    yield con
    con.close()


def test_0230_departure_on_the_change_date_itself_is_dst_ambiguous(con_2026_03_29_0230):
    # Real trip 76110000039214042 is genuinely scheduled on 2026-03-29
    # (service_id 12, calendar_dates exception_type=1 for 20260329) at
    # ordinary evening times (20:50-21:03). No real trip in the cached
    # 2026-03-29 schedule uses non-extended-notation hour 02 or 03 (the
    # local wall-clock window barely exists that day) - confirmed by direct
    # query against the real static data. So stop_sequence 2's
    # departure_time (and arrival_time) is edited in the fixture CSV from
    # its real 20:50:50 to 02:29:30/02:30:00, to test the "S itself is the
    # change date, non-extended notation, hour in {2,3}" branch of the rule.
    rows = stop_events_for(con_2026_03_29_0230, TRIP_0230)
    assert rows
    by_seq = {seq: (status, delay_s) for seq, status, delay_s in rows}
    assert 2 in by_seq
    status, delay_s = by_seq[2]
    assert status == "dst_ambiguous"
    assert delay_s is None
    # Sanity: the other (real, unedited) non-final stops are not.
    other_non_final = {seq: s for seq, (s, _d) in by_seq.items() if seq not in (2, max(by_seq))}
    assert other_non_final and all(s != "dst_ambiguous" for s in other_non_final.values())


@pytest.fixture(scope="module")
def con_2026_03_28_skipped():
    con = build_pipeline("2026-03-28", "static_dst_2026-03-28", own_parquet=SKIPPED_REALTIME)
    yield con
    con.close()


def test_skipped_stop_in_the_window_stays_skipped(con_2026_03_28_skipped):
    # TRIP_26 stop_sequence 2 (real hms 26:21:15 -> dst-ambiguous window) is
    # given a real SKIPPED stop_time_update in the realtime fixture. Status
    # precedence (D-009, D-021) puts skipped before dst_ambiguous, so it
    # must stay skipped.
    rows = stop_events_for(con_2026_03_28_skipped, TRIP_26)
    by_seq = {seq: (status, delay_s) for seq, status, delay_s in rows}
    assert 2 in by_seq
    status, delay_s = by_seq[2]
    assert status == "skipped"
    assert delay_s is None
