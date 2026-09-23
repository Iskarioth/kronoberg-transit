#!/usr/bin/env python3
"""aggregate.py - build the Google Sheets publishing layer from the full
Parquet warehouse (D-017).

Rebuilds every tab from data/warehouse/<table>/service_date=*/part-0.parquet.
--dry-run writes each tab as CSV to data/publish/ instead of touching the
Sheet. A real run clears and rewrites each tab, then appends one run_log row.

Usage:
    uv run --env-file .env python -m kronoberg_transit.aggregate
    uv run --env-file .env python -m kronoberg_transit.aggregate --dry-run
"""

import argparse
import csv
import datetime as dt
import os
import sys
import time
from pathlib import Path

import duckdb
import gspread

from kronoberg_transit.transform import TABLES as WAREHOUSE_TABLES
from kronoberg_transit.transform import render_sql

WAREHOUSE_DIR = Path("data/warehouse")
PUBLISH_DIR = Path("data/publish")
CATEGORIES_PATH = Path("config/route_categories.csv")

# Route category (D-022): the four allowed values, and the two that carry a
# town. A route missing from the mapping gets category 'Unmapped' at
# aggregation time - that value is never itself in the mapping file.
ROUTE_CATEGORIES = ["Växjö city lines", "Other town lines", "Regional lines", "School routes"]
TOWN_CATEGORIES = ["Växjö city lines", "Other town lines"]

# Sort keys (D-021 Part 1): every tab is written in this order, so the same
# warehouse always produces the same row order regardless of glob/partition
# read order.
SORT_KEYS = {
    "network_monthly": ["month", "day_type", "stop_set"],
    "route_monthly": ["month", "day_type", "stop_set", "route_id"],
    "route_daily": ["service_date", "day_type", "stop_set", "route_id"],
    "station_monthly": ["month", "day_type", "stop_set", "station_id"],
    "hour_monthly": ["month", "day_type", "stop_set", "hour_local"],
    "category_monthly": ["month", "day_type", "stop_set", "route_category"],
    "data_quality": ["service_date"],
}

TABS = [
    "network_monthly",
    "route_monthly",
    "route_daily",
    "station_monthly",
    "hour_monthly",
    "category_monthly",
    "data_quality",
]

# Replaced by station_monthly and hour_monthly (D-017); both confirmed
# header-only before this change.
RETIRED_TABS = ["stop_hotspots", "hour_of_day"]

MONTHLY_TABS = [
    "network_monthly",
    "route_monthly",
    "station_monthly",
    "hour_monthly",
    "category_monthly",
]

# Count columns shared by every T+M tab (subset present varies by tab; only
# columns that exist are checked).
COUNT_COLUMNS = [
    "scheduled_trips",
    "in_scope_trips",
    "out_of_scope_trips",
    "cancelled_trips",
    "trips_no_realtime_data",
    "trips_no_realtime_data_in_outage",
    "skipped_departures",
    "dst_ambiguous_departures",
    "eligible_departures",
    "observed_departures",
    "unobserved_departures",
    "observed_trips",
    "early_departures",
    "on_time_departures",
    "late_departures",
    "on_time_60_departures",
    "on_time_300_departures",
]

# Stop-set dimension (D-019): every tab below carries a stop_set column
# (all_stops | timing_stops). Trip-level columns are identical across stop
# sets by construction (the trip block never filters on is_timing_stop);
# only measure-block columns are restricted per stop_set.
STOP_SET_TABS = [
    "network_monthly",
    "route_monthly",
    "route_daily",
    "station_monthly",
    "hour_monthly",
    "category_monthly",
]

TRIP_LEVEL_COLUMNS = [
    "scheduled_trips",
    "in_scope_trips",
    "out_of_scope_trips",
    "cancelled_trips",
    "trips_no_realtime_data",
    "trips_no_realtime_data_in_outage",
    "cancellation_share",
    "no_realtime_data_share",
    "in_scope_share",
]

STOP_SET_KEY_COLUMNS = {
    "network_monthly": ["month", "day_type"],
    "route_monthly": ["month", "day_type", "route_id"],
    "route_daily": ["service_date", "route_id"],
    "station_monthly": ["month", "day_type", "station_id"],
    "hour_monthly": ["month", "day_type", "hour_local"],
    "category_monthly": ["month", "day_type", "route_category"],
}


