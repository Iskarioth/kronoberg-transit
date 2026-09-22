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
    uv run --env-file .env python -m kronoberg_transit.fetch_koda 2026-09-08 --hours 0-3
"""

import argparse
import concurrent.futures
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date as _date
from pathlib import Path

from kronoberg_transit.time_utils import local_hour_labels

BASE = "https://api.koda.trafiklab.se/KoDa/api/v2"
SEVEN_ZIP_MAGIC = b"\x37\x7a\xbc\xaf\x27\x1c"
POLL_SECONDS = 30
MAX_WAIT_MINUTES = 20
TIMEOUT = 120
MAX_IN_FLIGHT = 2


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
    hours=None,
) -> dict[int, Path | Exception]:
    """Fetch the requested hourly archives for a service date into dest_dir.

    Default (hours=None) is the local Stockholm hour labels that exist on
    this date (local_hour_labels) - never a label KoDa doesn't have, and
    never missing a label the date does have.

    Resumable: hours already present in dest_dir are skipped. At most
    max_in_flight requests run concurrently (CLAUDE.md rule 4). Returns a
    {hour: path_or_exception} map so the caller can report partial failures
    without losing the hours that did succeed.
    """
    if hours is None:
        hours = local_hour_labels(_date.fromisoformat(date))
    results: dict[int, Path | Exception] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_in_flight) as pool:
        futures = {
            pool.submit(fetch_hour, operator, feed, date, hour, key, dest_dir): hour
            for hour in hours
        }
        for future in concurrent.futures.as_completed(futures):
            hour = futures[future]
            try:
                results[hour] = future.result()
            except Exception as e:  # noqa: BLE001 - one hour's failure must not abort the batch
                results[hour] = e
    return results


def parse_hours(spec: str) -> list[int]:
    """Parse a comma-separated hour spec, e.g. "3", "0,4,7" or "0-3,5,7-9",
    into a sorted list of distinct hours (0-23)."""
    hours: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
            if start > end:
                raise ValueError(f"Invalid hour range {part!r}: start must not exceed end")
            hours.update(range(start, end + 1))
        else:
            hours.add(int(part))
    for h in hours:
        if not 0 <= h <= 23:
            raise ValueError(f"Hour {h} out of range 0-23")
    return sorted(hours)


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
    parser.add_argument(
        "--hours",
        default=None,
        help=(
            "Subset of hours to fetch, e.g. '3', '0,4,7' or '0-3,5,7-9' "
            "(default: the local hour labels that exist on this date)"
        ),
    )
    args = parser.parse_args()

    key = os.environ.get("TRAFIKLAB_KODA_KEY")
    if not key:
        sys.exit("Set TRAFIKLAB_KODA_KEY in your environment first.")

    hours = (
        local_hour_labels(_date.fromisoformat(args.date))
        if args.hours is None
        else parse_hours(args.hours)
    )

    dest_dir = Path(args.out) / args.operator / args.feed / args.date
    print(
        f"Fetching {args.operator}/{args.feed} for {args.date} "
        f"(hours {hours[0]:02d}-{hours[-1]:02d}) into {dest_dir}"
    )

    results = fetch_day(args.operator, args.feed, args.date, key, dest_dir, hours=hours)

    ok_hours = sorted(h for h, v in results.items() if isinstance(v, Path))
    failed_hours = sorted(h for h, v in results.items() if isinstance(v, Exception))

    print(f"\n{len(ok_hours)}/{len(hours)} hours fetched OK, {len(failed_hours)} failed")
    for h in failed_hours:
        print(f"  hour {h:02d}: {results[h]}")

    return 1 if failed_hours else 0


if __name__ == "__main__":
    sys.exit(main())
