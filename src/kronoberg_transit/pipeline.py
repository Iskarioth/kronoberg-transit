#!/usr/bin/env python3
"""pipeline.py - the daily pipeline (D-020).

Preflights HF/Google/KoDa access, selects the service dates that are missing
from the Hugging Face dataset (the warehouse of record), and for each date:
fetches, transforms, uploads that date's six table partitions to the
dataset in one commit, deletes intermediate files, and appends a run_log
row. Stops on the first failure. Then downloads the full dataset and
rebuilds the Google Sheet from it (skippable with --skip-sheet).

Stop events in the daylight-saving window are marked dst_ambiguous in the
warehouse rather than blocking the date (D-021; the D-020 date guard is
removed). A date with a non-zero schedule_mismatch_stop_events count is
still processed and uploaded, logged with run_log status='warning'.

Usage:
    uv run --env-file .env python -m kronoberg_transit.pipeline
    uv run --env-file .env python -m kronoberg_transit.pipeline --dates 2026-09-21..2026-09-25
    uv run --env-file .env python -m kronoberg_transit.pipeline --max-dates 3 --skip-sheet
"""

import argparse
import datetime as dt
import os
import shutil
import sys
import time
from pathlib import Path

import duckdb
from huggingface_hub import CommitOperationAdd, HfApi, snapshot_download

from kronoberg_transit import aggregate, transform
from kronoberg_transit.time_utils import STOCKHOLM

ANALYSIS_FLOOR = dt.date(2026, 9, 1)
DEFAULT_MAX_DATES = 7
HF_REPO_TYPE = "dataset"


class PreflightError(Exception):
    """A preflight check failed; nothing has been fetched or written yet."""


class RunLog:
    """Collects run_log rows (data_dictionary.md's run_log columns) and can
    flush them to the Sheet's run_log tab."""

    def __init__(self, run_type: str = "pipeline"):
        self.run_type = run_type
        self.rows: list[dict] = []

    def add(
        self,
        stage: str,
        status: str,
        service_date: str | None = None,
        rows_in: int | None = None,
        rows_out: int | None = None,
        duration_s: float | None = None,
        message: str = "",
    ) -> None:
        row = {
            "run_ts_utc": dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_type": self.run_type,
            "service_date": service_date or "",
            "stage": stage,
            "status": status,
            "rows_in": rows_in,
            "rows_out": rows_out,
            "duration_s": round(duration_s, 3) if duration_s is not None else None,
            "message": message,
        }
        self.rows.append(row)
        print(f"  [run_log] {row}")

    def flush_to_sheet(self, sh) -> None:
        ws = sh.worksheet("run_log")
        for row in self.rows:
            ws.append_row(
                [
                    row["run_ts_utc"],
                    row["run_type"],
                    row["service_date"],
                    row["stage"],
                    row["status"],
                    row["rows_in"] if row["rows_in"] is not None else "",
                    row["rows_out"] if row["rows_out"] is not None else "",
                    row["duration_s"] if row["duration_s"] is not None else "",
                    row["message"],
                ],
                value_input_option="RAW",
            )
        self.rows.clear()


# --------------------------------------------------------------------------
# Preflight (D-020 step 1): every check below must pass before anything is
# fetched. Each check is read-only / non-destructive.
# --------------------------------------------------------------------------


def check_hf_preflight(repo_id: str | None, token: str | None) -> None:
    if not token:
        raise PreflightError("HF_TOKEN is not set")
    if not repo_id:
        raise PreflightError("HF_DATASET_REPO is not set")
    api = HfApi(token=token)
    try:
        who = api.whoami()
    except Exception as e:
        raise PreflightError(f"HF_TOKEN is invalid or expired: {e}") from e
    try:
        api.repo_info(repo_id=repo_id, repo_type=HF_REPO_TYPE, token=token)
    except Exception as e:
        raise PreflightError(f"Cannot access dataset repo {repo_id}: {e}") from e
    # Best-effort permission check (no HF API for "would this write succeed"
    # without actually writing): a fine-grained or classic token that
    # reports a read-only role is rejected here; anything else proceeds and
    # the first real upload would surface a permission error.
    role = who.get("auth", {}).get("accessToken", {}).get("role")
    if role == "read":
        raise PreflightError(f"HF_TOKEN has role 'read'; it cannot write to {repo_id}")