def load_route_categories(con: duckdb.DuckDBPyConnection, path: Path = CATEGORIES_PATH) -> None:
    """Loads config/route_categories.csv (D-022) into the route_categories
    table, route_id read as text. Validates: unique route_id, category one of
    ROUTE_CATEGORIES, town non-empty for the two town-line categories and
    empty otherwise. Raises ValueError with every problem found, rather than
    stopping at the first one, so a bad mapping file can be fixed in one
    pass."""
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    problems = []
    seen_route_ids: set[str] = set()
    for i, row in enumerate(rows, start=2):  # +1 header, +1 to be 1-indexed
        route_id = row["route_id"]
        category = row["category"]
        town = row["town"]
        if route_id in seen_route_ids:
            problems.append(f"line {i}: duplicate route_id {route_id!r}")
        seen_route_ids.add(route_id)
        if category not in ROUTE_CATEGORIES:
            problems.append(
                f"line {i}: route_id {route_id!r} has category {category!r}, "
                f"expected one of {ROUTE_CATEGORIES}"
            )
            continue
        if category in TOWN_CATEGORIES and not town.strip():
            problems.append(
                f"line {i}: route_id {route_id!r} has category {category!r} but no town"
            )
        if category not in TOWN_CATEGORIES and town.strip():
            problems.append(
                f"line {i}: route_id {route_id!r} has category {category!r} "
                f"but a town ({town!r}); only {TOWN_CATEGORIES} carry a town"
            )
    if problems:
        raise ValueError(f"{path}: invalid route category mapping:\n" + "\n".join(problems))

    con.execute("""
        CREATE OR REPLACE TABLE route_categories (
            route_id VARCHAR, route_short_name VARCHAR, category VARCHAR,
            town VARCHAR, source VARCHAR
        )
    """)
    con.executemany(
        "INSERT INTO route_categories VALUES (?, ?, ?, ?, ?)",
        [
            (r["route_id"], r["route_short_name"], r["category"], r["town"], r["source"])
            for r in rows
        ],
    )


def check_partition_schemas(warehouse_dir: Path) -> None:
    """Every service_date partition of a warehouse table must share the same
    column names and types (D-021 Part 1). A silent schema drift between
    partitions (e.g. a column added or retyped mid-backfill) would otherwise
    only surface as a confusing downstream query error, or worse, a silent
    implicit cast."""
    import pyarrow.parquet as pq

    problems = []
    for table in WAREHOUSE_TABLES:
        table_dir = warehouse_dir / table
        if not table_dir.exists():
            continue
        partitions = sorted(table_dir.glob("service_date=*/part-0.parquet"))
        if not partitions:
            continue
        ref_path = partitions[0]
        ref_schema = {f.name: str(f.type) for f in pq.ParquetFile(ref_path).schema_arrow}
        for p in partitions[1:]:
            schema = {f.name: str(f.type) for f in pq.ParquetFile(p).schema_arrow}
            if schema != ref_schema:
                diff = {
                    k: (ref_schema.get(k), schema.get(k))
                    for k in set(ref_schema) | set(schema)
                    if ref_schema.get(k) != schema.get(k)
                }
                problems.append(f"{table} {p.parent.name} vs {ref_path.parent.name}: {diff}")
    if problems:
        raise AssertionError("Partition schema mismatch:\n" + "\n".join(problems))


def build_tables(
    con: duckdb.DuckDBPyConnection,
    warehouse_dir: Path = WAREHOUSE_DIR,
    categories_path: Path = CATEGORIES_PATH,
) -> None:
    check_partition_schemas(warehouse_dir)
    con.execute(render_sql("aggregate_base.sql", warehouse_dir=warehouse_dir.as_posix()))
    load_route_categories(con, categories_path)
    con.execute(render_sql("aggregate_network_monthly.sql"))
    con.execute(render_sql("aggregate_route_monthly.sql"))
    con.execute(render_sql("aggregate_route_daily.sql"))
    con.execute(render_sql("aggregate_station_monthly.sql"))
    con.execute(render_sql("aggregate_hour_monthly.sql"))
    con.execute(render_sql("aggregate_category_monthly.sql"))
    con.execute(render_sql("aggregate_data_quality.sql"))


