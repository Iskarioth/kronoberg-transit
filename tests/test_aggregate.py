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

import csv
import datetime as dt
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


def _build(
    con: duckdb.DuckDBPyConnection,
    warehouse_dir: Path,
    categories_path: Path = agg.CATEGORIES_PATH,
) -> None:
    con.execute(render_sql("aggregate_base.sql", warehouse_dir=warehouse_dir.as_posix()))
    agg.load_route_categories(con, categories_path)
    con.execute(render_sql("aggregate_network_monthly.sql"))
    con.execute(render_sql("aggregate_route_monthly.sql"))
    con.execute(render_sql("aggregate_route_daily.sql"))
    con.execute(render_sql("aggregate_station_monthly.sql"))
    con.execute(render_sql("aggregate_hour_monthly.sql"))
    con.execute(render_sql("aggregate_category_monthly.sql"))
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


# --------------------------------------------------------------------------
# Station coordinates (station_lat, station_lon in station_monthly)
# --------------------------------------------------------------------------
#
# A small synthetic warehouse, not the real fixture: station S1 has its own
# stops row with coordinates that differ between 2026-09-05 (Saturday) and
# 2026-09-10 (Thursday), both in month 2026-09. S1 is never itself a stop
# event; it is only reached through its child stop C1 (parent_station=S1,
# with its own, deliberately different coordinates, so a test that reads
# C1's coordinates instead of S1's would fail). Station S2 has no
# parent_station and a single date's coordinates.


def _write_table(table_dir: Path, service_date: str, schema: pa.Schema, rows: list[dict]) -> None:
    part_dir = table_dir / f"service_date={service_date}"
    part_dir.mkdir(parents=True)
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, part_dir / "part-0.parquet")


