#!/usr/bin/env python3
"""
smoke_trafiklab.py - smoke-test the Trafiklab realtime and KoDa historical
static keys end to end for Kronoberg (krono).

Checks:
  1. Realtime: exactly one request to the live GTFS Regional TripUpdates feed,
     parsed with gtfs-realtime-bindings.
  2. Historical static: one day of GTFS static schedule pulled from KoDa's
     historical endpoint (never the live GTFS Regional static endpoint, which
     is capped at 50 calls/month). Reports trips scheduled on that service
     date (via calendar_dates.txt, per how GTFS Regional defines service
     days) and the stop_times row count for those trips.

Usage:
    uv run --env-file .env python scripts/smoke_trafiklab.py
    uv run --env-file .env python scripts/smoke_trafiklab.py --date 2026-09-07
    uv run --env-file .env python scripts/smoke_trafiklab.py --realtime-only
    uv run --env-file .env python scripts/smoke_trafiklab.py --static-only
"""

import argparse
import csv
import datetime as dt
import gzip
import io
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path
from zoneinfo import ZoneInfo

from google.transit import gtfs_realtime_pb2

REALTIME_URL = "https://opendata.samtrafiken.se/gtfs-rt/krono/TripUpdates.pb"
KODA_STATIC_URL = "https://api.koda.trafiklab.se/KoDa/api/v2/gtfs-static/krono"
OPERATOR = "krono"
ZIP_MAGIC = b"PK\x03\x04"
SEVEN_ZIP_MAGIC = b"\x37\x7a\xbc\xaf\x27\x1c"
POLL_SECONDS = 30
MAX_WAIT_MINUTES = 20
TIMEOUT = 120
STATIC_DIR = Path("data/static")


def redact(url: str, key: str) -> str:
    return url.replace(key, "***") if key else url


def check_realtime(key: str) -> int:
    print(f"Requesting realtime TripUpdates for {OPERATOR}")
    url = f"{REALTIME_URL}?key={key}"
    print(f"  {redact(url, key)}")

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "smoke-trafiklab/1.0", "Accept-Encoding": "gzip, deflate"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
            encoding = resp.headers.get("Content-Encoding", "")
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "replace").strip()
        print(f"\nVERDICT (realtime): HTTP {e.code} {e.reason}")
        if detail:
            print(f"  Body: {detail}")
        return 1
    except urllib.error.URLError as e:
        print(f"\nVERDICT (realtime): connection failed: {e.reason}")
        return 1

    if encoding == "gzip":
        body = gzip.decompress(body)
    elif encoding == "deflate":
        body = zlib.decompress(body)

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(body)

    ts_utc = dt.datetime.fromtimestamp(feed.header.timestamp, tz=dt.UTC)
    ts_local = ts_utc.astimezone(ZoneInfo("Europe/Stockholm"))

    print(f"  Feed header timestamp: {ts_local.isoformat()} (Europe/Stockholm)")
    print(f"  Entities: {len(feed.entity)}")

    if len(feed.entity) == 0:
        print("\nVERDICT (realtime): parsed OK but feed has zero entities.")
        return 1

    print("\nVERDICT (realtime): OK.")
    return 0


def fetch_koda_static(date: str, key: str) -> bytes:
    url = f"{KODA_STATIC_URL}?date={date}&key={key}"
    print(f"Requesting KoDa historical static for {OPERATOR} on {date}")
    print(f"  {redact(url, key)}")

    req = urllib.request.Request(url, headers={"User-Agent": "smoke-trafiklab/1.0"})
    started = time.time()
    deadline = started + MAX_WAIT_MINUTES * 60

    while True:
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 202:
                waited = int(time.time() - started)
                print(f"  HTTP 202 - archive is being built (waited {waited}s)")
                if time.time() + POLL_SECONDS > deadline:
                    raise RuntimeError(
                        f"KoDa static archive still building after {MAX_WAIT_MINUTES} min"
                    )
                time.sleep(POLL_SECONDS)
                continue
            detail = e.read()[:300].decode("utf-8", "replace").strip()
            raise RuntimeError(f"HTTP {e.code} {e.reason}: {detail}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Connection failed: {e.reason}")


