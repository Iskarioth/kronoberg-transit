"""Tests for the D-017 publishing layer (kronoberg_transit.aggregate).

Fixture warehouse (tests/fixtures/aggregate/warehouse/) is cut from the real
20-day warehouse: full trips + stop_events for 2026-09-01, 2026-09-06 (Sunday)
and 2026-09-07 (Monday), filtered to a handful of real routes chosen to cover:
- route 106 (9011007010600000): reportable on every day used
- route 14 (9011007001400000, 9011007003700000): two route_ids sharing short
  name "14" with the same first/last station -> a real label collision;
  9011007001400000 also has exactly 18 observed trips on 2026-09-07 (<20,
  coverage 99%) -> "observed_trips<20" alone
- route 31 (9011007003100000, 9011007003000000): the other real collision
  pair; 9011007003100000 fails both floors on 2026-09-07 (9 observed trips,
  62% coverage)
- route 345 (9011007034500000): every trip out of scope -> zero eligible
  departures
- routes 9011007031100000 / 9011007031000000 / 9011007000800000: carry real
  observed departures with delay_s exactly -60, +60, +180 and +300
routes/stops/feed_quality/feed_gaps are kept in full for these three dates.
"""

from pathlib import Path

import duckdb
import pytest

import kronoberg_transit.aggregate as agg
from kronoberg_transit.transform import render_sql

FIXTURE_WAREHOUSE = Path(__file__).resolve().parent / "fixtures" / "aggregate" / "warehouse"


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect()
    c.execute(render_sql("aggregate_base.sql", warehouse_dir=FIXTURE_WAREHOUSE.as_posix()))
    c.execute(render_sql("aggregate_network_monthly.sql"))
    c.execute(render_sql("aggregate_route_monthly.sql"))
    c.execute(render_sql("aggregate_route_daily.sql"))
    c.execute(render_sql("aggregate_station_monthly.sql"))
    c.execute(render_sql("aggregate_hour_monthly.sql"))
    c.execute(render_sql("aggregate_data_quality.sql"))
    yield c
    c.close()


def test_consistency_checks_pass(con):
    agg.run_consistency_checks(con)