def _build_station_coords_warehouse(base_dir: Path) -> Path:
    trips_schema = pa.schema(
        [
            ("service_date", pa.date32()),
            ("route_id", pa.string()),
            ("trip_id", pa.string()),
            ("direction_id", pa.int64()),
            ("scheduled_first_departure_utc", pa.timestamp("us")),
        ]
    )
    stop_events_schema = pa.schema(
        [
            ("service_date", pa.date32()),
            ("trip_id", pa.string()),
            ("route_id", pa.string()),
            ("stop_sequence", pa.int64()),
            ("stop_id", pa.string()),
            ("stop_position", pa.string()),
            ("in_scope", pa.bool_()),
            ("is_timing_stop", pa.bool_()),
            ("status", pa.string()),
            ("delay_s", pa.int64()),
        ]
    )
    routes_schema = pa.schema(
        [
            ("service_date", pa.date32()),
            ("route_id", pa.string()),
            ("route_short_name", pa.string()),
        ]
    )
    stops_schema = pa.schema(
        [
            ("service_date", pa.date32()),
            ("stop_id", pa.string()),
            ("stop_name", pa.string()),
            ("stop_lat", pa.float64()),
            ("stop_lon", pa.float64()),
            ("parent_station", pa.string()),
        ]
    )
    empty_schema = pa.schema([("service_date", pa.date32())])

    d05, d10 = "2026-09-05", "2026-09-10"

    _write_table(
        base_dir / "trips",
        d05,
        trips_schema,
        [
            {
                "service_date": dt.date(2026, 9, 5),
                "route_id": "R1",
                "trip_id": "T1",
                "direction_id": 0,
                "scheduled_first_departure_utc": dt.datetime(2026, 9, 5, 10, 0, 0),  # noqa: DTZ001 - naive UTC, matching the warehouse's own timestamp columns
            }
        ],
    )
    _write_table(
        base_dir / "trips",
        d10,
        trips_schema,
        [
            {
                "service_date": dt.date(2026, 9, 10),
                "route_id": "R1",
                "trip_id": "T2",
                "direction_id": 0,
                "scheduled_first_departure_utc": dt.datetime(2026, 9, 10, 10, 0, 0),  # noqa: DTZ001 - naive UTC, matching the warehouse's own timestamp columns
            }
        ],
    )

    _write_table(
        base_dir / "stop_events",
        d05,
        stop_events_schema,
        [
            {
                "service_date": dt.date(2026, 9, 5),
                "trip_id": "T1",
                "route_id": "R1",
                "stop_sequence": 1,
                "stop_id": "C1",
                "stop_position": "first",
                "in_scope": True,
                "is_timing_stop": True,
                "status": "observed",
                "delay_s": 30,
            },
            {
                "service_date": dt.date(2026, 9, 5),
                "trip_id": "T1",
                "route_id": "R1",
                "stop_sequence": 2,
                "stop_id": "S2",
                "stop_position": "intermediate",
                "in_scope": True,
                "is_timing_stop": True,
                "status": "observed",
                "delay_s": -10,
            },
            {
                "service_date": dt.date(2026, 9, 5),
                "trip_id": "T1",
                "route_id": "R1",
                "stop_sequence": 3,
                "stop_id": "ZFIN",
                "stop_position": "final",
                "in_scope": True,
                "is_timing_stop": True,
                "status": None,
                "delay_s": None,
            },
        ],
    )
    _write_table(
        base_dir / "stop_events",
        d10,
        stop_events_schema,
        [
            {
                "service_date": dt.date(2026, 9, 10),
                "trip_id": "T2",
                "route_id": "R1",
                "stop_sequence": 1,
                "stop_id": "C1",
                "stop_position": "first",
                "in_scope": True,
                "is_timing_stop": True,
                "status": "observed",
                "delay_s": 50,
            },
            {
                "service_date": dt.date(2026, 9, 10),
                "trip_id": "T2",
                "route_id": "R1",
                "stop_sequence": 2,
                "stop_id": "ZFIN2",
                "stop_position": "final",
                "in_scope": True,
                "is_timing_stop": True,
                "status": None,
                "delay_s": None,
            },
        ],
    )

    _write_table(
        base_dir / "routes",
        d05,
        routes_schema,
        [{"service_date": dt.date(2026, 9, 5), "route_id": "R1", "route_short_name": "9"}],
    )
    _write_table(
        base_dir / "routes",
        d10,
        routes_schema,
        [{"service_date": dt.date(2026, 9, 10), "route_id": "R1", "route_short_name": "9"}],
    )

    _write_table(
        base_dir / "stops",
        d05,
        stops_schema,
        [
            {
                "service_date": dt.date(2026, 9, 5),
                "stop_id": "S1",
                "stop_name": "Station One",
                "stop_lat": 57.0,
                "stop_lon": 14.0,
                "parent_station": None,
            },
            {
                "service_date": dt.date(2026, 9, 5),
                "stop_id": "C1",
                "stop_name": "Child Stop",
                "stop_lat": 56.0,
                "stop_lon": 13.0,
                "parent_station": "S1",
            },
            {
                "service_date": dt.date(2026, 9, 5),
                "stop_id": "S2",
                "stop_name": "Solo Stop",
                "stop_lat": 56.5,
                "stop_lon": 15.0,
                "parent_station": None,
            },
        ],
    )
    _write_table(
        base_dir / "stops",
        d10,
        stops_schema,
        [
            {
                "service_date": dt.date(2026, 9, 10),
                "stop_id": "S1",
                "stop_name": "Station One",
                "stop_lat": 57.5,
                "stop_lon": 14.5,
                "parent_station": None,
            },
            {
                "service_date": dt.date(2026, 9, 10),
                "stop_id": "C1",
                "stop_name": "Child Stop",
                "stop_lat": 56.0,
                "stop_lon": 13.0,
                "parent_station": "S1",
            },
        ],
    )

    for feed_table in ("feed_quality", "feed_gaps"):
        _write_table(base_dir / feed_table, d05, empty_schema, [])
        _write_table(base_dir / feed_table, d10, empty_schema, [])

    return base_dir


