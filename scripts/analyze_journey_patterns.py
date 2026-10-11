"""Reproduce September journey evidence; exports stay in ignored data/.

Requires scripts/prepare_case_study.py's snapshot. No credentials or live writes.
"""

import csv
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "case-study" / "snapshot"
OUTPUT = ROOT / "data" / "case-study" / "journey-patterns"
REVISION = "3cb190163d9c400fd915bd62a919f8f9bfc90dd7"


def main() -> None:
    manifest = json.loads((SNAPSHOT.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["revision"] == REVISION
    assert len(manifest["files"]) == 180
    for entry in manifest["files"]:
        path = SNAPSHOT / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"], path
    con = duckdb.connect()
    for table in ("stop_events", "trips", "routes", "stops"):
        path = (SNAPSHOT / "data" / table / "service_date=*" / "*.parquet").as_posix()
        con.execute(
            f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{path}',hive_partitioning=true)"
        )
    assert con.execute("SELECT min(service_date),max(service_date) FROM trips").fetchone() == (
        dt.date(2026, 9, 1),
        dt.date(2026, 9, 30),
    )
    for table, key in (
        ("stop_events", "service_date,trip_id,stop_sequence"),
        ("trips", "service_date,trip_id"),
        ("routes", "service_date,route_id"),
        ("stops", "service_date,stop_id"),
    ):
        assert (
            con.execute(
                f"SELECT count(*) FROM (SELECT {key} FROM {table} GROUP BY ALL HAVING count(*)>1)"
            ).fetchone()[0]
            == 0
        ), table
    assert (
        con.execute(
            "SELECT count(*) FROM stop_events s JOIN trips t USING(service_date,trip_id) "
            "WHERE t.in_scope AND (scheduled_departure_utc IS NULL OR is_timing_stop IS NULL)"
        ).fetchone()[0]
        == 0
    )
    sql = (ROOT / "scripts" / "sql" / "journey_patterns.sql").read_text(encoding="utf-8")
    parts = re.split(r"\n-- result: ([a-z0-9_]+)\n", sql)
    con.execute(parts[0])
    results = {}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, query in zip(parts[1::2], parts[2::2], strict=True):
        result = con.execute(query)
        columns = [column[0] for column in result.description]
        rows = [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
        results[name] = {"sql": query.strip(), "rows": rows}
        with (OUTPUT / f"{name}.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        print(f"{name}: {len(rows)} rows")
    assert results["validation"]["rows"] == [
        {"scheduled_scope": 51714, "eligible": 129069, "observed": 127059, "on_time": 99396}
    ]
    assert results["ambiguity"]["rows"][0]["ambiguous_recurring_keys"] == 4
    assert len(results["focus_keys"]["rows"]) == 2
    for row in results["focus_keys"]["rows"]:
        assert row["observed_trips"] == row["observed_dates"] == 22
        assert row["coverage"] >= 0.9
    for row in results["focus_pairs"]["rows"]:
        assert row["observed_pairs"] <= row["eligible_pairs"]
        assert row["ontime_to_late"] + row["late_to_ontime"] <= row["observed_pairs"]
    assert (
        con.execute(
            "SELECT count(*) FROM timing WHERE status='observed' AND delay_s IS NULL"
        ).fetchone()[0]
        == 0
    )
    assert (
        con.execute(
            "SELECT count(*) FROM pairs WHERE pair_observed AND NOT pair_eligible"
        ).fetchone()[0]
        == 0
    )
    receipt = {
        "dataset": manifest["dataset"],
        "revision": REVISION,
        "executed_at": dt.datetime.now(dt.UTC).isoformat(),
        "sha256_files_verified": len(manifest["files"]),
        "setup_sql": parts[0],
        "results": results,
        "checks": "Manifest, source keys, date bounds, September baseline, recurring identity, "
        "paired eligibility and transition count bounds passed.",
    }
    (OUTPUT / "evidence.json").write_text(
        json.dumps(receipt, default=str, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Checks passed; exports saved under data/case-study/journey-patterns/.")


if __name__ == "__main__":
    main()