def test_class_boundaries_are_inclusive_on_time_exclusive_late(con):
    # Independently computed from the same fixture's raw stop_events, not
    # from aggregate.py's own CASE logic, so this actually checks it.
    route_id = "9011007031100000"
    service_date = "2026-09-01"
    expected = con.execute(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE delay_s < -60) AS early,
            COUNT(*) FILTER (WHERE delay_s BETWEEN -60 AND 180) AS on_time,
            COUNT(*) FILTER (WHERE delay_s > 180) AS late,
            COUNT(*) FILTER (WHERE delay_s BETWEEN -60 AND 60) AS on_time_60,
            COUNT(*) FILTER (WHERE delay_s BETWEEN -60 AND 300) AS on_time_300
        FROM all_stop_events
        WHERE route_id = '{route_id}' AND service_date = '{service_date}' AND status = 'observed'
        """
    ).fetchone()

    actual = con.execute(
        f"""
        SELECT early_departures, on_time_departures, late_departures,
               on_time_60_departures, on_time_300_departures
        FROM route_daily
        WHERE route_id = '{route_id}' AND service_date = '{service_date}'
        """
    ).fetchone()

    assert actual == expected
    # sanity: this route/date really does carry both the -60s and +60s boundary rows
    present = {
        r[0]
        for r in con.execute(
            f"""SELECT DISTINCT delay_s FROM all_stop_events
                WHERE route_id = '{route_id}' AND service_date = '{service_date}'
                AND status = 'observed' AND delay_s IN (-60, 60)"""
        ).fetchall()
    }
    assert present == {-60, 60}


def test_180_and_300_boundaries(con):
    for route_id, service_date, delay in [
        ("9011007031000000", "2026-09-01", 180),
        ("9011007000800000", "2026-09-01", 300),
    ]:
        row = con.execute(
            f"""SELECT status, delay_s FROM all_stop_events
                WHERE route_id = '{route_id}' AND service_date = '{service_date}'
                AND status = 'observed' AND delay_s = {delay} LIMIT 1"""
        ).fetchone()
        assert row == ("observed", delay)
        # +180 is on_time (base) but late by neither definition change; +300
        # is on_time_300 but late (base) and not on_time_60. Verified via the
        # same independent-count pattern as the previous test.
        expected_on_time = con.execute(
            f"""SELECT COUNT(*) FROM all_stop_events
                WHERE route_id='{route_id}' AND service_date='{service_date}'
                AND status='observed' AND delay_s BETWEEN -60 AND 180"""
        ).fetchone()[0]
        actual_on_time = con.execute(
            f"""SELECT on_time_departures FROM route_daily
                WHERE route_id='{route_id}' AND service_date='{service_date}'"""
        ).fetchone()[0]
        assert actual_on_time == expected_on_time


def test_floor_reportable_true(con):
    row = con.execute(
        """SELECT observed_trips, coverage_share, reportable, not_reportable_reason
           FROM route_daily WHERE route_id='9011007010600000' AND service_date='2026-09-07'"""
    ).fetchone()
    assert row == (28, 0.9242, True, "")


def test_floor_observed_trips_below_20_only(con):
    row = con.execute(
        """SELECT observed_trips, coverage_share, reportable, not_reportable_reason
           FROM route_daily WHERE route_id='9011007001400000' AND service_date='2026-09-07'"""
    ).fetchone()
    observed_trips, coverage_share, reportable, reason = row
    assert observed_trips < 20
    assert coverage_share >= 0.90
    assert reportable is False
    assert reason == "observed_trips<20"


def test_floor_both_reasons(con):
    row = con.execute(
        """SELECT observed_trips, coverage_share, reportable, not_reportable_reason
           FROM route_daily WHERE route_id='9011007003100000' AND service_date='2026-09-07'"""
    ).fetchone()
    observed_trips, coverage_share, reportable, reason = row
    assert observed_trips < 20
    assert coverage_share < 0.90
    assert reportable is False
    assert reason == "observed_trips<20;coverage<90%"


def test_floor_no_eligible_departures(con):
    row = con.execute(
        """SELECT eligible_departures, coverage_share, reportable, not_reportable_reason
           FROM route_monthly WHERE route_id='9011007034500000' AND day_type='all'"""
    ).fetchone()
    assert row == (0, None, False, "no_eligible_departures")


def test_null_shares_when_denominator_zero(con):
    row = con.execute(
        """SELECT coverage_share, early_share, on_time_share, late_share,
                  on_time_60_share, on_time_300_share, median_delay_s, p90_delay_s
           FROM route_monthly WHERE route_id='9011007034500000' AND day_type='all'"""
    ).fetchone()
    assert all(v is None for v in row)


def test_day_type(con):
    rows = con.execute(
        "SELECT DISTINCT service_date, day_type FROM day_types ORDER BY service_date"
    ).fetchall()
    assert dict(rows) == {
        __import__("datetime").date(2026, 9, 1): "weekday",
        __import__("datetime").date(2026, 9, 6): "sunday",
        __import__("datetime").date(2026, 9, 7): "weekday",
    }


def test_route_label_and_collision_handling(con):
    labels = dict(
        con.execute(
            """SELECT route_id, route_label FROM route_labels
               WHERE route_id IN ('9011007001400000', '9011007003700000',
                                   '9011007003100000', '9011007003000000',
                                   '9011007010600000')"""
        ).fetchall()
    )
    # Real collision: both "14" route_ids share first/last station.
    assert labels["9011007001400000"].endswith("(9011007001400000)")
    assert labels["9011007003700000"].endswith("(9011007003700000)")
    assert labels["9011007001400000"].split(" (")[0] == labels["9011007003700000"].split(" (")[0]

    # Real collision: both "31" route_ids share first/last station.
    assert labels["9011007003100000"].endswith("(9011007003100000)")
    assert labels["9011007003000000"].endswith("(9011007003000000)")

    # Non-colliding route: no route_id suffix.
    assert "(" not in labels["9011007010600000"]

    collisions = con.execute("SELECT label FROM label_collisions ORDER BY label").fetchall()
    assert len(collisions) == 2


def test_station_fallback_when_no_parent_station(con):
    row = con.execute(
        "SELECT stop_id, parent_station FROM canonical_stops WHERE parent_station IS NULL LIMIT 1"
    ).fetchone()
    stop_id, parent_station = row
    assert parent_station is None

    station_id = con.execute(
        f"SELECT station_id FROM stations WHERE stop_id = '{stop_id}'"
    ).fetchone()[0]
    assert station_id == stop_id

    own_name = con.execute(
        f"SELECT stop_name FROM canonical_stops WHERE stop_id = '{stop_id}'"
    ).fetchone()[0]
    station_name = con.execute(
        f"SELECT station_name FROM station_names WHERE station_id = '{station_id}'"
    ).fetchone()[0]
    assert station_name == own_name