def _columns(con: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    return {d[0] for d in con.execute(f"SELECT * FROM {table} LIMIT 0").description}


def run_consistency_checks(con: duckdb.DuckDBPyConnection) -> None:
    checks: list[tuple[str, bool]] = []

    # For each month, day type and stop_set, summing eligible/observed across
    # route_monthly, station_monthly and hour_monthly must equal
    # network_monthly for the same stop_set.
    for tab in ["route_monthly", "station_monthly", "hour_monthly"]:
        for col in ["eligible_departures", "observed_departures"]:
            n_bad = con.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT nm.month, nm.day_type, nm.stop_set
                    FROM network_monthly nm
                    JOIN (
                        SELECT month, day_type, stop_set, SUM({col}) AS s FROM {tab} GROUP BY 1, 2, 3
                    ) t ON t.month = nm.month AND t.day_type = nm.day_type AND t.stop_set = nm.stop_set
                    WHERE t.s != nm.{col}
                )
            """).fetchone()[0]
            checks.append(
                (f"sum({col}) over {tab} == network_monthly, within stop_set", n_bad == 0)
            )

    # Summing route_daily over a month's dates gives route_monthly with
    # day_type='all', for every count column, within the same stop_set.
    rd_cols = _columns(con, "route_daily") & set(COUNT_COLUMNS)
    for col in sorted(rd_cols):
        n_bad = con.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT rm.month, rm.route_id, rm.stop_set
                FROM route_monthly rm
                JOIN (
                    SELECT route_id, stop_set, strftime(service_date, '%Y-%m') AS month, SUM({col}) AS s
                    FROM route_daily GROUP BY 1, 2, 3
                ) t ON t.route_id = rm.route_id AND t.month = rm.month AND t.stop_set = rm.stop_set
                WHERE rm.day_type = 'all' AND t.s != rm.{col}
            )
        """).fetchone()[0]
        checks.append(
            (
                f"sum(route_daily.{col}) over a month == route_monthly(all).{col}, within stop_set",
                n_bad == 0,
            )
        )

    # day_type='all' equals the sum of weekday+saturday+sunday, for every
    # count column, on every monthly tab, within the same stop_set.
    for tab in MONTHLY_TABS:
        cols = _columns(con, tab) & set(COUNT_COLUMNS)
        group_cols = {
            "network_monthly": ["month", "stop_set"],
            "route_monthly": ["month", "stop_set", "route_id"],
            "station_monthly": ["month", "stop_set", "station_id"],
            "hour_monthly": ["month", "stop_set", "hour_local"],
            "category_monthly": ["month", "stop_set", "route_category"],
        }[tab]
        gc = ", ".join(group_cols)
        for col in sorted(cols):
            n_bad = con.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT {gc}, {col} AS all_val,
                        (SELECT SUM({col}) FROM {tab} t2
                         WHERE t2.day_type IN ('weekday', 'saturday', 'sunday')
                         AND ({" AND ".join(f"t2.{c} = t1.{c}" for c in group_cols)})
                        ) AS parts_sum
                    FROM {tab} t1
                    WHERE t1.day_type = 'all'
                ) WHERE all_val != COALESCE(parts_sum, 0)
            """).fetchone()[0]
            checks.append(
                (f"{tab}.{col}: all == weekday+saturday+sunday, within stop_set", n_bad == 0)
            )

    # D-019: timing_stops must never exceed all_stops for the same keys, on
    # every measure-block count column of every stop-set-bearing tab.
    for tab in STOP_SET_TABS:
        key_cols = STOP_SET_KEY_COLUMNS[tab]
        cols = _columns(con, tab) & set(COUNT_COLUMNS)
        kc = ", ".join(key_cols)
        for col in sorted(cols):
            n_bad = con.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT a.{key_cols[0]}
                    FROM (SELECT {kc}, {col} FROM {tab} WHERE stop_set = 'all_stops') a
                    JOIN (SELECT {kc}, {col} FROM {tab} WHERE stop_set = 'timing_stops') t
                        ON ({" AND ".join(f"a.{c} = t.{c}" for c in key_cols)})
                    WHERE t.{col} > a.{col}
                )
            """).fetchone()[0]
            checks.append((f"{tab}.{col}: timing_stops <= all_stops", n_bad == 0))

    # D-019: trip-level columns must be identical across stop sets for the
    # same keys, on every tab that carries them.
    for tab in ["network_monthly", "route_monthly", "route_daily", "category_monthly"]:
        key_cols = STOP_SET_KEY_COLUMNS[tab]
        cols = _columns(con, tab) & set(TRIP_LEVEL_COLUMNS)
        kc = ", ".join(key_cols)
        for col in sorted(cols):
            n_bad = con.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT a.{key_cols[0]}
                    FROM (SELECT {kc}, {col} FROM {tab} WHERE stop_set = 'all_stops') a
                    JOIN (SELECT {kc}, {col} FROM {tab} WHERE stop_set = 'timing_stops') t
                        ON ({" AND ".join(f"a.{c} = t.{c}" for c in key_cols)})
                    WHERE a.{col} IS DISTINCT FROM t.{col}
                )
            """).fetchone()[0]
            checks.append((f"{tab}.{col}: identical across stop sets", n_bad == 0))

    # D-022: category_monthly must sum exactly to network_monthly, for every
    # count column, within the same stop_set - computed directly from stop
    # events per category, not by summing route_monthly rows, so this also
    # guards against a category/route join that silently drops or duplicates
    # trips.
    cm_cols = _columns(con, "category_monthly") & set(COUNT_COLUMNS)
    for col in sorted(cm_cols):
        n_bad = con.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT nm.month, nm.day_type, nm.stop_set
                FROM network_monthly nm
                JOIN (
                    SELECT month, day_type, stop_set, SUM({col}) AS s FROM category_monthly GROUP BY 1, 2, 3
                ) t ON t.month = nm.month AND t.day_type = nm.day_type AND t.stop_set = nm.stop_set
                WHERE t.s != nm.{col}
            )
        """).fetchone()[0]
        checks.append(
            (f"sum(category_monthly.{col}) == network_monthly.{col}, within stop_set", n_bad == 0)
        )

    failed = [name for name, ok in checks if not ok]
    if failed:
        raise AssertionError(f"Consistency checks failed: {failed}")
    print(f"  All {len(checks)} consistency checks passed.")