def _build_base_and_station_monthly(con: duckdb.DuckDBPyConnection, warehouse_dir: Path) -> None:
    con.execute(render_sql("aggregate_base.sql", warehouse_dir=warehouse_dir.as_posix()))
    con.execute(render_sql("aggregate_station_monthly.sql"))


@pytest.fixture
def station_coords_con(tmp_path):
    warehouse_dir = _build_station_coords_warehouse(tmp_path)
    c = duckdb.connect()
    _build_base_and_station_monthly(c, warehouse_dir)
    yield c
    c.close()


def test_station_with_parent_gets_parent_station_coords(station_coords_con):
    """C1 (parent_station=S1) has its own coordinates (56.0, 13.0), distinct
    from S1's. Station S1's station_monthly rows must carry S1's own stops
    row values, never C1's."""
    rows = station_coords_con.execute(
        "SELECT DISTINCT station_lat, station_lon FROM station_monthly WHERE station_id = 'S1'"
    ).fetchall()
    assert rows == [(57.5, 14.5)]  # S1's own coordinates, and the later date wins (below)
    assert (56.0, 13.0) not in rows  # never C1's own coordinates


def test_station_without_parent_gets_its_own_coords(station_coords_con):
    """S2 has no parent_station, so it is its own station, with its own
    (single-date) coordinates."""
    rows = station_coords_con.execute(
        "SELECT DISTINCT station_lat, station_lon FROM station_monthly WHERE station_id = 'S2'"
    ).fetchall()
    assert rows == [(56.5, 15.0)]


def test_station_coords_use_the_later_dates_values(station_coords_con):
    """S1's own stops row has coordinates (57.0, 14.0) on 2026-09-05 and
    (57.5, 14.5) on 2026-09-10, both within 2026-09. The later date wins."""
    rows = station_coords_con.execute(
        "SELECT DISTINCT station_lat, station_lon FROM station_monthly WHERE station_id = 'S1'"
    ).fetchall()
    assert rows == [(57.5, 14.5)]


def test_station_coords_identical_across_day_type_and_stop_set(station_coords_con):
    """Every day_type/stop_set row of one station-month must carry the same
    coordinates: S1 has rows for day_type in ('all', 'weekday', 'saturday')
    and stop_set in ('all_stops', 'timing_stops'), all for month 2026-09."""
    rows = station_coords_con.execute(
        """SELECT day_type, stop_set, station_lat, station_lon
           FROM station_monthly WHERE station_id = 'S1' AND month = '2026-09'"""
    ).fetchall()
    assert len(rows) >= 2  # more than one day_type/stop_set combination is present
    distinct_coords = {(lat, lon) for _, _, lat, lon in rows}
    assert distinct_coords == {(57.5, 14.5)}


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


def test_append_run_log_default_status_is_ok():
    sh = MagicMock()
    agg.append_run_log(sh, rows_out=10, duration_s=1.5, message="ok")
    row = sh.worksheet.return_value.append_row.call_args.args[0]
    assert row[4] == "ok"


def test_append_run_log_explicit_warning_status():
    sh = MagicMock()
    agg.append_run_log(sh, rows_out=10, duration_s=1.5, message="unmapped", status="warning")
    row = sh.worksheet.return_value.append_row.call_args.args[0]
    assert row[4] == "warning"


# --------------------------------------------------------------------------
# D-022: route category mapping (config/route_categories.csv) and
# category_monthly
# --------------------------------------------------------------------------


def _write_category_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["route_id", "route_short_name", "category", "town", "source"]
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_load_route_categories_rejects_duplicate_route_id(tmp_path):
    path = tmp_path / "route_categories.csv"
    _write_category_csv(
        path,
        [
            {
                "route_id": "R1",
                "route_short_name": "1",
                "category": "Regional lines",
                "town": "",
                "source": "x",
            },
            {
                "route_id": "R1",
                "route_short_name": "1",
                "category": "Regional lines",
                "town": "",
                "source": "x",
            },
        ],
    )
    with pytest.raises(ValueError, match="duplicate route_id"):
        agg.load_route_categories(duckdb.connect(), path)