def extract_gtfs_text_files(body: bytes, names: list[str]) -> dict[str, str]:
    """Extract named text files from a zip or 7z GTFS static archive."""
    out: dict[str, str] = {}
    if body[:4] == ZIP_MAGIC:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            for name in names:
                out[name] = zf.read(name).decode("utf-8-sig")
    elif body[:6] == SEVEN_ZIP_MAGIC:
        import py7zr

        with py7zr.SevenZipFile(io.BytesIO(body), mode="r") as archive:
            extracted = archive.read(targets=names)
            for name in names:
                out[name] = extracted[name].read().decode("utf-8-sig")
    else:
        raise RuntimeError(f"Unrecognized archive format (first bytes: {body[:8]!r})")
    return out


def check_historical_static(date: str, key: str) -> int:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    try:
        body = fetch_koda_static(date, key)
    except RuntimeError as e:
        print(f"\nVERDICT (historical static): {e}")
        return 1

    is_zip = body[:4] == ZIP_MAGIC
    is_7z = body[:6] == SEVEN_ZIP_MAGIC
    ext = ".zip" if is_zip else (".7z" if is_7z else ".bin")
    outfile = STATIC_DIR / f"{OPERATOR}_{date}{ext}"
    outfile.write_bytes(body)

    print(f"  HTTP 200 - {len(body):,} bytes")
    print(f"  Saved to {outfile}")
    print(f"  Format: {'zip' if is_zip else ('7z' if is_7z else 'unknown')}")

    if not (is_zip or is_7z):
        print("\nVERDICT (historical static): payload is not a recognized archive.")
        print("First 100 bytes:", body[:100])
        return 1

    try:
        files = extract_gtfs_text_files(body, ["calendar_dates.txt", "trips.txt", "stop_times.txt"])
    except Exception as e:  # noqa: BLE001 - archive parsing, boundary with external data
        print(f"\nVERDICT (historical static): could not read GTFS files: {e}")
        return 1

    active_service_ids = {
        row["service_id"]
        for row in csv.DictReader(io.StringIO(files["calendar_dates.txt"]))
        if row["date"] == date.replace("-", "") and row["exception_type"] == "1"
    }

    scheduled_trip_ids = {
        row["trip_id"]
        for row in csv.DictReader(io.StringIO(files["trips.txt"]))
        if row["service_id"] in active_service_ids
    }

    stop_times_rows = sum(
        1
        for row in csv.DictReader(io.StringIO(files["stop_times.txt"]))
        if row["trip_id"] in scheduled_trip_ids
    )

    print(f"\n  Service date: {date}")
    print(f"  Trips scheduled (via calendar_dates.txt): {len(scheduled_trip_ids):,}")
    print(f"  stop_times rows for those trips: {stop_times_rows:,}")
    print("\nVERDICT (historical static): OK.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        default=(dt.datetime.now(tz=dt.UTC).date() - dt.timedelta(days=14)).isoformat(),
        help="Service date to check for the static feed (YYYY-MM-DD)",
    )
    parser.add_argument("--realtime-only", action="store_true")
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args()

    run_realtime = not args.static_only
    run_static = not args.realtime_only

    rc = 0

    if run_realtime:
        rt_key = os.environ.get("TRAFIKLAB_GTFS_RT_KEY")
        if not rt_key:
            sys.exit("Set TRAFIKLAB_GTFS_RT_KEY in your environment first.")
        rc |= check_realtime(rt_key)

    if run_static:
        koda_key = os.environ.get("TRAFIKLAB_KODA_KEY")
        if not koda_key:
            sys.exit("Set TRAFIKLAB_KODA_KEY in your environment first.")
        print()
        rc |= check_historical_static(args.date, koda_key)

    return rc


if __name__ == "__main__":
    sys.exit(main())
