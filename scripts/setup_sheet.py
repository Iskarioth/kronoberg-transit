#!/usr/bin/env python3
"""
setup_sheet.py - create/verify the Google Sheets serving layer for
kronoberg-transit: locale, tabs, headers, and a run_log entry.

Idempotent: an existing tab with matching headers is left alone. An existing
tab with different headers stops the script rather than overwriting it.

Usage:
    uv run --env-file .env python scripts/setup_sheet.py
    uv run --env-file .env python scripts/setup_sheet.py --ping   # CI: append a run_log row only
"""

import argparse
import datetime as dt
import os
import sys

import gspread

TABS = {
    "route_daily": [
        "service_date",
        "route_id",
        "route_name",
        "scheduled_departures",
        "observed_departures",
        "coverage_pct",
        "on_time_pct",
        "late_pct",
        "early_pct",
        "cancelled_trips",
        "median_delay_s",
        "p90_delay_s",
    ],
    "stop_hotspots": [
        "period_start",
        "period_end",
        "stop_id",
        "stop_name",
        "observed_departures",
        "median_delay_s",
        "p90_delay_s",
        "late_pct",
    ],
    "hour_of_day": [
        "period_start",
        "period_end",
        "day_type",
        "hour",
        "observed_departures",
        "on_time_pct",
        "median_delay_s",
    ],
    "data_quality": [
        "service_date",
        "feed",
        "expected_snapshots",
        "received_snapshots",
        "missing_hours",
        "trips_scheduled",
        "trips_observed",
        "notes",
    ],
    "run_log": [
        "run_ts_utc",
        "run_type",
        "service_date",
        "stage",
        "status",
        "rows_in",
        "rows_out",
        "duration_s",
        "message",
    ],
}

DEFAULT_TAB_NAMES = {"Sheet1", "Blad1"}


def get_sheet() -> gspread.Spreadsheet:
    key = os.environ.get("GOOGLE_SHEET_ID")
    sa_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "secrets/google_service_account.json")
    if not key:
        sys.exit("Set GOOGLE_SHEET_ID in your environment first.")
    gc = gspread.service_account(filename=sa_file)
    return gc.open_by_key(key)


def ensure_locale(sh: gspread.Spreadsheet) -> None:
    sh.batch_update(
        {
            "requests": [
                {
                    "updateSpreadsheetProperties": {
                        "properties": {"locale": "en_US", "timeZone": "Europe/Stockholm"},
                        "fields": "locale,timeZone",
                    }
                }
            ]
        }
    )
    print("Locale set to en_US, time zone Europe/Stockholm")


def ensure_tabs(sh: gspread.Spreadsheet) -> None:
    existing = {ws.title: ws for ws in sh.worksheets()}

    for name, headers in TABS.items():
        if name in existing:
            ws = existing[name]
            current_headers = ws.row_values(1)
            if current_headers and current_headers != headers:
                sys.exit(
                    f"Tab '{name}' already exists with different headers.\n"
                    f"  Expected: {headers}\n"
                    f"  Found:    {current_headers}\n"
                    "Stopping rather than overwrite. Resolve manually."
                )
            if not current_headers:
                ws.update(values=[headers], range_name="A1", value_input_option="USER_ENTERED")
                ws.freeze(rows=1)
            print(f"Tab '{name}': OK")
            continue

        ws = sh.add_worksheet(title=name, rows=100, cols=len(headers))
        ws.update(values=[headers], range_name="A1", value_input_option="USER_ENTERED")
        ws.freeze(rows=1)
        print(f"Tab '{name}': created")


def delete_default_tab(sh: gspread.Spreadsheet) -> None:
    for ws in sh.worksheets():
        if ws.title in DEFAULT_TAB_NAMES and ws.title not in TABS:
            sh.del_worksheet(ws)
            print(f"Deleted default tab '{ws.title}'")


def append_run_log(sh: gspread.Spreadsheet, run_type: str, message: str = "") -> None:
    ws = sh.worksheet("run_log")
    now = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = [now, run_type, "", run_type, "ok", "", "", "", message]
    ws.append_row(row, value_input_option="USER_ENTERED")
    print(f"Appended run_log row: run_type={run_type}, status=ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ping", action="store_true", help="Only append a run_log row (for CI)")
    parser.add_argument("--run-type", default=None, help="Override run_type for the appended row")
    args = parser.parse_args()

    sh = get_sheet()

    if args.ping:
        append_run_log(sh, run_type=args.run_type or "smoke-ci")
        return 0

    ensure_locale(sh)
    ensure_tabs(sh)
    delete_default_tab(sh)
    append_run_log(sh, run_type=args.run_type or "setup", message="Initial sheet setup")

    print("\nFinal tabs:")
    for ws in sh.worksheets():
        print(f"  {ws.title}: {ws.row_values(1)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
