#!/usr/bin/env python3
"""transform.py - build the stop-event warehouse for one service date (D-012).

Reads D's same-date static schedule and D's TripUpdates archives, plus D+1's
archives up to and including the hour containing the time two hours after D's
last scheduled arrival (D-011). Writes five Parquet tables to
data/warehouse/<table>/service_date=YYYY-MM-DD/part-0.parquet, replacing that
date's partition. Never uploads anywhere and never touches Google Sheets.

Usage:
    uv run --env-file .env python -m kronoberg_transit.transform 2026-09-07
"""

import argparse
import json
import os
import string
import sys
import tempfile
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

from kronoberg_transit.fetch_koda import fetch_day
from kronoberg_transit.gtfs_rt import stage_date
from kronoberg_transit.static_schedule import load_static_gtfs
from kronoberg_transit.time_utils import STOCKHOLM, local_hour_labels
from kronoberg_transit.time_utils import scheduled_time_utc as scheduled_time_utc_py

OPERATOR = "krono"
FEED = "TripUpdates"
RAW_DIR = Path("data/raw/koda")
STATIC_DIR = Path("data/static")
INTERIM_DIR = Path("data/interim/transform")
WAREHOUSE_DIR = Path("data/warehouse")
LOG_DIR = Path("data/logs")
SQL_DIR = Path(__file__).resolve().parent / "sql"

TABLES = ["trips", "stop_events", "routes", "stops", "feed_quality", "feed_gaps"]

# DuckDB memory bound: every connection this module opens gets a memory_limit
# and a spill (temp_directory) so it pages large intermediate tables to disk
# instead of growing RSS unbounded. Configurable via env vars.
DUCKDB_MEMORY_LIMIT = os.environ.get("DUCKDB_MEMORY_LIMIT", "2GB")
DUCKDB_TEMP_DIRECTORY = Path(os.environ.get("DUCKDB_TEMP_DIRECTORY", "data/tmp/duckdb"))


def new_duckdb_connection() -> duckdb.DuckDBPyConnection:
    DUCKDB_TEMP_DIRECTORY.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{DUCKDB_MEMORY_LIMIT}'")
    con.execute(f"SET temp_directory = '{DUCKDB_TEMP_DIRECTORY.as_posix()}'")
    return con


class RunLog:
    """Collects {stage, rows_in, rows_out} entries and writes them as JSON
    lines (run_log column names from docs/data_dictionary.md)."""

    def __init__(self, svc_date: str, run_type: str = "transform"):
        self.svc_date = svc_date
        self.run_type = run_type
        self.entries: list[dict] = []

    def stage(self, name: str, rows_in: int | None, rows_out: int | None, message: str = ""):
        entry = {
            "run_ts_utc": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_type": self.run_type,
            "service_date": self.svc_date,
            "stage": name,
            "status": "ok",
            "rows_in": rows_in,
            "rows_out": rows_out,
            "duration_s": None,
            "message": message,
        }
        self.entries.append(entry)
        print(f"  [{name}] rows_in={rows_in} rows_out={rows_out} {message}".rstrip())

    def timed_stage(self, name: str):
        return _TimedStage(self, name)

    def write(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        out_path = LOG_DIR / f"transform_{self.svc_date}.jsonl"
        with out_path.open("w", encoding="utf-8") as f:
            for entry in self.entries:
                f.write(json.dumps(entry) + "\n")
        return out_path


class _TimedStage:
    def __init__(self, log: RunLog, name: str):
        self.log = log
        self.name = name
        self.rows_in = None
        self.rows_out = None
        self.message = ""
        self._start = None

    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb):
        duration = time.monotonic() - self._start
        entry = {
            "run_ts_utc": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_type": self.log.run_type,
            "service_date": self.log.svc_date,
            "stage": self.name,
            "status": "error" if exc_type else "ok",
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "duration_s": round(duration, 3),
            "message": self.message,
        }
        self.log.entries.append(entry)
        print(
            f"  [{self.name}] rows_in={self.rows_in} rows_out={self.rows_out} "
            f"({duration:.2f}s) {self.message}".rstrip()
        )
        return False


