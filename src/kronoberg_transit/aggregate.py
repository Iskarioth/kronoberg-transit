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
import datetime as dt
import os
import sys
import time
from pathlib import Path

import duckdb
import gspread

from kronoberg_transit.transform import render_sql

WAREHOUSE_DIR = Path("data/warehouse")
PUBLISH_DIR = Path("data/publish")

TABS = [
    "network_monthly",
    "route_monthly",
    "route_daily",
    "station_monthly",
    "hour_monthly",
    "data_quality",
]

# Replaced by station_monthly and hour_monthly (D-017); both confirmed
# header-only before this change.
RETIRED_TABS = ["stop_hotspots", "hour_of_day"]

MONTHLY_TABS = ["network_monthly", "route_monthly", "station_monthly", "hour_monthly"]

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


def build_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(render_sql("aggregate_base.sql", warehouse_dir=WAREHOUSE_DIR.as_posix()))
    con.execute(render_sql("aggregate_network_monthly.sql"))
    con.execute(render_sql("aggregate_route_monthly.sql"))
    con.execute(render_sql("aggregate_route_daily.sql"))
    con.execute(render_sql("aggregate_station_monthly.sql"))
    con.execute(render_sql("aggregate_hour_monthly.sql"))
    con.execute(render_sql("aggregate_data_quality.sql"))


def _columns(con: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    return {d[0] for d in con.execute(f"SELECT * FROM {table} LIMIT 0").description}


def run_consistency_checks(con: duckdb.DuckDBPyConnection) -> None:
    checks: list[tuple[str, bool]] = []

    # For each month and day type, summing eligible/observed across
    # route_monthly, station_monthly and hour_monthly must equal
    # network_monthly.
    for tab in ["route_monthly", "station_monthly", "hour_monthly"]:
        for col in ["eligible_departures", "observed_departures"]:
            n_bad = con.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT nm.month, nm.day_type
                    FROM network_monthly nm
                    JOIN (
                        SELECT month, day_type, SUM({col}) AS s FROM {tab} GROUP BY 1, 2
                    ) t ON t.month = nm.month AND t.day_type = nm.day_type
                    WHERE t.s != nm.{col}
                )
            """).fetchone()[0]
            checks.append((f"sum({col}) over {tab} == network_monthly", n_bad == 0))

    # Summing route_daily over a month's dates gives route_monthly with
    # day_type='all', for every count column.
    rd_cols = _columns(con, "route_daily") & set(COUNT_COLUMNS)
    for col in sorted(rd_cols):
        n_bad = con.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT rm.month, rm.route_id
                FROM route_monthly rm
                JOIN (
                    SELECT route_id, strftime(service_date, '%Y-%m') AS month, SUM({col}) AS s
                    FROM route_daily GROUP BY 1, 2
                ) t ON t.route_id = rm.route_id AND t.month = rm.month
                WHERE rm.day_type = 'all' AND t.s != rm.{col}
            )
        """).fetchone()[0]
        checks.append(
            (f"sum(route_daily.{col}) over a month == route_monthly(all).{col}", n_bad == 0)
        )

    # day_type='all' equals the sum of weekday+saturday+sunday, for every
    # count column, on every monthly tab.
    for tab in MONTHLY_TABS:
        cols = _columns(con, tab) & set(COUNT_COLUMNS)
        group_cols = {
            "network_monthly": ["month"],
            "route_monthly": ["month", "route_id"],
            "station_monthly": ["month", "station_id"],
            "hour_monthly": ["month", "hour_local"],
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
            checks.append((f"{tab}.{col}: all == weekday+saturday+sunday", n_bad == 0))

    failed = [name for name, ok in checks if not ok]
    if failed:
        raise AssertionError(f"Consistency checks failed: {failed}")
    print(f"  All {len(checks)} consistency checks passed.")


def get_sheet() -> gspread.Spreadsheet:
    key = os.environ.get("GOOGLE_SHEET_ID")
    sa_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "secrets/google_service_account.json")
    if not key:
        sys.exit("Set GOOGLE_SHEET_ID in your environment first.")
    gc = gspread.service_account(filename=sa_file)
    return gc.open_by_key(key)


def write_csv(con: duckdb.DuckDBPyConnection, table: str, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{table}.csv"
    con.execute(f"COPY (SELECT * FROM {table}) TO '{out_path.as_posix()}' (HEADER, DELIMITER ',')")
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
    rows = con.execute(f"SELECT * FROM {table}").fetchall()
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


def append_run_log(sh: gspread.Spreadsheet, rows_out: int, duration_s: float, message: str) -> None:
    ws = sh.worksheet("run_log")
    now = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = [now, "aggregate", "", "aggregate", "ok", "", rows_out, round(duration_s, 3), message]
    ws.append_row(row, value_input_option="RAW")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Write CSVs to data/publish/ instead of the Sheet"
    )
    args = parser.parse_args()

    t0 = time.monotonic()
    con = duckdb.connect()
    print("Building publishing tables from the warehouse...")
    build_tables(con)
    run_consistency_checks(con)

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
    append_run_log(sh, sum(counts.values()), duration, message)
    print(f"Rebuilt {len(TABS)} tabs in '{sh.title}': {counts}")
    if deleted:
        print(f"Deleted retired tabs: {deleted}")
    print(f"Done in {duration:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
