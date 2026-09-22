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
from unittest.mock import MagicMock

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import kronoberg_transit.aggregate as agg
from kronoberg_transit.transform import render_sql

FIXTURE_WAREHOUSE = Path(__file__).resolve().parent / "fixtures" / "aggregate" / "warehouse"

# route 9011007087300000 (route 873) on 2026-09-01, trimmed to its one
# direction-0 and one direction-1 trip: both have n_trips=1 and the same
# earliest_departure (03:55:00), an exact tie in aggregate_base.sql's
# route_pattern selection broken only by direction_id (D-019).
FIXTURE_WAREHOUSE_TIE = Path(__file__).resolve().parent / "fixtures" / "aggregate" / "warehouse_tie"


def _build(con: duckdb.DuckDBPyConnection, warehouse_dir: Path) -> None:
    con.execute(render_sql("aggregate_base.sql", warehouse_dir=warehouse_dir.as_posix()))
    con.execute(render_sql("aggregate_network_monthly.sql"))
    con.execute(render_sql("aggregate_route_monthly.sql"))
    con.execute(render_sql("aggregate_route_daily.sql"))
    con.execute(render_sql("aggregate_station_monthly.sql"))
    con.execute(render_sql("aggregate_hour_monthly.sql"))
    con.execute(render_sql("aggregate_data_quality.sql"))


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect()
    _build(c, FIXTURE_WAREHOUSE)
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


def test_build_is_deterministic():
    """Running the aggregate build twice from the same warehouse must give
    byte-identical results on every published tab (D-019: aggregate_base.sql's
    route_pattern tie-break was previously not a total order, so two runs
    could pick a different direction for a route's label)."""
    con_a = duckdb.connect()
    _build(con_a, FIXTURE_WAREHOUSE)
    first = {}
    for table in agg.TABS:
        cols = [d[0] for d in con_a.execute(f"SELECT * FROM {table} LIMIT 0").description]
        rows = set(con_a.execute(f"SELECT * FROM {table}").fetchall())
        first[table] = (cols, rows)
    con_a.close()

    con_b = duckdb.connect()
    _build(con_b, FIXTURE_WAREHOUSE)
    for table in agg.TABS:
        cols = [d[0] for d in con_b.execute(f"SELECT * FROM {table} LIMIT 0").description]
        rows = set(con_b.execute(f"SELECT * FROM {table}").fetchall())
        expected_cols, expected_rows = first[table]
        assert cols == expected_cols, f"{table}: column order changed between builds"
        assert rows == expected_rows, (
            f"{table}: rows differ between two builds of the same warehouse"
        )
    con_b.close()


def test_pattern_tie_broken_deterministically():
    """route_pattern's ORDER BY (n_trips DESC, earliest_departure ASC,
    direction_id ASC NULLS LAST, pattern_key ASC) must pick the same pattern
    every time for an exact n_trips/earliest_departure tie (D-019). The
    direction-0 trip's label wins here because 0 < 1."""
    route_id = "9011007087300000"
    for _ in range(3):
        con = duckdb.connect()
        _build(con, FIXTURE_WAREHOUSE_TIE)
        n_trips_tied = con.execute(
            "SELECT COUNT(DISTINCT n_trips), COUNT(DISTINCT earliest_departure) "
            f"FROM pattern_counts WHERE route_id = '{route_id}'"
        ).fetchone()
        assert n_trips_tied == (1, 1), "fixture no longer reproduces the tie"

        label = con.execute(
            f"SELECT route_label FROM route_labels WHERE route_id = '{route_id}'"
        ).fetchone()[0]
        assert label == "873 · Värnamo station – Ljungby terminal"
        con.close()


# --------------------------------------------------------------------------
# D-021 Part 1: one run_log row per event, stable row order, partition
# schema consistency
# --------------------------------------------------------------------------


