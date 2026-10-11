"""Reproduce the focused Växjö route 1 study from the pinned September snapshot.

No source writes or credentials. Individual observations and chart exports remain
in ignored data/. A notebook provides the public analytical record.
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
OUTPUT = ROOT / "data" / "case-study" / "city-route1"
REVISION = "3cb190163d9c400fd915bd62a919f8f9bfc90dd7"
ROUTE = "9011007000100000"


def main() -> None:
    manifest = json.loads((SNAPSHOT.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["revision"] == REVISION and len(manifest["files"]) == 180
    for entry in manifest["files"]:
        assert (
            hashlib.sha256((SNAPSHOT / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
        ), entry["path"]
    con = duckdb.connect()
    for table in ("stop_events", "trips", "routes", "stops"):
        path = (SNAPSHOT / "data" / table / "service_date=*" / "*.parquet").as_posix()
        route_filter = f" WHERE route_id='{ROUTE}'" if table != "stops" else ""
        con.execute(
            f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{path}',hive_partitioning=true)"
            + route_filter
        )
    category_path = (ROOT / "config" / "route_categories.csv").as_posix()
    con.execute(
        f"CREATE VIEW route_categories AS SELECT * FROM read_csv('{category_path}',all_varchar=true)"
    )
    for table, key in (
        ("stop_events", "service_date,trip_id,stop_sequence"),
        ("trips", "service_date,trip_id"),
        ("routes", "service_date,route_id"),
        ("stops", "service_date,stop_id"),
        ("route_categories", "route_id"),
    ):
        assert (
            con.execute(
                f"SELECT count(*) FROM (SELECT {key} FROM {table} GROUP BY ALL HAVING count(*)>1)"
            ).fetchone()[0]
            == 0
        ), table
    assert con.execute("SELECT min(service_date),max(service_date) FROM trips").fetchone() == (
        dt.date(2026, 9, 1),
        dt.date(2026, 9, 30),
    )
    shared_setup = (
        (ROOT / "scripts" / "sql" / "journey_patterns.sql")
        .read_text(encoding="utf-8")
        .split("-- result:")[0]
    )
    con.execute(shared_setup)
    assert con.execute("SELECT count(*) FROM ambiguous").fetchone()[0] == 0
    assert con.execute("SELECT DISTINCT category FROM journeys").fetchall() == [
        ("Växjö city lines",)
    ]
    assert con.execute("SELECT count(*) FROM journeys").fetchone()[0] == 3832
    joined = con.execute(
        "SELECT count(*) FROM timing WHERE status IN ('observed','unobserved')"
    ).fetchone()[0]
    raw = con.execute(
        "SELECT count(*) FROM stop_events WHERE in_scope AND is_timing_stop "
        "AND stop_position<>'final' AND status IN ('observed','unobserved')"
    ).fetchone()[0]
    assert joined == raw == 13194
    assert (
        con.execute(
            "SELECT count(*) FROM timing WHERE status='observed' AND "
            "date_diff('second',scheduled_departure_utc,held_departure_utc) IS DISTINCT FROM delay_s"
        ).fetchone()[0]
        == 0
    )
    sql = (ROOT / "scripts" / "sql" / "city_route1.sql").read_text(encoding="utf-8")
    parts = re.split(r"\n-- result: ([a-z0-9_]+)\n", sql)
    con.execute(parts[0])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results = {}
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
    assert results["scope"]["rows"][0]["scheduled_trips"] == 1354
    assert results["route_population"]["rows"] == [
        {"eligible": 13194, "observed": 13140, "on_time": 10539}
    ]
    section = results["section_summary"]["rows"][2]
    assert (section["observed_pairs"], section["eligible_pairs"], section["median_change"]) == (
        1338,
        1343,
        124,
    )
    recurring = results["recurring_section"]["rows"]
    assert sum(row["observed_pairs"] for row in recurring) == section["observed_pairs"]
    assert sum(row["eligible_pairs"] for row in recurring) == section["eligible_pairs"]
    assert sum(row["reportable"] for row in recurring) == 60
    for row in recurring:
        assert row["gained_delay"] + row["lost_delay"] + row["unchanged"] == row["observed_pairs"]
        assert row["observed_pairs"] == row["observed_trips"] == row["observed_dates"]
    for row in results["individual_pairs"]["rows"]:
        if row["pair_observed"]:
            assert row["pair_eligible"] and row["delta"] == row["next_delay"] - row["delay_s"]
    receipt = {
        "dataset": manifest["dataset"],
        "revision": REVISION,
        "executed_at": dt.datetime.now(dt.UTC).isoformat(),
        "sha256_files_verified": 180,
        "route_id": ROUTE,
        "route_categories_sha256": hashlib.sha256(Path(category_path).read_bytes()).hexdigest(),
        "setup_sql": shared_setup + parts[0],
        "source_filter": f"route_id={ROUTE}",
        "results": results,
        "checks": "Snapshot hashes, source-key uniqueness, dates, category, joined/raw "
        "eligibility, timestamp delays, recurring-to-section counts and paired arithmetic passed.",
    }
    (OUTPUT / "evidence.json").write_text(
        json.dumps(receipt, default=str, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Route 1 checks passed.")


if __name__ == "__main__":
    main()