def check_sheet_preflight():
    try:
        return aggregate.get_sheet()
    except Exception as e:
        raise PreflightError(f"Cannot open the Google Sheet: {e}") from e


def check_koda_key_preflight() -> None:
    if not os.environ.get("TRAFIKLAB_KODA_KEY"):
        raise PreflightError("TRAFIKLAB_KODA_KEY is not set")


def run_preflight(repo_id: str | None, hf_token: str | None):
    check_hf_preflight(repo_id, hf_token)
    sh = check_sheet_preflight()
    check_koda_key_preflight()
    return sh


# --------------------------------------------------------------------------
# Date selection (D-020 step 2)
# --------------------------------------------------------------------------


def list_hf_service_dates(repo_id: str, token: str, table: str = "trips") -> list[dt.date]:
    """Service dates already present in the dataset, read from one table's
    partition directories (every table is uploaded together, so any one
    table's dates represent the dataset's coverage)."""
    api = HfApi(token=token)
    files = api.list_repo_files(repo_id=repo_id, repo_type=HF_REPO_TYPE)
    prefix = f"data/{table}/service_date="
    dates: set[dt.date] = set()
    for f in files:
        if not f.startswith(prefix):
            continue
        rest = f[len(prefix) :]
        date_str = rest.split("/", 1)[0]
        try:
            dates.add(dt.date.fromisoformat(date_str))
        except ValueError:
            continue
    return sorted(dates)


def select_dates(
    hf_dates: list[dt.date], today_stockholm: dt.date, max_dates: int = DEFAULT_MAX_DATES
) -> list[dt.date]:
    """From the day after the latest date in the dataset up to
    today-2 days (Stockholm), at most max_dates, never before
    ANALYSIS_FLOOR."""
    latest = max(hf_dates) if hf_dates else (ANALYSIS_FLOOR - dt.timedelta(days=1))
    start = max(latest + dt.timedelta(days=1), ANALYSIS_FLOOR)
    end = today_stockholm - dt.timedelta(days=2)
    dates = []
    d = start
    while d <= end and len(dates) < max_dates:
        dates.append(d)
        d += dt.timedelta(days=1)
    return dates


def parse_dates_arg(spec: str) -> list[dt.date]:
    """'--dates A..B' (inclusive range) or a single 'YYYY-MM-DD'."""
    if ".." in spec:
        a, b = spec.split("..", 1)
        start, end = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
        if start > end:
            raise ValueError(f"Invalid date range {spec!r}: start after end")
        out = []
        d = start
        while d <= end:
            out.append(d)
            d += dt.timedelta(days=1)
        return out
    return [dt.date.fromisoformat(spec)]


# --------------------------------------------------------------------------
# Per-date processing (D-020 step 3)
# --------------------------------------------------------------------------


def partition_row_counts(svc_date_str: str, warehouse_dir: Path) -> dict[str, int]:
    import pyarrow.parquet as pq

    counts = {}
    for table in transform.TABLES:
        path = warehouse_dir / table / f"service_date={svc_date_str}" / "part-0.parquet"
        counts[table] = pq.ParquetFile(path).metadata.num_rows
    return counts


def upload_partitions(svc_date_str: str, repo_id: str, token: str, warehouse_dir: Path) -> None:
    api = HfApi(token=token)
    operations = []
    for table in transform.TABLES:
        local_path = warehouse_dir / table / f"service_date={svc_date_str}" / "part-0.parquet"
        if not local_path.exists():
            raise FileNotFoundError(f"Expected partition not found: {local_path}")
        repo_path = f"data/{table}/service_date={svc_date_str}/part-0.parquet"
        operations.append(
            CommitOperationAdd(path_in_repo=repo_path, path_or_fileobj=str(local_path))
        )
    api.create_commit(
        repo_id=repo_id,
        repo_type=HF_REPO_TYPE,
        operations=operations,
        commit_message=f"data: add service date {svc_date_str}",
    )