def check_unmapped_routes(con: duckdb.DuckDBPyConnection) -> tuple[bool, str]:
    """D-022 tripwire: whether any service date has in-scope trips on a
    route_id missing from config/route_categories.csv. Returns
    (any_unmapped, message); message is empty when any_unmapped is False."""
    by_date = con.execute("""
        SELECT service_date, unmapped_route_trips
        FROM data_quality
        WHERE unmapped_route_trips > 0
        ORDER BY service_date
    """).fetchall()
    if not by_date:
        return False, ""
    route_ids = con.execute("""
        SELECT DISTINCT t.route_id
        FROM all_trips t
        LEFT JOIN route_categories rc ON rc.route_id = t.route_id
        WHERE t.in_scope AND rc.route_id IS NULL
        ORDER BY 1
    """).fetchall()
    ids = ", ".join(r[0] for r in route_ids)
    dates = "; ".join(f"{d.isoformat()}={n}" for d, n in by_date)
    message = f"unmapped_route_trips>0 (D-022): route_id(s) {ids}; by date: {dates}"
    return True, message


def get_sheet() -> gspread.Spreadsheet:
    key = os.environ.get("GOOGLE_SHEET_ID")
    sa_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "secrets/google_service_account.json")
    if not key:
        sys.exit("Set GOOGLE_SHEET_ID in your environment first.")
    gc = gspread.service_account(filename=sa_file)
    return gc.open_by_key(key)


def _order_by_clause(table: str) -> str:
    keys = SORT_KEYS.get(table)
    return f" ORDER BY {', '.join(keys)}" if keys else ""