def test_load_route_categories_rejects_bad_category(tmp_path):
    path = tmp_path / "route_categories.csv"
    _write_category_csv(
        path,
        [
            {
                "route_id": "R1",
                "route_short_name": "1",
                "category": "Not a real category",
                "town": "",
                "source": "x",
            }
        ],
    )
    with pytest.raises(ValueError, match="expected one of"):
        agg.load_route_categories(duckdb.connect(), path)


def test_load_route_categories_rejects_missing_town(tmp_path):
    path = tmp_path / "route_categories.csv"
    _write_category_csv(
        path,
        [
            {
                "route_id": "R1",
                "route_short_name": "1",
                "category": "Växjö city lines",
                "town": "",
                "source": "x",
            }
        ],
    )
    with pytest.raises(ValueError, match="no town"):
        agg.load_route_categories(duckdb.connect(), path)


def test_load_route_categories_rejects_extra_town(tmp_path):
    path = tmp_path / "route_categories.csv"
    _write_category_csv(
        path,
        [
            {
                "route_id": "R1",
                "route_short_name": "1",
                "category": "Regional lines",
                "town": "Växjö",
                "source": "x",
            }
        ],
    )
    with pytest.raises(ValueError, match="only"):
        agg.load_route_categories(duckdb.connect(), path)


def test_load_route_categories_accepts_valid_mapping(tmp_path):
    path = tmp_path / "route_categories.csv"
    _write_category_csv(
        path,
        [
            {
                "route_id": "R1",
                "route_short_name": "1",
                "category": "Regional lines",
                "town": "",
                "source": "x",
            },
            {
                "route_id": "R2",
                "route_short_name": "2",
                "category": "Växjö city lines",
                "town": "Växjö",
                "source": "x",
            },
        ],
    )
    con = duckdb.connect()
    agg.load_route_categories(con, path)  # does not raise
    rows = con.execute(
        "SELECT route_id, category, town FROM route_categories ORDER BY route_id"
    ).fetchall()
    assert rows == [("R1", "Regional lines", ""), ("R2", "Växjö city lines", "Växjö")]


# Synthetic warehouse for the unmapped-route and category-pooling tests: one
# service date (2026-09-14, a Monday), three routes each with one trip -
# RA1 and RB1 mapped to the same category ("Regional lines"); RU1 left out of
# the mapping entirely. RA1 has 5 observed non-final departures at delay_s=10,
# RB1 has 1 at delay_s=1000: pooling all 6 gives median_delay_s=10
# (quantile_cont), whereas averaging the two routes' own medians (10 and
# 1000) would give 505, and summing them would give 1010 - either wrong
# answer would mean category_monthly is summing/averaging route_monthly
# instead of aggregating stop_events directly.
_CAT_SVC_DATE = dt.date(2026, 9, 14)