def delete_interim(svc_date_str: str) -> None:
    """Intermediate build files (D-020 consequences): staged parquet, raw
    7z archives and the cached static schedule. All can be refetched from
    KoDa, so this is safe to run after every successful date."""
    svc_date_obj = dt.date.fromisoformat(svc_date_str)
    next_day_str = (svc_date_obj + dt.timedelta(days=1)).isoformat()

    shutil.rmtree(transform.INTERIM_DIR / svc_date_str, ignore_errors=True)
    shutil.rmtree(
        transform.RAW_DIR / transform.OPERATOR / transform.FEED / svc_date_str, ignore_errors=True
    )
    shutil.rmtree(
        transform.RAW_DIR / transform.OPERATOR / transform.FEED / next_day_str, ignore_errors=True
    )
    for ext in (".zip", ".7z"):
        static_path = transform.STATIC_DIR / f"{transform.OPERATOR}_{svc_date_str}{ext}"
        static_path.unlink(missing_ok=True)


def process_date(
    svc_date: dt.date,
    repo_id: str,
    hf_token: str,
    warehouse_dir: Path,
    keep_interim: bool,
    run_log: RunLog,
) -> str:
    """Returns 'ok' or 'failed'. The daylight-saving date guard (D-020) is
    removed by D-021: stop events in the window are marked dst_ambiguous in
    the warehouse instead, and a date with schedule mismatches is still
    processed and uploaded, just logged with status='warning'."""
    svc_date_str = svc_date.isoformat()

    print(f"Processing {svc_date_str}...")
    t0 = time.monotonic()
    try:
        result = transform.run_transform(svc_date_str, warehouse_dir=warehouse_dir)
    except Exception as e:  # noqa: BLE001 - any transform failure stops the run
        duration = time.monotonic() - t0
        run_log.add(
            "transform", "error", service_date=svc_date_str, duration_s=duration, message=str(e)
        )
        print(f"STOP: transform failed for {svc_date_str}: {e}")
        return "failed"

    out_of_scope = result["feed_quality"]["out_of_scope_trips_in_feed"]
    if out_of_scope > 0:
        duration = time.monotonic() - t0
        message = f"out_of_scope_trips_in_feed={out_of_scope} > 0 (D-013 tripwire)"
        run_log.add(
            "transform", "error", service_date=svc_date_str, duration_s=duration, message=message
        )
        print(f"STOP: {message}")
        return "failed"

    try:
        upload_partitions(svc_date_str, repo_id, hf_token, warehouse_dir)
    except Exception as e:  # noqa: BLE001
        duration = time.monotonic() - t0
        run_log.add(
            "transform",
            "error",
            service_date=svc_date_str,
            duration_s=duration,
            message=f"upload to {repo_id} failed: {e}",
        )
        print(f"STOP: upload failed for {svc_date_str}: {e}")
        return "failed"

    if not keep_interim:
        delete_interim(svc_date_str)

    counts = partition_row_counts(svc_date_str, warehouse_dir)
    duration = time.monotonic() - t0
    schedule_mismatches = result["feed_quality"]["schedule_mismatch_stop_events"]
    unmatched_trips = result["feed_quality"]["unmatched_realtime_trips"]
    status = "warning" if (schedule_mismatches > 0 or unmatched_trips > 0) else "ok"
    message = f"uploaded to {repo_id}: {counts}"
    if schedule_mismatches > 0:
        message += f"; schedule_mismatch_stop_events={schedule_mismatches} (D-021)"
    if unmatched_trips > 0:
        message += f"; unmatched_realtime_trips={unmatched_trips} (D-021)"
    run_log.add(
        "transform",
        status,
        service_date=svc_date_str,
        rows_in=result["n_scheduled_trips"],
        rows_out=sum(counts.values()),
        duration_s=duration,
        message=message,
    )
    return "ok"


