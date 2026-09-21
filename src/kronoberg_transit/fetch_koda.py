#!/usr/bin/env python3
"""
fetch_koda.py - resumable downloader for KoDa historical GTFS-Realtime archives.

Downloads one service date of a feed as 24 hourly archives for an operator, with
at most two requests in flight at a time (per CLAUDE.md rule 4). An hour whose
archive is already on disk is skipped without a network call, so an interrupted
run can be resumed by re-running the same command.

Usage:
    uv run --env-file .env python -m kronoberg_transit.fetch_koda 2026-09-07
    uv run --env-file .env python -m kronoberg_transit.fetch_koda 2026-09-07 --feed VehiclePositions
"""

import argparse
import concurrent.futures
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://api.koda.trafiklab.se/KoDa/api/v2"
SEVEN_ZIP_MAGIC = b"\x37\x7a\xbc\xaf\x27\x1c"
POLL_SECONDS = 30
MAX_WAIT_MINUTES = 20
TIMEOUT = 120
MAX_IN_FLIGHT = 2
HOURS = range(24)


class FetchError(Exception):
    """A single hour's archive could not be fetched."""


def build_url(operator: str, feed: str, date: str, hour: int, key: str) -> str:
    return f"{BASE}/gtfs-rt/{operator}/{feed}?date={date}&key={key}&hour={hour:02d}"


def redact(url: str, key: str) -> str:
    return url.replace(key, "***") if key else url


def fetch_hour(operator: str, feed: str, date: str, hour: int, key: str, dest_dir: Path) -> Path:
    """Download one hour's archive, polling through HTTP 202. Returns the saved path.

    Skips the network call entirely if the archive is already on disk, which is
    what makes a re-run of fetch_day resumable.
    """
    outfile = dest_dir / f"{hour:02d}.7z"
    if outfile.exists():
        return outfile

    dest_dir.mkdir(parents=True, exist_ok=True)
    url = build_url(operator, feed, date, hour, key)
    req = urllib.request.Request(url, headers={"User-Agent": "fetch-koda/1.0"})

    label = f"{operator}/{feed} {date} hour {hour:02d}"
    started = time.time()
    deadline = started + MAX_WAIT_MINUTES * 60

    while True:
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 202:
                if time.time() + POLL_SECONDS > deadline:
                    raise FetchError(f"{label}: still building after {MAX_WAIT_MINUTES} min") from e
                time.sleep(POLL_SECONDS)
                continue
            if e.code == 404:
                raise FetchError(f"{label}: no data (404)") from e
            detail = e.read()[:300].decode("utf-8", "replace").strip()
            raise FetchError(f"{label}: HTTP {e.code} {detail}") from e
        except urllib.error.URLError as e:
            raise FetchError(f"{label}: connection failed: {e.reason}") from e

    if body[:6] != SEVEN_ZIP_MAGIC:
        raise FetchError(f"{label}: payload is not a 7z archive")

    tmp = outfile.with_suffix(".7z.part")
    tmp.write_bytes(body)
    tmp.replace(outfile)
    return outfile


def fetch_day(
    operator: str,
    feed: str,
    date: str,
    key: str,
    dest_dir: Path,
    max_in_flight: int = MAX_IN_FLIGHT,
) -> dict[int, Path | Exception]:
    """Fetch all 24 hourly archives for a service date into dest_dir.

    Resumable: hours already present in dest_dir are skipped. Returns a
    {hour: path_or_exception} map so the caller can report partial failures
    without losing the hours that did succeed.
    """
    results: dict[int, Path | Exception] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_in_flight) as pool:
        futures = {
            pool.submit(fetch_hour, operator, feed, date, hour, key, dest_dir): hour
            for hour in HOURS
        }
        for future in concurrent.futures.as_completed(futures):
            hour = futures[future]
            try:
                results[hour] = future.result()
            except Exception as e:  # noqa: BLE001 - one hour's failure must not abort the batch
                results[hour] = e
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", help="Service date to fetch (YYYY-MM-DD)")
    parser.add_argument("--operator", default="krono")
    parser.add_argument(
        "--feed",
        default="TripUpdates",
        choices=["TripUpdates", "ServiceAlerts", "VehiclePositions"],
    )
    parser.add_argument("--out", default="data/raw/koda", help="Base output directory")
    args = parser.parse_args()

    key = os.environ.get("TRAFIKLAB_KODA_KEY")
    if not key:
        sys.exit("Set TRAFIKLAB_KODA_KEY in your environment first.")

    dest_dir = Path(args.out) / args.operator / args.feed / args.date
    print(f"Fetching {args.operator}/{args.feed} for {args.date} into {dest_dir}")

    results = fetch_day(args.operator, args.feed, args.date, key, dest_dir)

    ok_hours = sorted(h for h, v in results.items() if isinstance(v, Path))
    failed_hours = sorted(h for h, v in results.items() if isinstance(v, Exception))

    print(f"\n{len(ok_hours)}/24 hours fetched OK, {len(failed_hours)} failed")
    for h in failed_hours:
        print(f"  hour {h:02d}: {results[h]}")

    return 1 if failed_hours else 0


if __name__ == "__main__":
    sys.exit(main())
