"""Tests for D-016 feed outages: the feed_gaps table and trips.no_data_in_outage.

Fixture (tests/fixtures/transform/static_feed_gaps_2026-09-07 +
realtime_feed_gaps_2026-09-07_{own,next_day}.parquet) is built from two real
routes/stops, with a small, deliberately sparse set of real-shaped snapshot
timestamps: a burst at 00:20:00-00:20:32 local, then nothing until
04:00:00-04:00:16 local. That produces, over the window from local midnight
to the end of hour 23 (no D+1 hours needed, since both fixture trips end well
before 22:00 local):
- a before_first gap (00:00:00 -> 00:20:00, 1200s)
- a between gap, the "night outage" (00:20:32 -> 04:00:00, 13168s)
- an after_last gap (04:00:16 -> 24:00:00), not asserted on directly

TRIP_INSIDE_OUTAGE (01:00-01:30 local) falls entirely inside the between gap
(the night outage). TRIP_OUTSIDE_OUTAGE (00:20:05-00:20:10 local) falls
entirely inside the burst of snapshots, i.e. outside every gap, while still
having no realtime data of its own.
"""

from datetime import date, timedelta
from pathlib import Path

import duckdb

from kronoberg_transit.transform import render_sql, run_hard_checks

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "transform"

WINDOW_START_UTC = 1788732000  # 2026-09-07 00:00:00 Europe/Stockholm
WINDOW_END_UTC = 1788818400  # 2026-09-08 00:00:00 Europe/Stockholm (no D+1 hours needed)


def build_pipeline() -> duckdb.DuckDBPyConnection:
    svc_date = "2026-09-07"
    svc_date_obj = date.fromisoformat(svc_date)
    date_int = int(svc_date.replace("-", ""))
    date_str_compact = svc_date.replace("-", "")
    weekday_col = svc_date_obj.strftime("%A").lower()

    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute(
        render_sql(
            "static_schedule.sql",
            static_dir=(FIXTURES / "static_feed_gaps_2026-09-07").as_posix(),
            date_int=date_int,
            weekday_col=weekday_col,
        )
    )
    con.execute(
        render_sql(
            "realtime_dedup.sql",
            glob_list=(
                "['"
                + (FIXTURES / "realtime_feed_gaps_2026-09-07_own.parquet").as_posix()
                + "', '"
                + (FIXTURES / "realtime_feed_gaps_2026-09-07_next_day.parquet").as_posix()
                + "']"
            ),
        )
    )
    con.execute(
        render_sql(
            "feed_gaps.sql",
            svc_date=svc_date,
            feed="TripUpdates",
            window_start_utc=WINDOW_START_UTC,
            window_end_utc=WINDOW_END_UTC,
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


def test_feed_gaps_before_first_and_night_outage():
    con = build_pipeline()
    rows = con.execute(
        "SELECT kind, gap_start_utc, gap_end_utc, gap_s FROM feed_gaps ORDER BY gap_start_utc"
    ).fetchall()

    assert len(rows) == 3
    kinds = [r[0] for r in rows]
    assert kinds == ["before_first", "between", "after_last"]

    before_first = rows[0]
    assert before_first[3] == 1200

    night_outage = rows[1]
    assert night_outage[3] == 13168

    after_last = rows[2]
    assert after_last[3] > 300


def test_hard_checks_pass_on_fixture():
    con = build_pipeline()
    run_hard_checks(con)


def test_no_realtime_data_trip_inside_outage_is_flagged():
    con = build_pipeline()
    row = con.execute(
        "SELECT trip_status, no_data_in_outage FROM trips WHERE trip_id = ?",
        ["TRIP_INSIDE_OUTAGE"],
    ).fetchone()
    assert row == ("no_realtime_data", True)


def test_no_realtime_data_trip_outside_any_outage_is_not_flagged():
    con = build_pipeline()
    row = con.execute(
        "SELECT trip_status, no_data_in_outage FROM trips WHERE trip_id = ?",
        ["TRIP_OUTSIDE_OUTAGE"],
    ).fetchone()
    assert row == ("no_realtime_data", False)