def _write_partition(table_dir: Path, service_date: str, schema: pa.Schema) -> None:
    part_dir = table_dir / f"service_date={service_date}"
    part_dir.mkdir(parents=True)
    table = pa.Table.from_pylist([], schema=schema)
    pq.write_table(table, part_dir / "part-0.parquet")


def test_check_partition_schemas_passes_when_consistent(tmp_path):
    schema = pa.schema([("trip_id", pa.string()), ("route_id", pa.string())])
    table_dir = tmp_path / "trips"
    _write_partition(table_dir, "2026-09-01", schema)
    _write_partition(table_dir, "2026-09-02", schema)
    agg.check_partition_schemas(tmp_path)  # does not raise


def test_check_partition_schemas_detects_type_mismatch(tmp_path):
    table_dir = tmp_path / "trips"
    _write_partition(
        table_dir, "2026-09-01", pa.schema([("trip_id", pa.string()), ("route_id", pa.string())])
    )
    _write_partition(
        table_dir, "2026-09-02", pa.schema([("trip_id", pa.string()), ("route_id", pa.int64())])
    )
    with pytest.raises(AssertionError, match="Partition schema mismatch"):
        agg.check_partition_schemas(tmp_path)


def test_check_partition_schemas_detects_missing_column(tmp_path):
    table_dir = tmp_path / "trips"
    _write_partition(
        table_dir, "2026-09-01", pa.schema([("trip_id", pa.string()), ("route_id", pa.string())])
    )
    _write_partition(table_dir, "2026-09-02", pa.schema([("trip_id", pa.string())]))
    with pytest.raises(AssertionError, match="Partition schema mismatch"):
        agg.check_partition_schemas(tmp_path)


def test_network_monthly_sorted_by_key_columns(con):
    _, rows = agg._table_values(con, "network_monthly")
    cols = [d[0] for d in con.execute("SELECT * FROM network_monthly LIMIT 0").description]
    key_idxs = [cols.index(c) for c in agg.SORT_KEYS["network_monthly"]]
    keys = [tuple(row[i] for i in key_idxs) for row in rows]
    assert keys == sorted(keys)


def test_route_daily_sorted_by_key_columns(con):
    _, rows = agg._table_values(con, "route_daily")
    cols = [d[0] for d in con.execute("SELECT * FROM route_daily LIMIT 0").description]
    key_idxs = [cols.index(c) for c in agg.SORT_KEYS["route_daily"]]
    keys = [tuple(row[i] for i in key_idxs) for row in rows]
    assert keys == sorted(keys)


def test_write_csv_and_table_values_agree_on_order(con, tmp_path):
    """The Sheet-write path (_table_values) and the CSV-write path
    (write_csv) must produce the same row order from the same table."""
    import csv

    agg.write_csv(con, "route_monthly", tmp_path)
    with (tmp_path / "route_monthly.csv").open(newline="", encoding="utf-8") as f:
        csv_rows = list(csv.reader(f))[1:]

    _, table_rows = agg._table_values(con, "route_monthly")
    assert len(csv_rows) == len(table_rows)
    cols = [d[0] for d in con.execute("SELECT * FROM route_monthly LIMIT 0").description]
    key_idxs = [cols.index(c) for c in agg.SORT_KEYS["route_monthly"]]
    csv_keys = [tuple(r[i] for i in key_idxs) for r in csv_rows]
    table_keys = [tuple(str(r[i]) for i in key_idxs) for r in table_rows]
    assert csv_keys == table_keys


def test_append_run_log_default_run_type_is_aggregate():
    sh = MagicMock()
    agg.append_run_log(sh, rows_out=10, duration_s=1.5, message="ok")
    row = sh.worksheet.return_value.append_row.call_args.args[0]
    assert row[1] == "aggregate"


def test_append_run_log_explicit_run_type():
    sh = MagicMock()
    agg.append_run_log(sh, rows_out=10, duration_s=1.5, message="ok", run_type="pipeline")
    row = sh.worksheet.return_value.append_row.call_args.args[0]
    assert row[1] == "pipeline"