def render_sql(name: str, **params) -> str:
    text = (SQL_DIR / name).read_text(encoding="utf-8")
    return string.Template(text).substitute(**params)


def duckdb_list_literal(paths: list[Path]) -> str:
    quoted = ", ".join("'" + str(p).replace("\\", "/") + "'" for p in paths)
    return f"[{quoted}]"


def compute_next_day_cutoff_hours(con: duckdb.DuckDBPyConnection, svc_date_obj: date) -> list[int]:
    """The D+1 local hours to read: none if D's last scheduled arrival plus
    2h still falls on D itself, otherwise every D+1 hour label that exists
    (local_hour_labels) up to and including the cutoff hour. Never returns a
    label D+1 doesn't have (D-011's spring-change gap)."""
    max_arr_hms = con.execute("SELECT MAX(arrival_time) FROM scheduled_stop_times").fetchone()[0]
    last_arrival_utc = scheduled_time_utc_py(svc_date_obj, max_arr_hms)
    cutoff_utc = datetime.fromtimestamp(last_arrival_utc, tz=UTC) + timedelta(hours=2)
    cutoff_local = cutoff_utc.astimezone(STOCKHOLM)
    next_day = svc_date_obj + timedelta(days=1)
    if cutoff_local.date() <= svc_date_obj:
        return []
    if cutoff_local.date() > next_day:
        # Extremely late-running schedules would need more than one extra day;
        # not observed in either validated date, so this is treated as a bug.
        raise RuntimeError(
            f"D+1 cutoff {cutoff_local.isoformat()} falls beyond {next_day}, unsupported"
        )
    return [h for h in local_hour_labels(next_day) if h <= cutoff_local.hour]


def ensure_hours_fetched(operator: str, feed: str, svc_date: str, hours, dest_dir: Path) -> None:
    key = os.environ.get("TRAFIKLAB_KODA_KEY")
    missing = [h for h in hours if not (dest_dir / f"{h:02d}.7z").exists()]
    if not missing:
        return
    if not key:
        raise SystemExit(
            f"Missing {feed} archives for {svc_date} hours {missing} and "
            "TRAFIKLAB_KODA_KEY is not set."
        )
    results = fetch_day(operator, feed, svc_date, key, dest_dir, hours=missing)
    failed = {h: v for h, v in results.items() if isinstance(v, Exception)}
    if failed:
        raise SystemExit(f"Failed to fetch {feed} for {svc_date}: {failed}")


def check_single_operator_per_trip(con: duckdb.DuckDBPyConnection) -> None:
    """D-013: a trip's operator must be unambiguous. Fail the run rather than
    silently pick one of several is_operator=1 rows."""
    bad = con.execute("SELECT trip_id FROM trip_scope WHERE n_operator_rows > 1").fetchall()
    if bad:
        trip_ids = [r[0] for r in bad]
        raise AssertionError(f"Trips with more than one is_operator=1 attribution row: {trip_ids}")