def write_csv(con: duckdb.DuckDBPyConnection, table: str, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{table}.csv"
    order_by = _order_by_clause(table)
    con.execute(
        f"COPY (SELECT * FROM {table}{order_by}) TO '{out_path.as_posix()}' (HEADER, DELIMITER ',')"
    )
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# Sheets reinterprets values under USER_ENTERED: a 16-digit ID like route_id
# becomes a rounded float in scientific notation, and a comma-joined list
# like "144,145,146" becomes one number with thousands separators. Every tab
# is written RAW instead, with each column's Python type chosen by its
# DuckDB type: BOOLEAN -> bool, VARCHAR/DATE/TIMESTAMP -> str (IDs, labels,
# lists, dates and timestamps are all text), everything else (counts,
# shares) -> the native number DuckDB already returns.
def _column_kinds(con: duckdb.DuckDBPyConnection, table: str) -> dict[str, str]:
    kinds = {}
    for name, col_type, *_ in con.execute(f"DESCRIBE {table}").fetchall():
        if col_type == "BOOLEAN":
            kinds[name] = "bool"
        elif col_type in ("VARCHAR", "DATE", "TIMESTAMP"):
            kinds[name] = "text"
        else:
            kinds[name] = "number"
    return kinds


def _cell(v, kind: str):
    if v is None:
        return ""
    if kind == "bool":
        return bool(v)
    if kind == "text":
        if isinstance(v, (dt.date, dt.datetime)):
            return v.isoformat()
        return str(v)
    return v


def _table_values(con: duckdb.DuckDBPyConnection, table: str) -> tuple[list[str], list[list]]:
    kinds = _column_kinds(con, table)
    cols = [d[0] for d in con.execute(f"SELECT * FROM {table} LIMIT 0").description]
    order_by = _order_by_clause(table)
    rows = con.execute(f"SELECT * FROM {table}{order_by}").fetchall()
    values = [[_cell(v, kinds[c]) for v, c in zip(row, cols, strict=True)] for row in rows]
    return cols, values


def write_sheet_tab(sh: gspread.Spreadsheet, con: duckdb.DuckDBPyConnection, table: str) -> int:
    cols, rows = _table_values(con, table)
    values = [cols] + rows

    need_rows, need_cols = max(len(values), 2), max(len(cols), 1)
    existing = {ws.title: ws for ws in sh.worksheets()}
    if table in existing:
        ws = existing[table]
        ws.clear()
        if ws.row_count < need_rows or ws.col_count < need_cols:
            ws.resize(rows=max(ws.row_count, need_rows), cols=max(ws.col_count, need_cols))
    else:
        ws = sh.add_worksheet(title=table, rows=need_rows, cols=need_cols)
    ws.update(values=values, range_name="A1", value_input_option="RAW")
    ws.freeze(rows=1)
    return len(rows)


def verify_sheet_tab(sh: gspread.Spreadsheet, con: duckdb.DuckDBPyConnection, table: str) -> None:
    """Read the tab back and compare row count and every text/ID column
    exactly against what was just written. Raises on any difference."""
    kinds = _column_kinds(con, table)
    cols, rows = _table_values(con, table)
    text_idxs = [i for i, c in enumerate(cols) if kinds[c] == "text"]

    ws = sh.worksheet(table)
    read_back = ws.get_all_values()

    if len(read_back) != len(rows) + 1:
        raise AssertionError(
            f"{table}: read-back row count {len(read_back) - 1} != written {len(rows)}"
        )
    if read_back[0] != cols:
        raise AssertionError(f"{table}: read-back header {read_back[0]} != written {cols}")

    for i, (written_row, read_row) in enumerate(zip(rows, read_back[1:], strict=True)):
        expected = [str(written_row[j]) for j in text_idxs]
        actual = [read_row[j] for j in text_idxs]
        if expected != actual:
            raise AssertionError(
                f"{table}: row {i} text/ID mismatch: wrote {expected}, read back {actual}"
            )


def delete_retired_tabs(sh: gspread.Spreadsheet) -> list[str]:
    existing = {ws.title: ws for ws in sh.worksheets()}
    deleted = []
    for name in RETIRED_TABS:
        if name in existing:
            sh.del_worksheet(existing[name])
            deleted.append(name)
    return deleted


def append_run_log(
    sh: gspread.Spreadsheet,
    rows_out: int,
    duration_s: float,
    message: str,
    run_type: str = "aggregate",
    status: str = "ok",
) -> None:
    ws = sh.worksheet("run_log")
    now = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = [now, run_type, "", "aggregate", status, "", rows_out, round(duration_s, 3), message]
    ws.append_row(row, value_input_option="RAW")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Write CSVs to data/publish/ instead of the Sheet"
    )
    parser.add_argument(
        "--warehouse-dir",
        default=str(WAREHOUSE_DIR),
        help=f"Warehouse input directory (default: {WAREHOUSE_DIR})",
    )
    args = parser.parse_args()

    t0 = time.monotonic()
    con = duckdb.connect()
    print("Building publishing tables from the warehouse...")
    build_tables(con, warehouse_dir=Path(args.warehouse_dir))
    run_consistency_checks(con)

    any_unmapped, unmapped_message = check_unmapped_routes(con)
    if any_unmapped:
        print(f"  WARNING: {unmapped_message}")
    else:
        print("  No unmapped-route trips (D-022).")

    if args.dry_run:
        counts = {t: write_csv(con, t, PUBLISH_DIR) for t in TABS}
        duration = time.monotonic() - t0
        print(
            f"Dry run: wrote {PUBLISH_DIR}/*.csv ({counts}), Sheet not touched. ({duration:.1f}s)"
        )
        return 0

    sh = get_sheet()
    counts = {}
    for t in TABS:
        counts[t] = write_sheet_tab(sh, con, t)
        verify_sheet_tab(sh, con, t)
    print(f"  Read-back check passed for all {len(TABS)} tabs.")
    deleted = delete_retired_tabs(sh)
    duration = time.monotonic() - t0
    message = f"tabs rebuilt: {counts}; retired tabs deleted: {deleted}"
    status = "ok"
    if any_unmapped:
        status = "warning"
        message += f"; {unmapped_message}"
    append_run_log(sh, sum(counts.values()), duration, message, status=status)
    print(f"Rebuilt {len(TABS)} tabs in '{sh.title}': {counts}")
    if deleted:
        print(f"Deleted retired tabs: {deleted}")
    print(f"Done in {duration:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
