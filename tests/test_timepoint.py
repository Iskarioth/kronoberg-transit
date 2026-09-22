"""Tests for D-019 stop_events.is_timing_stop.

Both fixtures reuse the real trip 76110000040138578 (route 69, 2026-09-07):
5 stops with real timepoint values 1,0,0,0,1 (first and last are timing
stops; the 3 intermediate stops are not).
- static_quoting_2026-09-07: the real stop_times.txt row, unmodified.
- static_timepoint_empty_2026-09-07: derived from the same real fixture with
  the first stop's timepoint blanked, to test that GTFS's "empty means exact
  time" rule is honoured.
Realtime input is the existing empty (0-row) parquet fixture -
is_timing_stop depends only on the static schedule, not on realtime data.
"""

from datetime import date
from pathlib import Path

import duckdb

from kronoberg_transit.transform import render_sql, run_hard_checks

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "transform"
EMPTY_REALTIME = FIXTURES / "realtime_feed_gaps_2026-09-07_next_day.parquet"

TRIP_ID = "76110000040138578"


def build_pipeline(static_dirname: str) -> duckdb.DuckDBPyConnection:
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
            static_dir=(FIXTURES / static_dirname).as_posix(),
            date_int=date_int,
            weekday_col=weekday_col,
        )
    )
    con.execute(
        render_sql(
            "realtime_dedup.sql",
            glob_list="['" + EMPTY_REALTIME.as_posix() + "', '" + EMPTY_REALTIME.as_posix() + "']",
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

    con.create_function(
        "scheduled_time_utc",
        lambda hms: _scheduled_time_utc_py(svc_date_obj, hms) if hms else None,
        ["VARCHAR"],
        "BIGINT",
    )
    con.execute(render_sql("trips.sql", svc_date=svc_date))
    con.execute(render_sql("stop_events.sql", svc_date=svc_date))
    return con


def test_hard_checks_pass_with_mixed_timing_stops():
    con = build_pipeline("static_quoting_2026-09-07")
    run_hard_checks(con)


def test_mixed_timing_and_non_timing_stops():
    con = build_pipeline("static_quoting_2026-09-07")
    rows = con.execute(
        "SELECT stop_sequence, is_timing_stop FROM stop_events WHERE trip_id = ? ORDER BY stop_sequence",
        [TRIP_ID],
    ).fetchall()
    assert rows == [
        (1, True),  # first stop, real timepoint=1
        (2, False),  # real timepoint=0
        (3, False),  # real timepoint=0
        (4, False),  # real timepoint=0
        (5, True),  # final stop, real timepoint=1
    ]


def test_empty_timepoint_is_treated_as_timing_stop():
    con = build_pipeline("static_timepoint_empty_2026-09-07")
    run_hard_checks(con)
    row = con.execute(
        "SELECT is_timing_stop FROM stop_events WHERE trip_id = ? AND stop_sequence = 1",
        [TRIP_ID],
    ).fetchone()
    assert row == (True,)

    # sanity: the underlying static value really is empty for this row
    raw = con.execute(
        "SELECT timepoint FROM scheduled_stop_times WHERE trip_id = ? AND stop_sequence = 1",
        [TRIP_ID],
    ).fetchone()
    assert raw[0] is None or raw[0] == ""