def run_hard_checks(con: duckdb.DuckDBPyConnection) -> None:
    checks: list[tuple[str, bool]] = []

    n_stop_events = con.execute("SELECT COUNT(*) FROM stop_events").fetchone()[0]
    n_sched_stop_times = con.execute("SELECT COUNT(*) FROM scheduled_stop_times").fetchone()[0]
    checks.append(
        ("stop_events rows == scheduled stop_times rows", n_stop_events == n_sched_stop_times)
    )

    n_trips = con.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
    n_sched_trips = con.execute("SELECT COUNT(*) FROM scheduled_trips").fetchone()[0]
    checks.append(("trips rows == scheduled trips", n_trips == n_sched_trips))

    n_dupe_keys = con.execute(
        "SELECT COUNT(*) FROM (SELECT trip_id, stop_sequence FROM stop_events "
        "GROUP BY 1, 2 HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    checks.append(("(trip_id, stop_sequence) unique in stop_events", n_dupe_keys == 0))

    n_orphan_trips = con.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT trip_id FROM stop_events) se "
        "WHERE NOT EXISTS (SELECT 1 FROM trips t WHERE t.trip_id = se.trip_id)"
    ).fetchone()[0]
    checks.append(("every stop_events trip exists in trips", n_orphan_trips == 0))

    n_bad_nonfinal = con.execute(
        "SELECT COUNT(*) FROM stop_events WHERE stop_position != 'final' AND status IS NULL"
    ).fetchone()[0]
    checks.append(("every non-final row has a status", n_bad_nonfinal == 0))

    n_bad_final = con.execute(
        "SELECT COUNT(*) FROM stop_events WHERE stop_position = 'final' AND status IS NOT NULL"
    ).fetchone()[0]
    checks.append(("every final row has a null status", n_bad_final == 0))

    n_bad_delay = con.execute(
        "SELECT COUNT(*) FROM stop_events WHERE (status = 'observed') != (delay_s IS NOT NULL)"
    ).fetchone()[0]
    checks.append(("delay_s is non-null exactly when status = observed", n_bad_delay == 0))

    n_bad_scope = con.execute(
        "SELECT COUNT(*) FROM trips WHERE in_scope = (trip_status = 'out_of_scope')"
    ).fetchone()[0]
    checks.append(
        ("trips.in_scope is false exactly when trip_status = out_of_scope", n_bad_scope == 0)
    )

    n_scope_mismatch = con.execute(
        "SELECT COUNT(*) FROM stop_events se JOIN trips t ON t.trip_id = se.trip_id "
        "WHERE se.in_scope != t.in_scope"
    ).fetchone()[0]
    checks.append(("stop_events.in_scope matches trips.in_scope", n_scope_mismatch == 0))

    n_bad_out_of_scope_status = con.execute(
        "SELECT COUNT(*) FROM stop_events se JOIN trips t ON t.trip_id = se.trip_id "
        "WHERE t.trip_status = 'out_of_scope' AND se.stop_position != 'final' "
        "AND se.status != 'out_of_scope'"
    ).fetchone()[0]
    checks.append(
        (
            "every non-final stop event of an out-of-scope trip has status out_of_scope",
            n_bad_out_of_scope_status == 0,
        )
    )

    n_bad_gap_len = con.execute("SELECT COUNT(*) FROM feed_gaps WHERE gap_s <= 300").fetchone()[0]
    checks.append(("every feed_gaps row has gap_s > 300", n_bad_gap_len == 0))

    n_overlapping_gaps = con.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT gap_start_utc, LAG(gap_end_utc) OVER (ORDER BY gap_start_utc) AS prev_end"
        "  FROM feed_gaps"
        ") WHERE prev_end IS NOT NULL AND gap_start_utc < prev_end"
    ).fetchone()[0]
    checks.append(("feed_gaps windows don't overlap within a date", n_overlapping_gaps == 0))

    n_bad_outage_flag = con.execute(
        "SELECT COUNT(*) FROM trips "
        "WHERE (no_data_in_outage IS NOT NULL) != (trip_status = 'no_realtime_data')"
    ).fetchone()[0]
    checks.append(
        (
            "trips.no_data_in_outage is non-null exactly when trip_status = no_realtime_data",
            n_bad_outage_flag == 0,
        )
    )

    n_null_timing_stop = con.execute(
        "SELECT COUNT(*) FROM stop_events WHERE is_timing_stop IS NULL"
    ).fetchone()[0]
    checks.append(("stop_events.is_timing_stop is non-null on every row", n_null_timing_stop == 0))

    failed = [name for name, ok in checks if not ok]
    if failed:
        raise AssertionError(f"Hard checks failed: {failed}")
    print(f"  All {len(checks)} hard checks passed.")


