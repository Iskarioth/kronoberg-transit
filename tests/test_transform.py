"""Tests for the DuckDB SQL layer of kronoberg_transit.transform, against
small real fixtures cut from the cached static schedules and the interim
parquet produced by real transform runs (tests/fixtures/transform/).

Covers D-007 to D-011: a normal trip, a looping trip that revisits a
stop_id, a trip with non-final SKIPPED stops, a cancelled trip, a trip
whose stops left the feed and came back, a scheduled trip with no
realtime data, and a trip found in the wrong day's archives.
"""

from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

from kronoberg_transit.transform import (
    RunLog,
    build_feed_quality,
    compute_next_day_cutoff_hours,
    duckdb_list_literal,
    render_sql,
    run_hard_checks,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "transform"

NORMAL_TRIP = "76110000044237190"
LOOPING_TRIP = "76110000040137819"
SKIPPED_TRIP = "76110000042420681"
CANCELLED_TRIP = "76110000042500503"
REAPPEARED_TRIP = "76110000043920067"
NO_RT_TRIP = "76110000044132375"

TRIP_06 = "76110100044217987"
MISMATCHED_TRIP = "76110000036556696"


def build_pipeline(svc_date: str, static_dir: Path, own_parquet: Path, next_day_parquet: Path):
    svc_date_obj = date.fromisoformat(svc_date)
    date_int = int(svc_date.replace("-", ""))
    date_str_compact = svc_date.replace("-", "")
    weekday_col = svc_date_obj.strftime("%A").lower()

    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute(
        render_sql(
            "static_schedule.sql",
            static_dir=static_dir.as_posix(),
            date_int=date_int,
            weekday_col=weekday_col,
        )
    )
    con.execute(
        render_sql(
            "realtime_dedup.sql",
            glob_list=duckdb_list_literal([own_parquet, next_day_parquet]),
        )
    )
    from kronoberg_transit.time_utils import scheduled_time_utc as _scheduled_time_utc_py

    con.execute(
        render_sql(
            "feed_gaps.sql",
            svc_date=svc_date,
            feed="TripUpdates",
            window_start_utc=_scheduled_time_utc_py(svc_date_obj, "00:00:00"),
            window_end_utc=_scheduled_time_utc_py(svc_date_obj, "24:00:00"),
        )
    )
    con.execute(render_sql("held_values.sql", date_str=date_str_compact))

    from kronoberg_transit.time_utils import scheduled_time_utc

    con.create_function(
        "scheduled_time_utc",
        lambda hms: scheduled_time_utc(svc_date_obj, hms) if hms else None,
        ["VARCHAR"],
        "BIGINT",
    )
    con.execute(render_sql("trips.sql", svc_date=svc_date))

    from kronoberg_transit.time_utils import is_offset_change_date

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


@pytest.fixture(scope="module")
def con_2026_09_07():
    con = build_pipeline(
        "2026-09-07",
        FIXTURES / "static_2026-09-07",
        FIXTURES / "realtime_2026-09-07_own.parquet",
        FIXTURES / "realtime_2026-09-07_next_day.parquet",
    )
    yield con
    con.close()


@pytest.fixture(scope="module")
def con_2026_09_06():
    con = build_pipeline(
        "2026-09-06",
        FIXTURES / "static_2026-09-06",
        FIXTURES / "realtime_2026-09-06_own.parquet",
        FIXTURES / "realtime_2026-09-06_next_day.parquet",
    )
    yield con
    con.close()


def stop_events_for(con, trip_id):
    return con.execute(
        "SELECT stop_sequence, stop_id, stop_position, status, delay_s, "
        "arrival_marker, departure_marker, last_stop_relationship, "
        "held_arrival_utc, held_departure_utc, last_seen_utc "
        "FROM stop_events WHERE trip_id = ? ORDER BY stop_sequence",
        [trip_id],
    ).fetchall()


def test_hard_checks_pass_on_fixture(con_2026_09_07):
    run_hard_checks(con_2026_09_07)


def test_normal_trip_observed_with_sensible_delay(con_2026_09_07):
    rows = stop_events_for(con_2026_09_07, NORMAL_TRIP)
    assert len(rows) == 8
    assert rows[0][2] == "first"
    assert rows[-1][2] == "final"
    assert all(r[2] == "intermediate" for r in rows[1:-1])
    assert rows[-1][3] is None  # final stop has no status

    non_final = rows[:-1]
    assert all(r[3] in ("observed", "unobserved") for r in non_final)
    for seq, _sid, _pos, status, delay_s, *_ in non_final:
        if status == "observed":
            assert delay_s is not None
            assert -3600 < delay_s < 3600
        else:
            assert delay_s is None


def test_looping_trip_keyed_on_stop_sequence_not_stop_id(con_2026_09_07):
    rows = stop_events_for(con_2026_09_07, LOOPING_TRIP)
    stop_ids = [r[1] for r in rows]
    stop_seqs = [r[0] for r in rows]

    # The trip must keep every scheduled stop_sequence even though a
    # stop_id repeats - a stop_id-keyed join would have fanned out or
    # silently dropped one occurrence.
    assert len(stop_seqs) == len(set(stop_seqs))
    assert len(stop_ids) != len(set(stop_ids)), "fixture trip should revisit a stop_id"

    repeated = next(sid for sid in set(stop_ids) if stop_ids.count(sid) > 1)
    occurrences = [r for r in rows if r[1] == repeated]
    assert len(occurrences) == 2
    assert occurrences[0][0] != occurrences[1][0]


def test_skipped_stops_excluded_from_punctuality(con_2026_09_07):
    rows = stop_events_for(con_2026_09_07, SKIPPED_TRIP)
    skipped = [r for r in rows if r[3] == "skipped"]
    assert skipped, "fixture trip should have at least one skipped non-final stop"
    for row in skipped:
        _seq, _sid, pos, status, delay_s, _am, _dm, last_rel, *_ = row
        assert pos != "final"
        assert status == "skipped"
        assert delay_s is None
        assert last_rel == "SKIPPED"


def test_cancelled_trip_stops_all_cancelled_except_final(con_2026_09_07):
    trip_status = con_2026_09_07.execute(
        "SELECT trip_status FROM trips WHERE trip_id = ?", [CANCELLED_TRIP]
    ).fetchone()[0]
    assert trip_status == "cancelled"

    rows = stop_events_for(con_2026_09_07, CANCELLED_TRIP)
    non_final = [r for r in rows if r[2] != "final"]
    final = [r for r in rows if r[2] == "final"]
    assert non_final and final
    assert all(r[3] == "cancelled" for r in non_final)
    assert all(r[4] is None for r in non_final)  # delay_s
    assert all(r[3] is None for r in final)  # status null even though trip cancelled


def test_reappeared_stops_use_last_appearance_with_marker(con_2026_09_07):
    rows = stop_events_for(con_2026_09_07, REAPPEARED_TRIP)
    by_seq = {r[0]: r for r in rows}
    # Per docs/validation/held_values_2026-09-07.md, stop_sequences 11-18 of
    # this trip left the feed and came back; the returned value carries the
    # recorded-time marker even though the pre-drop value did not.
    for seq in range(11, 19):
        assert seq in by_seq, f"stop_sequence {seq} missing from fixture"
        _seq, _sid, pos, status, delay_s, _arrival_marker, departure_marker, *_ = by_seq[seq]
        if pos == "final":
            continue
        assert departure_marker is True, f"stop_sequence {seq} should carry the recorded marker"
        assert status == "observed"
        assert delay_s is not None


def test_trip_with_no_realtime_data(con_2026_09_07):
    trip_status = con_2026_09_07.execute(
        "SELECT trip_status, first_seen_utc, last_seen_utc FROM trips WHERE trip_id = ?",
        [NO_RT_TRIP],
    ).fetchone()
    assert trip_status[0] == "no_realtime_data"
    assert trip_status[1] is None
    assert trip_status[2] is None

    rows = stop_events_for(con_2026_09_07, NO_RT_TRIP)
    non_final = [r for r in rows if r[2] != "final"]
    assert non_final
    for row in non_final:
        _seq, _sid, _pos, status, delay_s, _am, _dm, _lsr, held_arr, held_dep, last_seen = row
        assert status == "unobserved"
        assert delay_s is None
        assert held_arr is None
        assert held_dep is None
        assert last_seen is None


@pytest.mark.parametrize(
    ("svc_date", "max_arrival_hms", "expected_hours"),
    [
        # Real 2026-09-06 last scheduled arrival (26:00:00 -> 02:00 local on
        # 2026-09-07); +2h cutoff falls in local hour 4 on 2026-09-07.
        ("2026-09-06", "26:00:00", [0, 1, 2, 3, 4]),
        # Real 2026-09-07 last scheduled arrival (25:08:00 -> 01:08 local on
        # 2026-09-08); +2h cutoff falls in local hour 3 on 2026-09-08.
        ("2026-09-07", "25:08:00", [0, 1, 2, 3]),
        # Last arrival well before midnight: cutoff (+2h) still on D itself.
        ("2026-09-07", "20:00:00", []),
        # D-011 spring-change case: D+1 is 2026-03-29 (23h day, no local
        # hour 02). The GTFS noon-anchor rule (scheduled_time_utc) adds
        # 26:30:00 in fixed-offset UTC arithmetic from D's noon; because the
        # spring change loses an hour of local wall-clock time on D+1, this
        # lands at local 05:30 CEST, not the naively-expected 04:30. The
        # cutoff window must read D+1 hours up to 05, but the returned list
        # must never include 02, which doesn't exist.
        ("2026-03-28", "26:30:00", [0, 1, 3, 4, 5]),
        # D-011 autumn-change case: D+1 is 2025-10-26 (25h day). A last
        # arrival of 24:30:00 (-> 00:30 local on D+1) plus 2h lands at 02:30
        # local on D+1; hour 02's archive already holds both real
        # occurrences of that local hour, so the label list is unaffected.
        ("2025-10-25", "24:30:00", [0, 1, 2]),
    ],
)
def test_compute_next_day_cutoff_hours(svc_date, max_arrival_hms, expected_hours):
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute("CREATE TABLE scheduled_stop_times AS SELECT ? AS arrival_time", [max_arrival_hms])
    hours = compute_next_day_cutoff_hours(con, date.fromisoformat(svc_date))
    assert hours == expected_hours


def test_feed_quality_never_lists_a_nonexistent_local_hour(tmp_path):
    # 2026-03-29 is a spring-change (23h) day with no local hour 02 archive
    # (D-020/D-011). own_hours (as computed by run_transform) must already
    # exclude label 02, so local_hours_without_snapshots can never list it -
    # built from one real staged hour (cut from the cached KoDa archive) so
    # only hour 00 is "present" and every other real hour is "without".
    fixture = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "transform"
        / "feed_quality_2026-03-29_hour00.parquet"
    )
    own_interim = tmp_path / "own"
    own_interim.mkdir()
    (own_interim / "00.parquet").write_bytes(fixture.read_bytes())

    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute("CREATE TABLE trips (trip_status VARCHAR, first_seen_utc TIMESTAMP)")
    con.execute("""
        CREATE TABLE stop_events (
            trip_id VARCHAR, stop_sequence INTEGER, status VARCHAR,
            scheduled_arrival_utc TIMESTAMP, scheduled_departure_utc TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE realtime_trip_rows (
            trip_id VARCHAR, stop_sequence INTEGER,
            arrival_time_present BOOLEAN, arrival_time BIGINT,
            arrival_delay_present BOOLEAN, arrival_delay INTEGER,
            departure_time_present BOOLEAN, departure_time BIGINT,
            departure_delay_present BOOLEAN, departure_delay INTEGER
        )
    """)
    con.execute("CREATE TABLE scheduled_trips (trip_id VARCHAR)")

    from kronoberg_transit.time_utils import local_hour_labels

    own_hours = local_hour_labels(date(2026, 3, 29))
    assert 2 not in own_hours

    log = RunLog("2026-03-29")
    row = build_feed_quality(con, "2026-03-29", own_interim, own_hours, [], log)

    hours_without = row["local_hours_without_snapshots"].split(",")
    assert "02" not in hours_without
    assert "00" not in hours_without  # hour 00 was staged, so it is present
    assert set(hours_without) == {f"{h:02d}" for h in own_hours if h != 0}


