"""Tests for D-013 trip scope: trips run by a neighbouring authority (any
agency in agency.txt other than Länstrafiken Kronoberg) are out of scope.

Fixtures are cut from the cached static schedules (real trip_id, route_id,
service_id, calendar rows) and, for the full-pipeline case, the real
realtime parquet fixtures already used by test_transform.py.
"""

from datetime import date
from pathlib import Path

import duckdb
import pytest

from kronoberg_transit.transform import (
    duckdb_list_literal,
    render_sql,
    run_hard_checks,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "transform"


def load_static(dirname: str, date_int: int, weekday_col: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute(
        render_sql(
            "static_schedule.sql",
            static_dir=(FIXTURES / dirname).as_posix(),
            date_int=date_int,
            weekday_col=weekday_col,
        )
    )
    return con


def test_skanetrafiken_trip_on_route_521_is_out_of_scope():
    con = load_static("static_scope_2026-09-07", 20260907, "monday")
    row = con.execute(
        "SELECT operator, in_scope FROM trip_scope WHERE trip_id = ?",
        ["76110000035912067"],
    ).fetchone()
    assert row == ("Skånetrafiken", False)


def test_kalmars_trip_on_route_310_is_out_of_scope():
    con = load_static("static_scope_2026-09-06", 20260906, "sunday")
    row = con.execute(
        "SELECT operator, in_scope FROM trip_scope WHERE trip_id = ?",
        ["76110000042432234"],
    ).fetchone()
    assert row == ("Kalmars Länsstrafik", False)


def test_connect_bus_trip_on_route_310_is_in_scope():
    con = load_static("static_scope_2026-09-06", 20260906, "sunday")
    row = con.execute(
        "SELECT operator, in_scope FROM trip_scope WHERE trip_id = ?",
        ["76110000042434006"],
    ).fetchone()
    assert row == ("Connect bus", True)


def test_trip_without_attribution_row_is_in_scope():
    con = load_static("static_no_attribution_2026-09-06", 20260906, "sunday")
    row = con.execute(
        "SELECT operator, in_scope FROM trip_scope WHERE trip_id = ?",
        ["76110000042434006"],
    ).fetchone()
    assert row == (None, True)


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
    con.execute(render_sql("stop_events.sql", svc_date=svc_date))
    return con


NORMAL_TRIP = "76110000044237190"


@pytest.fixture(scope="module")
def con_reassigned_operator():
    con = build_pipeline(
        "2026-09-07",
        FIXTURES / "static_2026-09-07_reassigned_operator",
        FIXTURES / "realtime_2026-09-07_own.parquet",
        FIXTURES / "realtime_2026-09-07_next_day.parquet",
    )
    yield con
    con.close()


def test_out_of_scope_trip_that_appears_in_feed(con_reassigned_operator):
    con = con_reassigned_operator
    run_hard_checks(con)

    trip = con.execute(
        "SELECT operator, in_scope, trip_status, first_seen_utc FROM trips WHERE trip_id = ?",
        [NORMAL_TRIP],
    ).fetchone()
    operator, in_scope, trip_status, first_seen_utc = trip
    assert operator == "Skånetrafiken"
    assert in_scope is False
    assert trip_status == "out_of_scope"
    assert first_seen_utc is not None  # it really did appear in TripUpdates

    rows = con.execute(
        "SELECT stop_position, status, in_scope FROM stop_events "
        "WHERE trip_id = ? ORDER BY stop_sequence",
        [NORMAL_TRIP],
    ).fetchall()
    non_final = [r for r in rows if r[0] != "final"]
    assert non_final
    assert all(status == "out_of_scope" for _pos, status, _in_scope in non_final)
    assert all(in_scope is False for *_rest, in_scope in rows)

    out_of_scope_trips_in_feed = con.execute(
        "SELECT COUNT(*) FROM trips WHERE trip_status = 'out_of_scope' AND first_seen_utc IS NOT NULL"
    ).fetchone()[0]
    assert out_of_scope_trips_in_feed == 1