def write_partition(
    con: duckdb.DuckDBPyConnection,
    table_sql_name: str,
    table: str,
    svc_date: str,
    warehouse_dir: Path = WAREHOUSE_DIR,
) -> int:
    out_dir = warehouse_dir / table / f"service_date={svc_date}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "part-0.parquet"
    if out_path.exists():
        out_path.unlink()
    con.execute(
        f"COPY (SELECT * FROM {table_sql_name}) TO '{out_path.as_posix()}' (FORMAT PARQUET)"
    )
    return con.execute(f"SELECT COUNT(*) FROM {table_sql_name}").fetchone()[0]


def run_transform(svc_date: str, warehouse_dir: Path = WAREHOUSE_DIR) -> dict:
    svc_date_obj = date.fromisoformat(svc_date)
    next_day = (svc_date_obj + timedelta(days=1)).isoformat()
    date_int = int(svc_date.replace("-", ""))
    date_str_compact = svc_date.replace("-", "")
    weekday_col = svc_date_obj.strftime("%A").lower()
    own_hours = local_hour_labels(svc_date_obj)

    log = RunLog(svc_date)
    print(f"Transforming {OPERATOR}/{FEED} for {svc_date}")

    con = new_duckdb_connection()
    con.execute("SET TimeZone='UTC'")

    with tempfile.TemporaryDirectory() as tmpdir:
        with log.timed_stage("static_schedule_load") as s:
            static_dir = load_static_gtfs(
                svc_date,
                os.environ.get("TRAFIKLAB_KODA_KEY"),
                Path(tmpdir),
                extra_files=["calendar.txt", "stops.txt", "agency.txt", "attributions.txt"],
            )
            con.execute(
                render_sql(
                    "static_schedule.sql",
                    static_dir=static_dir.as_posix(),
                    date_int=date_int,
                    weekday_col=weekday_col,
                )
            )
            n_scheduled = con.execute("SELECT COUNT(*) FROM scheduled_trips").fetchone()[0]
            check_single_operator_per_trip(con)
            n_empty_timepoint = con.execute(
                "SELECT COUNT(*) FROM scheduled_stop_times WHERE timepoint IS NULL OR timepoint = ''"
            ).fetchone()[0]
            s.rows_out = n_scheduled
            s.message = f"{n_scheduled} trips active on {svc_date}; {n_empty_timepoint} empty timepoint values"

        next_day_hours = compute_next_day_cutoff_hours(con, svc_date_obj)

        own_dir = RAW_DIR / OPERATOR / FEED / svc_date
        ensure_hours_fetched(OPERATOR, FEED, svc_date, own_hours, own_dir)
        if next_day_hours:
            next_dir = RAW_DIR / OPERATOR / FEED / next_day
            ensure_hours_fetched(OPERATOR, FEED, next_day, next_day_hours, next_dir)

        own_interim = INTERIM_DIR / svc_date / "own"
        with log.timed_stage("stage_own_archives") as s:
            present, missing = stage_date(OPERATOR, FEED, svc_date, own_interim, hours=own_hours)
            s.rows_out = len(present)
            s.message = (
                f"{len(present)}/{len(own_hours)} own hours staged, missing: {missing or 'none'}"
            )

        next_interim = INTERIM_DIR / svc_date / "next_day"
        with log.timed_stage("stage_next_day_archives") as s:
            if next_day_hours:
                present_n, missing_n = stage_date(
                    OPERATOR, FEED, next_day, next_interim, hours=next_day_hours
                )
                s.rows_out = len(present_n)
                s.message = f"D+1 hours read: {present_n}, missing: {missing_n or 'none'}"
            else:
                s.rows_out = 0
                s.message = "no D+1 hours needed"

        glob_paths = [own_interim / "*.parquet"]
        if next_day_hours:
            glob_paths.append(next_interim / "*.parquet")

        with log.timed_stage("dedup_snapshots") as s:
            con.execute(render_sql("realtime_dedup.sql", glob_list=duckdb_list_literal(glob_paths)))
            n_raw = con.execute("SELECT COUNT(*) FROM raw_rows").fetchone()[0]
            n_dedup = con.execute("SELECT COUNT(*) FROM rows_dedup").fetchone()[0]
            s.rows_in, s.rows_out = n_raw, n_dedup

        with log.timed_stage("feed_gaps") as s:
            window_start_utc = scheduled_time_utc_py(svc_date_obj, "00:00:00")
            if next_day_hours:
                window_end_hms = f"{24 + max(next_day_hours) + 1:02d}:00:00"
            else:
                window_end_hms = "24:00:00"
            window_end_utc = scheduled_time_utc_py(svc_date_obj, window_end_hms)
            con.execute(
                render_sql(
                    "feed_gaps.sql",
                    svc_date=svc_date,
                    feed=FEED,
                    window_start_utc=window_start_utc,
                    window_end_utc=window_end_utc,
                )
            )
            n_gaps = con.execute("SELECT COUNT(*) FROM feed_gaps").fetchone()[0]
            s.rows_out = n_gaps
            s.message = f"window {window_start_utc}..{window_end_utc}, {n_gaps} outages"

        with log.timed_stage("held_values") as s:
            con.execute(render_sql("held_values.sql", date_str=date_str_compact))
            n_ignored_prior = con.execute(
                "SELECT COUNT(DISTINCT trip_id) FROM ignored_prior_day_rows WHERE start_date < ?",
                [date_str_compact],
            ).fetchone()[0]
            n_ignored_next = con.execute(
                "SELECT COUNT(DISTINCT trip_id) FROM ignored_prior_day_rows WHERE start_date > ?",
                [date_str_compact],
            ).fetchone()[0]
            n_ignored = n_ignored_prior + n_ignored_next
            n_stops = con.execute("SELECT COUNT(*) FROM stop_last_appearance").fetchone()[0]
            s.rows_out = n_stops
            s.message = (
                f"{n_ignored} distinct trips ignored (start_date != {svc_date}): "
                f"{n_ignored_prior} prior-day, {n_ignored_next} next-day"
            )

        con.create_function(
            "scheduled_time_utc",
            lambda hms: scheduled_time_utc_py(svc_date_obj, hms) if hms else None,
            ["VARCHAR"],
            "BIGINT",
        )

        with log.timed_stage("build_trips") as s:
            con.execute(render_sql("trips.sql", svc_date=svc_date))
            s.rows_in = n_scheduled
            s.rows_out = con.execute("SELECT COUNT(*) FROM trips").fetchone()[0]

        with log.timed_stage("build_stop_events") as s:
            con.execute(render_sql("stop_events.sql", svc_date=svc_date))
            s.rows_in = con.execute("SELECT COUNT(*) FROM scheduled_stop_times").fetchone()[0]
            s.rows_out = con.execute("SELECT COUNT(*) FROM stop_events").fetchone()[0]

        with log.timed_stage("build_routes_stops") as s:
            con.execute(render_sql("routes_stops.sql", svc_date=svc_date))
            s.rows_out = (
                con.execute("SELECT COUNT(*) FROM routes_out").fetchone()[0]
                + con.execute("SELECT COUNT(*) FROM stops_out").fetchone()[0]
            )

        with log.timed_stage("run_hard_checks"):
            run_hard_checks(con)

        feed_quality_row = build_feed_quality(
            con, svc_date, own_interim, own_hours, next_day_hours, log
        )

        with log.timed_stage("write_partitions") as s:
            counts = {
                "trips": write_partition(con, "trips", "trips", svc_date, warehouse_dir),
                "stop_events": write_partition(
                    con, "stop_events", "stop_events", svc_date, warehouse_dir
                ),
                "routes": write_partition(con, "routes_out", "routes", svc_date, warehouse_dir),
                "stops": write_partition(con, "stops_out", "stops", svc_date, warehouse_dir),
                "feed_gaps": write_partition(
                    con, "feed_gaps", "feed_gaps", svc_date, warehouse_dir
                ),
            }
            write_feed_quality_partition(feed_quality_row, svc_date, warehouse_dir)
            counts["feed_quality"] = 1
            s.rows_out = sum(counts.values())
            s.message = str(counts)

    log_path = log.write()
    print(f"Run log: {log_path}")

    return {
        "svc_date": svc_date,
        "next_day_hours": next_day_hours,
        "n_scheduled_trips": n_scheduled,
        "n_ignored_prior_day": n_ignored_prior,
        "n_ignored_next_day": n_ignored_next,
        "feed_quality": feed_quality_row,
        "log_entries": log.entries,
    }


