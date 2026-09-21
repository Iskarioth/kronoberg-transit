#!/usr/bin/env python3
"""
koda_check.py - verify that Trafiklab's KoDa historical archive is alive and
actually serving Kronoberg (krono) GTFS-RT data.

What it does:
  1. Requests one hour of historical TripUpdates for the Kronoberg operator.
  2. Handles the HTTP 202 "archive is being built" case by polling.
  3. Confirms the payload is a real 7-zip archive and not an error page.
  4. Optionally lists the protobuf files inside, if py7zr is installed.

Usage:
    export TRAFIKLAB_KODA_KEY="your-koda-api-key"
    python3 koda_check.py                  # checks a date ~14 days ago
    python3 koda_check.py 2026-03-12       # checks a specific date
    python3 koda_check.py 2026-03-12 17    # specific date and hour

Notes:
  - The KoDa key is a separate key from your GTFS Regional key. Add a "KoDa"
    key to your project at developer.trafiklab.se.
  - An hour is requested rather than a full day so the archive is small and
    fast to build. A full day can take up to an hour to generate.
"""

import datetime as dt
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "https://api.koda.trafiklab.se/KoDa/api/v2"
OPERATOR = "krono"  # Länstrafiken Kronoberg
FEED = "TripUpdates"  # ServiceAlerts | TripUpdates | VehiclePositions
SEVEN_ZIP_MAGIC = b"\x37\x7a\xbc\xaf\x27\x1c"
POLL_SECONDS = 30
MAX_WAIT_MINUTES = 20
TIMEOUT = 120


def redact(url, key):
    return url.replace(key, "***") if key else url


def build_url(operator, feed, date, hour, key):
    url = f"{BASE}/gtfs-rt/{operator}/{feed}?date={date}&key={key}"
    if hour is not None:
        url += f"&hour={hour:02d}"
    return url


def fetch(url, key):
    """Return (status, body_bytes). Body is empty for non-200 responses."""
    req = urllib.request.Request(url, headers={"User-Agent": "koda-check/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status = resp.status
            body = resp.read() if status == 200 else b""
            return status, body
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "replace").strip()
        print(f"  HTTP {e.code} {e.reason}")
        if detail:
            print(f"  Body: {detail}")
        return e.code, b""
    except urllib.error.URLError as e:
        print(f"  Connection failed: {e.reason}")
        return None, b""


def describe_archive(path):
    """List a few entries inside the 7z, if py7zr is available."""
    try:
        import py7zr
    except ImportError:
        print("  (install py7zr to list archive contents: pip install py7zr)")
        return
    try:
        with py7zr.SevenZipFile(path, mode="r") as archive:
            names = archive.getnames()
        print(f"  Archive contains {len(names)} entries. First few:")
        for name in names[:5]:
            print(f"    {name}")
    except Exception as e:  # noqa: BLE001 - archive parsing, boundary with external data
        print(f"  Could not read archive: {e}")


def main():
    key = os.environ.get("TRAFIKLAB_KODA_KEY")
    if not key:
        sys.exit("Set TRAFIKLAB_KODA_KEY in your environment first.")

    if len(sys.argv) > 1:
        date = sys.argv[1]
    else:
        date = (dt.datetime.now(tz=dt.UTC).date() - dt.timedelta(days=14)).isoformat()
    hour = int(sys.argv[2]) if len(sys.argv) > 2 else 8

    url = build_url(OPERATOR, FEED, date, hour, key)
    print(f"Checking KoDa for {OPERATOR}/{FEED} on {date} hour {hour:02d}")
    print(f"  {redact(url, key)}\n")

    started = time.time()
    deadline = started + MAX_WAIT_MINUTES * 60
    attempt = 0

    while True:
        attempt += 1
        status, body = fetch(url, key)

        if status == 200:
            break
        if status == 202:
            waited = int(time.time() - started)
            print(f"  HTTP 202 - archive is being built (waited {waited}s)")
            if time.time() + POLL_SECONDS > deadline:
                print(f"\nVERDICT: still building after {MAX_WAIT_MINUTES} min.")
                print("KoDa is responding, but the archive was not ready in time.")
                print("Re-run later, or pre-trigger files with HEAD requests.")
                return 2
            time.sleep(POLL_SECONDS)
            continue
        if status == 401 or status == 403:
            print("\nVERDICT: authentication failed.")
            print("Check that this key is a KoDa key, not a GTFS Regional key.")
            return 1
        if status == 404:
            print("\nVERDICT: no data for that operator/feed/date.")
            print("Try a different date, or confirm the operator abbreviation.")
            return 1

        print(f"\nVERDICT: unexpected response after {attempt} attempt(s).")
        return 1

    outfile = f"koda_{OPERATOR}_{FEED}_{date}_{hour:02d}.7z"
    with open(outfile, "wb") as f:
        f.write(body)

    size_mb = len(body) / 1_048_576
    is_7z = body[:6] == SEVEN_ZIP_MAGIC

    print(f"  HTTP 200 - {len(body):,} bytes ({size_mb:.2f} MB)")
    print(f"  Saved to {outfile}")
    print(f"  Valid 7-zip header: {is_7z}")

    if not is_7z:
        print("\nVERDICT: got a 200 but the payload is not a 7z archive.")
        print("First 100 bytes:", body[:100])
        return 1

    describe_archive(outfile)
    print("\nVERDICT: KoDa is alive and serving Kronoberg data. Proceed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