def test_trip_with_mismatched_start_date_is_ignored(con_2026_09_06):
    con = con_2026_09_06
    # Scheduled trip for 2026-09-06 builds normally.
    n_trips = con.execute("SELECT COUNT(*) FROM trips WHERE trip_id = ?", [TRIP_06]).fetchone()[0]
    assert n_trips == 1

    # The mismatched trip (start_date 2026-09-05, seen in 2026-09-06's own
    # archives per D-011) never enters the matched realtime population...
    n_matched = con.execute(
        "SELECT COUNT(*) FROM realtime_trip_rows WHERE trip_id = ?", [MISMATCHED_TRIP]
    ).fetchone()[0]
    assert n_matched == 0

    # ...but is captured separately, with its real start_date.
    ignored_dates = con.execute(
        "SELECT DISTINCT start_date FROM ignored_prior_day_rows WHERE trip_id = ?",
        [MISMATCHED_TRIP],
    ).fetchall()
    assert ignored_dates == [("20260905",)]

    # It is not part of D's own trips/stop_events tables (D-011: it belongs
    # to 2026-09-05, not to this run's service date).
    assert (
        con.execute("SELECT COUNT(*) FROM trips WHERE trip_id = ?", [MISMATCHED_TRIP]).fetchone()[0]
        == 0
    )