def _build_category_warehouse(base_dir: Path) -> Path:
    trips_schema = pa.schema(
        [
            ("service_date", pa.date32()),
            ("trip_id", pa.string()),
            ("route_id", pa.string()),
            ("direction_id", pa.int64()),
            ("scheduled_first_departure_utc", pa.timestamp("us")),
            ("scheduled_last_arrival_utc", pa.timestamp("us")),
            ("scheduled_stops", pa.int64()),
            ("operator", pa.string()),
            ("in_scope", pa.bool_()),
            ("trip_status", pa.string()),
            ("first_seen_utc", pa.timestamp("us")),
            ("last_seen_utc", pa.timestamp("us")),
            ("no_data_in_outage", pa.bool_()),
        ]
    )
    stop_events_schema = pa.schema(
        [
            ("service_date", pa.date32()),
            ("trip_id", pa.string()),
            ("route_id", pa.string()),
            ("stop_sequence", pa.int64()),
            ("stop_id", pa.string()),
            ("stop_position", pa.string()),
            ("scheduled_arrival_utc", pa.timestamp("us")),
            ("scheduled_departure_utc", pa.timestamp("us")),
            ("held_arrival_utc", pa.timestamp("us")),
            ("held_departure_utc", pa.timestamp("us")),
            ("arrival_marker", pa.bool_()),
            ("departure_marker", pa.bool_()),
            ("last_stop_relationship", pa.string()),
            ("last_seen_utc", pa.timestamp("us")),
            ("in_scope", pa.bool_()),
            ("is_timing_stop", pa.bool_()),
            ("status", pa.string()),
            ("delay_s", pa.int64()),
        ]
    )
    routes_schema = pa.schema(
        [
            ("service_date", pa.date32()),
            ("route_id", pa.string()),
            ("route_short_name", pa.int64()),
            ("route_long_name", pa.string()),
            ("route_type", pa.int64()),
        ]
    )
    stops_schema = pa.schema(
        [
            ("service_date", pa.date32()),
            ("stop_id", pa.string()),
            ("stop_name", pa.string()),
            ("stop_lat", pa.float64()),
            ("stop_lon", pa.float64()),
            ("parent_station", pa.string()),
        ]
    )
    feed_quality_schema = pa.schema(
        [
            ("service_date", pa.date32()),
            ("feed", pa.string()),
            ("archive_files", pa.int64()),
            ("distinct_snapshots", pa.int64()),
            ("duplicate_snapshots", pa.int64()),
            ("out_of_scope_trips_in_feed", pa.int64()),
            ("first_snapshot_utc", pa.timestamp("us")),
            ("last_snapshot_utc", pa.timestamp("us")),
            ("max_gap_s", pa.int64()),
            ("gaps_over_300s", pa.int64()),
            ("local_hours_without_snapshots", pa.string()),
            ("next_day_hours_read", pa.string()),
            ("dst_ambiguous_departures", pa.int64()),
            ("schedule_mismatch_stop_events", pa.int64()),
            ("schedule_mismatch_updates", pa.int64()),
            ("unmatched_realtime_trips", pa.int64()),
        ]
    )
    feed_gaps_schema = pa.schema(
        [
            ("service_date", pa.date32()),
            ("feed", pa.string()),
            ("gap_start_utc", pa.timestamp("us")),
            ("gap_end_utc", pa.timestamp("us")),
            ("gap_s", pa.int64()),
            ("kind", pa.string()),
        ]
    )

    d = _CAT_SVC_DATE
    d_str = d.isoformat()

    def trip_row(trip_id, route_id, scheduled_stops):
        return {
            "service_date": d,
            "trip_id": trip_id,
            "route_id": route_id,
            "direction_id": 0,
            "scheduled_first_departure_utc": dt.datetime(2026, 9, 14, 6, 0, 0),  # noqa: DTZ001
            "scheduled_last_arrival_utc": dt.datetime(2026, 9, 14, 7, 0, 0),  # noqa: DTZ001
            "scheduled_stops": scheduled_stops,
            "operator": None,
            "in_scope": True,
            "trip_status": "in_feed",
            "first_seen_utc": None,
            "last_seen_utc": None,
            "no_data_in_outage": None,
        }

    _write_table(
        base_dir / "trips",
        d_str,
        trips_schema,
        [
            trip_row("TA1", "RA1", 6),
            trip_row("TB1", "RB1", 2),
            trip_row("TU1", "RU1", 2),
        ],
    )

    def se_row(trip_id, route_id, seq, stop_id, position, status, delay_s):
        return {
            "service_date": d,
            "trip_id": trip_id,
            "route_id": route_id,
            "stop_sequence": seq,
            "stop_id": stop_id,
            "stop_position": position,
            "scheduled_arrival_utc": dt.datetime(2026, 9, 14, 6, 0, 0),  # noqa: DTZ001
            "scheduled_departure_utc": dt.datetime(2026, 9, 14, 6, 0, 0),  # noqa: DTZ001
            "held_arrival_utc": None,
            "held_departure_utc": None,
            "arrival_marker": None,
            "departure_marker": None,
            "last_stop_relationship": None,
            "last_seen_utc": None,
            "in_scope": True,
            "is_timing_stop": True,
            "status": status,
            "delay_s": delay_s,
        }

    se_rows = [
        se_row("TA1", "RA1", 1, "A_S1", "first", "observed", 10),
        se_row("TA1", "RA1", 2, "A_S2", "intermediate", "observed", 10),
        se_row("TA1", "RA1", 3, "A_S3", "intermediate", "observed", 10),
        se_row("TA1", "RA1", 4, "A_S4", "intermediate", "observed", 10),
        se_row("TA1", "RA1", 5, "A_S5", "intermediate", "observed", 10),
        se_row("TA1", "RA1", 6, "A_S6", "final", None, None),
        se_row("TB1", "RB1", 1, "B_S1", "first", "observed", 1000),
        se_row("TB1", "RB1", 2, "B_S2", "final", None, None),
        se_row("TU1", "RU1", 1, "U_S1", "first", "observed", 50),
        se_row("TU1", "RU1", 2, "U_S2", "final", None, None),
    ]
    _write_table(base_dir / "stop_events", d_str, stop_events_schema, se_rows)

    _write_table(
        base_dir / "routes",
        d_str,
        routes_schema,
        [
            {
                "service_date": d,
                "route_id": rid,
                "route_short_name": sn,
                "route_long_name": None,
                "route_type": 700,
            }
            for rid, sn in [("RA1", 1001), ("RB1", 1002), ("RU1", 1003)]
        ],
    )

    _write_table(
        base_dir / "stops",
        d_str,
        stops_schema,
        [
            {
                "service_date": d,
                "stop_id": sid,
                "stop_name": sid,
                "stop_lat": 56.0,
                "stop_lon": 14.0,
                "parent_station": None,
            }
            for sid in [
                "A_S1",
                "A_S2",
                "A_S3",
                "A_S4",
                "A_S5",
                "A_S6",
                "B_S1",
                "B_S2",
                "U_S1",
                "U_S2",
            ]
        ],
    )

    _write_table(
        base_dir / "feed_quality",
        d_str,
        feed_quality_schema,
        [
            {
                "service_date": d,
                "feed": "TripUpdates",
                "archive_files": 24,
                "distinct_snapshots": 24,
                "duplicate_snapshots": 0,
                "out_of_scope_trips_in_feed": 0,
                "first_snapshot_utc": dt.datetime(2026, 9, 14, 0, 0, 0),  # noqa: DTZ001
                "last_snapshot_utc": dt.datetime(2026, 9, 14, 23, 59, 0),  # noqa: DTZ001
                "max_gap_s": 0,
                "gaps_over_300s": 0,
                "local_hours_without_snapshots": "",
                "next_day_hours_read": "",
                "dst_ambiguous_departures": 0,
                "schedule_mismatch_stop_events": 0,
                "schedule_mismatch_updates": 0,
                "unmatched_realtime_trips": 0,
            }
        ],
    )

    _write_table(
        base_dir / "feed_gaps",
        d_str,
        feed_gaps_schema,
        [
            {
                "service_date": d,
                "feed": "TripUpdates",
                "gap_start_utc": dt.datetime(2026, 9, 14, 0, 0, 0),  # noqa: DTZ001
                "gap_end_utc": dt.datetime(2026, 9, 14, 0, 6, 0),  # noqa: DTZ001
                "gap_s": 360,
                "kind": "before_first",
            }
        ],
    )

    return base_dir