# --------------------------------------------------------------------------
# Rebuild the Sheet from the full dataset (D-020 step 4)
# --------------------------------------------------------------------------


def download_hf_dataset(repo_id: str, token: str, local_dir: Path) -> Path:
    """Downloads the dataset's data/ folder to local_dir/data. Never
    touches the dataset card (README.md) - only data/** is requested."""
    snapshot_download(
        repo_id=repo_id,
        repo_type=HF_REPO_TYPE,
        token=token,
        local_dir=str(local_dir),
        allow_patterns=["data/**"],
    )
    return local_dir / "data"


def rebuild_sheet(sh, warehouse_dir: Path) -> None:
    """Rebuilds every tab and appends exactly one run_log row, with
    run_type='pipeline' (D-021 Part 1: one row per event, not one from this
    function plus another from the caller)."""
    t0 = time.monotonic()
    con = duckdb.connect()
    aggregate.build_tables(con, warehouse_dir=warehouse_dir)
    aggregate.run_consistency_checks(con)
    counts = {}
    for t in aggregate.TABS:
        counts[t] = aggregate.write_sheet_tab(sh, con, t)
        aggregate.verify_sheet_tab(sh, con, t)
    deleted = aggregate.delete_retired_tabs(sh)
    duration = time.monotonic() - t0
    message = f"tabs rebuilt: {counts}; retired tabs deleted: {deleted}"
    aggregate.append_run_log(sh, sum(counts.values()), duration, message, run_type="pipeline")
    print(f"Rebuilt {len(aggregate.TABS)} tabs: {counts}")


# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dates",
        default=None,
        help="Explicit date or range 'A..B' (YYYY-MM-DD); overrides auto-selection",
    )
    parser.add_argument("--max-dates", type=int, default=DEFAULT_MAX_DATES)
    parser.add_argument(
        "--skip-sheet", action="store_true", help="Don't touch the Google Sheet at all"
    )
    parser.add_argument(
        "--warehouse-dir",
        default=str(transform.WAREHOUSE_DIR),
        help=f"Local warehouse directory used while processing dates (default: {transform.WAREHOUSE_DIR})",
    )
    parser.add_argument(
        "--keep-interim",
        action="store_true",
        help="Don't delete intermediate build files after each date",
    )
    args = parser.parse_args()

    warehouse_dir = Path(args.warehouse_dir)
    hf_token = os.environ.get("HF_TOKEN")
    repo_id = os.environ.get("HF_DATASET_REPO")

    print("Preflight checks...")
    try:
        sh = run_preflight(repo_id, hf_token)
    except PreflightError as e:
        print(f"PREFLIGHT FAILED: {e}")
        return 1
    print("  preflight OK")

    if args.dates:
        dates = parse_dates_arg(args.dates)
    else:
        hf_dates = list_hf_service_dates(repo_id, hf_token)
        today = dt.datetime.now(tz=STOCKHOLM).date()
        dates = select_dates(hf_dates, today, args.max_dates)

    print(f"Dates to process: {[d.isoformat() for d in dates] or 'none'}")

    run_log = RunLog()
    any_failed = False
    for svc_date in dates:
        status = process_date(
            svc_date, repo_id, hf_token, warehouse_dir, args.keep_interim, run_log
        )
        if status == "failed":
            any_failed = True
            break

    if not args.skip_sheet:
        run_log.flush_to_sheet(sh)
        if not any_failed:
            local_dl_dir = Path("data/tmp/hf_download")
            hf_warehouse_dir = download_hf_dataset(repo_id, hf_token, local_dl_dir)
            rebuild_sheet(sh, hf_warehouse_dir)
        else:
            print("Skipping Sheet rebuild: a date failed this run.")
    else:
        print("skip-sheet: not touching the Sheet")

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
