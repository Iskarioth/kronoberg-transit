"""Reproduce complete September case-study evidence from a pinned public dataset.

Downloads only September partitions, without credentials. Writes data and evidence
under gitignored data/case-study/. Never reads .env or touches Google Sheets.
"""

import datetime as dt
import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb
from huggingface_hub import HfApi, hf_hub_download

from kronoberg_transit.aggregate import build_tables, run_consistency_checks

DATASET = "Traumenteize/kronoberg-transit-punctuality"
REVISION = "3cb190163d9c400fd915bd62a919f8f9bfc90dd7"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "case-study"


def records(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict]:
    result = con.execute(sql)
    columns = [column[0] for column in result.description]
    return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]


def main() -> None:
    info = HfApi(token=False).dataset_info(DATASET, revision=REVISION, files_metadata=True)
    files = sorted(
        file.rfilename
        for file in info.siblings
        if "/service_date=2026-09-" in file.rfilename and file.rfilename.endswith(".parquet")
    )
    assert len(files) == 180, f"Expected 30 dates times six tables, found {len(files)}"
    expected_dates = {f"2026-09-{day:02}" for day in range(1, 31)}
    for table in ("stop_events", "trips", "routes", "stops", "feed_quality", "feed_gaps"):
        dates = {
            path.split("/")[2].removeprefix("service_date=")
            for path in files
            if path.startswith(f"data/{table}/")
        }
        assert dates == expected_dates, f"Incomplete September table: {table}"

    def download(filename: str) -> Path:
        return Path(
            hf_hub_download(
                repo_id=DATASET,
                repo_type="dataset",
                revision=REVISION,
                filename=filename,
                token=False,
                local_dir=OUTPUT / "snapshot",
                cache_dir=OUTPUT / "cache",
            )
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(download, files))
    manifest = {
        "dataset": DATASET,
        "revision": REVISION,
        "files": [
            {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for name, path in zip(files, paths, strict=True)
        ],
    }
    warehouse = OUTPUT / "snapshot" / "data"
    con = duckdb.connect()
    build_tables(con, warehouse, ROOT / "config" / "route_categories.csv")
    run_consistency_checks(con)
    for table, key in (
        ("all_stop_events", "service_date, trip_id, stop_sequence"),
        ("all_trips", "service_date, trip_id"),
    ):
        duplicates = con.execute(
            f"SELECT count(*) FROM (SELECT {key} FROM {table} GROUP BY ALL HAVING count(*) > 1)"
        ).fetchone()[0]
        assert duplicates == 0, f"Duplicate keys in {table}"

    population = "month='2026-09' AND day_type='all' AND stop_set='timing_stops'"
    network = records(con, f"SELECT * FROM network_monthly WHERE {population}")[0]
    assert network["month_complete"] and network["service_dates"] == 30
    independent = records(
        con,
        """SELECT count(*) FILTER (WHERE status IN ('observed','unobserved')) AS eligible,
           count(*) FILTER (WHERE status='observed') AS observed,
           count(*) FILTER (WHERE status='observed' AND delay_s BETWEEN -60 AND 180) AS on_time,
           count(DISTINCT (service_date,trip_id)) FILTER (WHERE status='observed') AS trips
           FROM all_stop_events WHERE in_scope AND stop_position != 'final' AND is_timing_stop""",
    )[0]
    for raw, aggregate in (
        ("eligible", "eligible_departures"),
        ("observed", "observed_departures"),
        ("on_time", "on_time_departures"),
        ("trips", "observed_trips"),
    ):
        assert independent[raw] == network[aggregate], f"Raw/aggregate mismatch: {raw}"

    evidence = {
        "retrieved_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "dataset": DATASET,
        "revision": REVISION,
        "code_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "route_categories_sha256": hashlib.sha256(
            (ROOT / "config" / "route_categories.csv").read_bytes()
        ).hexdigest(),
        "network": network,
        "all_stops": records(
            con,
            "SELECT observed_departures,on_time_departures,eligible_departures,on_time_share,"
            "coverage_share FROM network_monthly WHERE month='2026-09' AND day_type='all' "
            "AND stop_set='all_stops'",
        )[0],
        "independent_raw_counts": independent,
        "day_types": records(
            con,
            "SELECT day_type, eligible_departures, observed_departures, on_time_departures, "
            "coverage_share,on_time_share FROM network_monthly WHERE month='2026-09' "
            "AND stop_set='timing_stops' AND day_type != 'all' ORDER BY day_type",
        ),
        "weekday_hours": records(
            con,
            "SELECT hour_local, observed_departures,on_time_departures,coverage_share,on_time_share "
            "FROM hour_monthly WHERE month='2026-09' AND stop_set='timing_stops' "
            "AND day_type='weekday' AND reportable ORDER BY on_time_share LIMIT 5",
        ),
        "categories": records(
            con,
            f"SELECT route_category,eligible_departures,observed_departures,on_time_departures,"
            f"coverage_share,on_time_share,reportable,not_reportable_reason "
            f"FROM category_monthly WHERE {population} "
            "ORDER BY on_time_share",
        ),
        "stations": records(
            con,
            f"SELECT station_name,observed_departures,on_time_departures,coverage_share,on_time_share "
            f"FROM station_monthly WHERE {population} AND reportable "
            "ORDER BY observed_departures DESC LIMIT 8",
        ),
        "station_counts": records(
            con,
            f"SELECT count(*) AS stations, count(*) FILTER (WHERE reportable) AS reportable_stations "
            f"FROM station_monthly WHERE {population}",
        )[0],
        "quality": records(con, "SELECT * FROM data_quality ORDER BY service_date"),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (OUTPUT / "evidence.json").write_text(
        json.dumps(evidence, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
    )
    print(
        json.dumps({key: value for key, value in evidence.items() if key != "quality"}, default=str)
    )


if __name__ == "__main__":
    main()