@pytest.fixture
def category_warehouse(tmp_path):
    return _build_category_warehouse(tmp_path / "warehouse")


@pytest.fixture
def category_con(category_warehouse, tmp_path):
    categories_path = tmp_path / "route_categories.csv"
    _write_category_csv(
        categories_path,
        [
            {
                "route_id": "RA1",
                "route_short_name": "1001",
                "category": "Regional lines",
                "town": "",
                "source": "test",
            },
            {
                "route_id": "RB1",
                "route_short_name": "1002",
                "category": "Regional lines",
                "town": "",
                "source": "test",
            },
        ],
    )
    c = duckdb.connect()
    _build(c, category_warehouse, categories_path)
    yield c
    c.close()


def test_unmapped_route_gets_unmapped_category(category_con):
    row = category_con.execute(
        "SELECT route_category, route_town FROM route_daily WHERE route_id = 'RU1'"
    ).fetchone()
    assert row == ("Unmapped", "")


def test_mapped_route_gets_its_category_and_town(category_con):
    row = category_con.execute(
        "SELECT route_category, route_town FROM route_daily WHERE route_id = 'RA1'"
    ).fetchone()
    assert row == ("Regional lines", "")


def test_route_category_and_town_are_last_two_columns(category_con):
    cols = [d[0] for d in category_con.execute("SELECT * FROM route_daily LIMIT 0").description]
    assert cols[-2:] == ["route_category", "route_town"]
    cols = [d[0] for d in category_con.execute("SELECT * FROM route_monthly LIMIT 0").description]
    assert cols[-2:] == ["route_category", "route_town"]