def build_feed_quality(
    con: duckdb.DuckDBPyConnection,
    svc_date: str,
    own_interim: Path,
    own_hours: list[int],
    next_day_hours: list[int],
    log: RunLog,
) -> dict:
    with log.timed_stage("feed_quality_stats") as s:
        con.execute(
            render_sql(
                "feed_quality_snapshots.sql",
                own_glob=duckdb_list_literal([own_interim / "*.parquet"]),
            )
        )
        n_archive_files = con.execute("SELECT COUNT(*) FROM all_snapshot_files_own").fetchone()[0]
        n_distinct = con.execute("SELECT COUNT(*) FROM snapshots_own").fetchone()[0]
        first_ts, last_ts = con.execute(
            "SELECT MIN(header_timestamp), MAX(header_timestamp) FROM snapshots_own"
        ).fetchone()
        max_gap, gaps_over_300 = con.execute(
            "SELECT MAX(gap_s), COUNT(*) FILTER (WHERE gap_s > 300) FROM snapshot_gaps_own"
        ).fetchone()
        hours_present = {
            r[0] for r in con.execute("SELECT DISTINCT hour FROM all_snapshot_files_own").fetchall()
        }
        hours_without = sorted(set(own_hours) - hours_present)

        n_out_of_scope_in_feed = con.execute(
            "SELECT COUNT(*) FROM trips WHERE trip_status = 'out_of_scope' AND first_seen_utc IS NOT NULL"
        ).fetchone()[0]

        s.rows_in = n_archive_files
        s.rows_out = n_distinct
        s.message = (
            f"gaps_over_300s={gaps_over_300} out_of_scope_trips_in_feed={n_out_of_scope_in_feed}"
        )

    return {
        "service_date": svc_date,
        "feed": FEED,
        "archive_files": n_archive_files,
        "distinct_snapshots": n_distinct,
        "duplicate_snapshots": n_archive_files - n_distinct,
        "out_of_scope_trips_in_feed": n_out_of_scope_in_feed,
        "first_snapshot_utc": (
            datetime.fromtimestamp(first_ts, tz=UTC).replace(tzinfo=None) if first_ts else None
        ),
        "last_snapshot_utc": (
            datetime.fromtimestamp(last_ts, tz=UTC).replace(tzinfo=None) if last_ts else None
        ),
        "max_gap_s": max_gap,
        "gaps_over_300s": gaps_over_300,
        "local_hours_without_snapshots": ",".join(f"{h:02d}" for h in hours_without),
        "next_day_hours_read": ",".join(f"{h:02d}" for h in next_day_hours),
    }


def write_feed_quality_partition(
    row: dict, svc_date: str, warehouse_dir: Path = WAREHOUSE_DIR
) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    out_dir = warehouse_dir / "feed_quality" / f"service_date={svc_date}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "part-0.parquet"
    table = pa.Table.from_pylist([row])
    pq.write_table(table, out_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", help="Service date to process (YYYY-MM-DD)")
    parser.add_argument(
        "--warehouse-dir",
        default=str(WAREHOUSE_DIR),
        help=f"Warehouse output directory (default: {WAREHOUSE_DIR})",
    )
    args = parser.parse_args()

    result = run_transform(args.date, warehouse_dir=Path(args.warehouse_dir))
    print(f"\nDone. {result['n_scheduled_trips']} trips scheduled on {args.date}.")
    print(f"D+1 hours read: {result['next_day_hours'] or 'none'}")
    print(
        f"Realtime trips ignored: {result['n_ignored_prior_day']} prior-day, "
        f"{result['n_ignored_next_day']} next-day"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