def test_unmapped_route_counted_in_data_quality(category_con):
    row = category_con.execute(
        f"SELECT unmapped_route_trips FROM data_quality WHERE service_date = '{_CAT_SVC_DATE}'"
    ).fetchone()
    assert row == (1,)  # RU1's one in-scope trip


def test_check_unmapped_routes_reports_route_id(category_con):
    any_unmapped, message = agg.check_unmapped_routes(category_con)
    assert any_unmapped is True
    assert "RU1" in message


def test_check_unmapped_routes_false_when_everything_mapped(con):
    # The module-level `con` fixture builds from FIXTURE_WAREHOUSE with the
    # real config/route_categories.csv, which covers every route it contains.
    any_unmapped, message = agg.check_unmapped_routes(con)
    assert any_unmapped is False
    assert message == ""


def test_category_monthly_median_computed_from_pooled_stop_events(category_con):
    """RA1 (5 departures at delay_s=10) and RB1 (1 at delay_s=1000) are both
    'Regional lines'. The pooled median over all 6 stop events is 10, not the
    505 an average-of-route-medians would give, nor the 1010 a sum would
    give - proving category_monthly aggregates stop_events directly rather
    than combining route_monthly's per-route medians."""
    row = category_con.execute(
        """SELECT median_delay_s FROM category_monthly
           WHERE service_dates = 1 AND route_category = 'Regional lines'
             AND stop_set = 'all_stops' AND day_type = 'weekday'"""
    ).fetchone()
    assert row == (10.0,)


def test_category_monthly_eligible_departures_sum_to_network_monthly(category_con):
    key = "month = '2026-09' AND day_type = 'weekday' AND stop_set = 'all_stops'"
    network_total = category_con.execute(
        f"SELECT eligible_departures FROM network_monthly WHERE {key}"
    ).fetchone()[0]
    category_total = category_con.execute(
        f"SELECT SUM(eligible_departures) FROM category_monthly WHERE {key}"
    ).fetchone()[0]
    assert network_total == 7  # RA1's 5 + RB1's 1 + RU1's 1
    assert category_total == network_total

    per_category = dict(
        category_con.execute(
            f"SELECT route_category, eligible_departures FROM category_monthly WHERE {key}"
        ).fetchall()
    )
    assert per_category == {"Regional lines": 6, "Unmapped": 1}


def test_category_monthly_consistency_checks_pass(category_con):
    agg.run_consistency_checks(category_con)  # does not raise, includes D-022's own checks
