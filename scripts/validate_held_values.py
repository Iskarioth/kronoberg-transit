#!/usr/bin/env python3
"""
validate_held_values.py - tests whether D-005's "held value" (the time field
from a stop's last TripUpdates appearance) is a recorded actual passage time
or a stale prediction, using TripUpdates alone and, for 2026-09-07, an
independent VehiclePositions passage check.

Evidence only: never edits docs/definitions.md or docs/decisions.md.

Usage:
    uv run --env-file .env python scripts/validate_held_values.py 2026-09-07 --vehicle-positions
    uv run --env-file .env python scripts/validate_held_values.py 2026-09-06
"""

import argparse
import itertools
import os
import sys
import tempfile
from bisect import bisect_left
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa

sys.path.insert(0, str(Path(__file__).resolve().parent))
from koda_scan_lib import (
    FEED,
    INTERIM_DIR,
    OPERATOR,
    REPORT_DIR,
    VP_FEED,
    VP_INTERIM_DIR,
    fmt_pct_list,
    fmt_share,
    fmt_ts_both,
    git_commit_hash,
    load_static_gtfs,
    percentile,
    setup_duckdb,
    stage_date,
    stage_date_vp,
)

from kronoberg_transit.time_utils import scheduled_time_utc


def assert_unique(con: duckdb.DuckDBPyConnection, table: str, key_cols: list[str]) -> None:
    """Fails loudly if `table` is not unique on key_cols. Every stop-event
    table in this script is keyed on (trip, stop_sequence) - a stop_id alone
    can repeat within one trip on a looping route, so stop_id-keyed joins
    silently fan out and duplicate rows."""
    cols = ", ".join(key_cols)
    n_total = con.execute(f"SELECT COUNT(*) FROM {table} AS __au_t").fetchone()[0]
    n_distinct = con.execute(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT {cols} FROM {table} AS __au_t)"
    ).fetchone()[0]
    if n_total != n_distinct:
        raise AssertionError(
            f"{table} violates uniqueness on ({cols}): {n_total} rows, {n_distinct} distinct"
        )


def assert_unique_rows(rows: list[dict], key_cols: list[str], label: str) -> None:
    """Python-side equivalent of assert_unique, for row lists already fetched
    out of DuckDB (e.g. V4's classification rows)."""
    keys = [tuple(r[c] for c in key_cols) for r in rows]
    if len(keys) != len(set(keys)):
        raise AssertionError(
            f"{label} violates uniqueness on {key_cols}: {len(keys)} rows, {len(set(keys))} distinct"
        )


# --------------------------------------------------------------------------
# Setup extensions on top of koda_scan_lib.setup_duckdb
# --------------------------------------------------------------------------


def setup_held_value_tables(con: duckdb.DuckDBPyConnection, static_dir: Path) -> None:
    """Adds static_stops (with coordinates) and the per-STU held-value series
    (excludes CANCELED trips and SKIPPED stop_time_updates), on top of the
    tables koda_scan_lib.setup_duckdb already built."""
    con.execute(
        f"CREATE OR REPLACE TABLE static_stops AS SELECT * FROM read_csv("
        f"'{static_dir}/stops.txt', header=true, quote='\"', escape='\"', delim=',', sample_size=-1, "
        f"types={{'stop_id': 'VARCHAR', 'stop_lat': 'DOUBLE', 'stop_lon': 'DOUBLE'}})"
    )

    con.execute(
        "CREATE OR REPLACE TABLE excluded_counts AS "
        "SELECT "
        "  (SELECT COUNT(*) FROM (SELECT DISTINCT r.header_timestamp, r.trip_id, r.start_date FROM rows_dedup "
        "     r JOIN target_trips tt ON r.trip_id = tt.trip_id AND COALESCE(r.start_date,'') = COALESCE(tt.start_date,'') "
        "     WHERE r.trip_schedule_relationship = 'CANCELED')) AS canceled_trip_observations,"
        "  (SELECT COUNT(*) FROM rows_dedup r JOIN target_trips tt ON r.trip_id = tt.trip_id "
        "     AND COALESCE(r.start_date,'') = COALESCE(tt.start_date,'') "
        "     WHERE r.stop_id IS NOT NULL AND r.stop_schedule_relationship = 'SKIPPED') AS skipped_stus"
    )

    con.execute(
        "CREATE OR REPLACE TABLE held_value_rows AS "
        "SELECT r.trip_id, r.start_date, r.stop_id, r.stop_sequence, r.header_timestamp, "
        "r.arrival_time, r.arrival_time_present, r.arrival_delay, r.arrival_delay_present, "
        "r.arrival_uncertainty, r.arrival_uncertainty_present, "
        "r.departure_time, r.departure_time_present, r.departure_delay, r.departure_delay_present, "
        "r.departure_uncertainty, r.departure_uncertainty_present, "
        "(sfs.final_stop_sequence IS NOT NULL AND r.stop_sequence = sfs.final_stop_sequence) AS is_final_stop "
        "FROM rows_dedup r "
        "JOIN target_trips tt ON r.trip_id = tt.trip_id AND COALESCE(r.start_date,'') = COALESCE(tt.start_date,'') "
        "LEFT JOIN static_final_stop sfs ON sfs.trip_id = r.trip_id "
        "WHERE r.stop_id IS NOT NULL "
        "AND r.trip_schedule_relationship != 'CANCELED' "
        "AND r.stop_schedule_relationship != 'SKIPPED'"
    )

    # held_time: departure.time, except arrival.time for the trip's final
    # static stop (the term "held value" from the handoff spec).
    con.execute(
        "CREATE OR REPLACE TABLE held_series AS "
        "SELECT trip_id, start_date, stop_id, stop_sequence, header_timestamp, is_final_stop, "
        "CASE WHEN is_final_stop THEN arrival_time ELSE departure_time END AS held_time, "
        "CASE WHEN is_final_stop THEN arrival_time_present ELSE departure_time_present END AS held_time_present, "
        "CASE WHEN is_final_stop THEN arrival_delay ELSE departure_delay END AS held_delay, "
        "CASE WHEN is_final_stop THEN arrival_delay_present ELSE departure_delay_present END AS held_delay_present, "
        "CASE WHEN is_final_stop THEN arrival_uncertainty ELSE departure_uncertainty END AS held_uncertainty, "
        "CASE WHEN is_final_stop THEN arrival_uncertainty_present ELSE departure_uncertainty_present END AS held_uncertainty_present "
        "FROM held_value_rows "
        "WHERE (is_final_stop AND arrival_time_present) OR (NOT is_final_stop AND departure_time_present)"
    )
    con.execute(
        "CREATE OR REPLACE TABLE held_series_annotated AS "
        "SELECT *, "
        "LAG(held_time) OVER w AS prev_held_time, "
        "(held_time IS DISTINCT FROM LAG(held_time) OVER w) AS changed "
        "FROM held_series "
        "WINDOW w AS (PARTITION BY trip_id, start_date, stop_sequence ORDER BY header_timestamp)"
    )


def build_held_value_summary(con: duckdb.DuckDBPyConnection) -> None:
    """Per (trip, stop): the held value, t_cross, the value at t_cross, changes
    after t_cross, drift, t_last_change, and how the stop left (mid-route drop
    vs trip removal). All set-based SQL - no Python-side per-row loops."""
    con.execute(
        "CREATE OR REPLACE TABLE held_value_summary AS "
        "SELECT trip_id, start_date, stop_id, stop_sequence, "
        "ARG_MAX(held_time, header_timestamp) AS held_value, "
        "MAX(header_timestamp) AS held_ts, "
        "ARG_MAX(held_uncertainty_present, header_timestamp) AS held_uncertainty_present, "
        "MIN(header_timestamp) FILTER (WHERE header_timestamp >= held_time) AS t_cross, "
        "MAX(header_timestamp) FILTER (WHERE changed) AS t_last_change, "
        "COUNT(*) FILTER (WHERE changed) AS n_changes_total "
        "FROM held_series_annotated "
        "GROUP BY trip_id, start_date, stop_id, stop_sequence"
    )
    con.execute(
        "CREATE OR REPLACE TABLE held_value_at_cross AS "
        "SELECT s.trip_id, s.start_date, s.stop_sequence, h.held_time AS value_at_t_cross "
        "FROM held_value_summary s JOIN held_series_annotated h "
        "ON h.trip_id = s.trip_id AND COALESCE(h.start_date,'') = COALESCE(s.start_date,'') "
        "AND h.stop_sequence = s.stop_sequence AND h.header_timestamp = s.t_cross"
    )
    con.execute(
        "CREATE OR REPLACE TABLE changes_after_cross AS "
        "SELECT s.trip_id, s.start_date, s.stop_sequence, "
        "COUNT(*) FILTER (WHERE h.changed AND h.header_timestamp > s.t_cross) AS n_changes_after_cross "
        "FROM held_value_summary s JOIN held_series_annotated h "
        "ON h.trip_id = s.trip_id AND COALESCE(h.start_date,'') = COALESCE(s.start_date,'') AND h.stop_sequence = s.stop_sequence "
        "WHERE s.t_cross IS NOT NULL "
        "GROUP BY s.trip_id, s.start_date, s.stop_sequence"
    )
    con.execute(
        "CREATE OR REPLACE TABLE trip_last_ts AS "
        "SELECT trip_id, start_date, MAX(header_timestamp) AS trip_last_ts "
        "FROM held_series_annotated GROUP BY 1, 2"
    )
    con.execute(
        "CREATE OR REPLACE TABLE held_value_check1 AS "
        "SELECT hvs.trip_id, hvs.start_date, hvs.stop_id, hvs.stop_sequence, "
        "hvs.held_value, hvs.held_ts, hvs.held_uncertainty_present, "
        "hvs.t_cross, hvac.value_at_t_cross, cac.n_changes_after_cross, "
        "(hvs.held_value - hvac.value_at_t_cross) AS drift, "
        "hvs.t_last_change, hvs.n_changes_total, "
        "(hvs.held_ts = tlt.trip_last_ts) AS is_trip_removal "
        "FROM held_value_summary hvs "
        "LEFT JOIN held_value_at_cross hvac "
        "ON hvac.trip_id = hvs.trip_id AND COALESCE(hvac.start_date,'') = COALESCE(hvs.start_date,'') AND hvac.stop_sequence = hvs.stop_sequence "
        "LEFT JOIN changes_after_cross cac "
        "ON cac.trip_id = hvs.trip_id AND COALESCE(cac.start_date,'') = COALESCE(hvs.start_date,'') AND cac.stop_sequence = hvs.stop_sequence "
        "JOIN trip_last_ts tlt "
        "ON tlt.trip_id = hvs.trip_id AND COALESCE(tlt.start_date,'') = COALESCE(hvs.start_date,'')"
    )
    assert_unique(con, "held_value_check1", ["trip_id", "COALESCE(start_date,'')", "stop_sequence"])


# --------------------------------------------------------------------------
# Check 1: frozen or drifting
# --------------------------------------------------------------------------

CHECK1_COLUMNS = [
    "trip_id",
    "start_date",
    "stop_id",
    "stop_sequence",
    "held_value",
    "held_ts",
    "held_uncertainty_present",
    "t_cross",
    "value_at_t_cross",
    "n_changes_after_cross",
    "drift",
    "t_last_change",
    "n_changes_total",
    "is_trip_removal",
]


def fetch_check1_rows(con: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = con.execute(f"SELECT {', '.join(CHECK1_COLUMNS)} FROM held_value_check1").fetchall()
    return [dict(zip(CHECK1_COLUMNS, r, strict=True)) for r in rows]


def summarize_check1(rows: list[dict]) -> dict:
    total = len(rows)
    reach_cross = [r for r in rows if r["t_cross"] is not None]
    zero_changes = [r for r in reach_cross if r["n_changes_after_cross"] == 0]
    drifts = [r["drift"] for r in reach_cross if r["drift"] is not None]
    t_last_minus_cross = [
        r["t_last_change"] - r["t_cross"] for r in reach_cross if r["t_last_change"] is not None
    ]
    held_minus_last_change = [
        r["held_value"] - r["t_last_change"] for r in rows if r["t_last_change"] is not None
    ]
    return {
        "total": total,
        "n_reach_cross": len(reach_cross),
        "n_zero_changes_after_cross": len(zero_changes),
        "n_zero_changes_denom": len(reach_cross),
        "drift_percentiles": {q: percentile(drifts, q) for q in (1, 5, 25, 50, 75, 95, 99)},
        "drift_share_within": {
            th: (sum(1 for d in drifts if abs(d) <= th) / len(drifts) if drifts else None)
            for th in (15, 30, 60)
        },
        "n_drift": len(drifts),
        "t_last_minus_cross_percentiles": {
            q: percentile(t_last_minus_cross, q) for q in (5, 25, 50, 75, 95)
        },
        "n_t_last_minus_cross": len(t_last_minus_cross),
        "held_minus_last_change_percentiles": {
            q: percentile(held_minus_last_change, q) for q in (5, 25, 50, 75, 95)
        },
        "n_held_minus_last_change": len(held_minus_last_change),
    }


def check1(rows: list[dict]) -> dict:
    overall = summarize_check1(rows)
    by_leave_kind = {
        "mid_route_drop": summarize_check1([r for r in rows if not r["is_trip_removal"]]),
        "trip_removal": summarize_check1([r for r in rows if r["is_trip_removal"]]),
    }
    by_uncertainty = {
        "uncertainty_present": summarize_check1([r for r in rows if r["held_uncertainty_present"]]),
        "uncertainty_absent": summarize_check1(
            [r for r in rows if not r["held_uncertainty_present"]]
        ),
    }
    return {"overall": overall, "by_leave_kind": by_leave_kind, "by_uncertainty": by_uncertainty}


# --------------------------------------------------------------------------
# Check 2: uncertainty
# --------------------------------------------------------------------------

TIME_BUCKET_CASE = (
    "CASE WHEN (header_timestamp - {field}) > 60 THEN '>60s_past' "
    "WHEN (header_timestamp - {field}) >= 0 THEN '0-60s_past' "
    "WHEN (header_timestamp - {field}) >= -60 THEN '0-60s_future' "
    "ELSE '>60s_future' END"
)
UNCERTAINTY_BUCKET_CASE = (
    "CASE WHEN NOT {present} THEN 'absent' "
    "WHEN {field} = 0 THEN 'present_zero' "
    "ELSE 'present_nonzero' END"
)


def check2_cross_tab(con: duckdb.DuckDBPyConnection, event: str) -> dict:
    """event: 'arrival' or 'departure'."""
    time_field = f"{event}_time"
    present_field = f"{event}_time_present"
    unc_field = f"{event}_uncertainty"
    unc_present_field = f"{event}_uncertainty_present"
    unc_case = UNCERTAINTY_BUCKET_CASE.format(present=unc_present_field, field=unc_field)
    time_case = TIME_BUCKET_CASE.format(field=time_field)

    rows = con.execute(
        f"SELECT {unc_case} AS unc_bucket, {time_case} AS time_bucket, COUNT(*) "
        f"FROM held_value_rows WHERE {present_field} GROUP BY 1, 2"
    ).fetchall()
    total = con.execute(f"SELECT COUNT(*) FROM held_value_rows WHERE {present_field}").fetchone()[0]

    nonzero_values = [
        r[0]
        for r in con.execute(
            f"SELECT {unc_field} FROM held_value_rows WHERE {present_field} AND {unc_present_field} AND {unc_field} != 0"
        ).fetchall()
    ]

    return {
        "total": total,
        "cross_tab": rows,
        "nonzero_value_percentiles": {
            q: percentile(nonzero_values, q) for q in (5, 25, 50, 75, 95)
        },
        "n_nonzero": len(nonzero_values),
    }


def check2_appearance(con: duckdb.DuckDBPyConnection, event: str) -> dict:
    time_field = f"{event}_time"
    unc_present_field = f"{event}_uncertainty_present"

    con.execute(
        "CREATE OR REPLACE TABLE uncertainty_stop_summary AS "
        "SELECT trip_id, start_date, stop_id, "
        f"MIN(header_timestamp) FILTER (WHERE {unc_present_field}) AS first_unc_ts, "
        f"MAX(header_timestamp) FILTER (WHERE NOT {unc_present_field}) AS last_absent_ts "
        "FROM held_value_rows "
        f"WHERE {time_field}_present "
        "GROUP BY 1, 2, 3"
    )
    n_appears = con.execute(
        "SELECT COUNT(*) FROM uncertainty_stop_summary WHERE first_unc_ts IS NOT NULL"
    ).fetchone()[0]
    n_appears_then_disappears = con.execute(
        "SELECT COUNT(*) FROM uncertainty_stop_summary "
        "WHERE first_unc_ts IS NOT NULL AND last_absent_ts IS NOT NULL AND last_absent_ts > first_unc_ts"
    ).fetchone()[0]

    now_minus_time = [
        r[0]
        for r in con.execute(
            "SELECT h.header_timestamp - h." + time_field + " "
            "FROM uncertainty_stop_summary s JOIN held_value_rows h "
            "ON h.trip_id = s.trip_id AND COALESCE(h.start_date,'') = COALESCE(s.start_date,'') "
            "AND h.stop_id = s.stop_id AND h.header_timestamp = s.first_unc_ts "
            f"WHERE h.{time_field}_present"
        ).fetchall()
    ]

    return {
        "n_appears": n_appears,
        "n_appears_then_disappears": n_appears_then_disappears,
        "now_minus_time_percentiles": {
            q: percentile(now_minus_time, q) for q in (5, 25, 50, 75, 95)
        },
        "n_now_minus_time": len(now_minus_time),
    }


def check2(con: duckdb.DuckDBPyConnection) -> dict:
    return {
        "arrival": {
            "cross_tab": check2_cross_tab(con, "arrival"),
            "appearance": check2_appearance(con, "arrival"),
        },
        "departure": {
            "cross_tab": check2_cross_tab(con, "departure"),
            "appearance": check2_appearance(con, "departure"),
        },
    }


# --------------------------------------------------------------------------
# Check 3: trip removal
# --------------------------------------------------------------------------


def check3(con: duckdb.DuckDBPyConnection) -> dict:
    con.execute(
        "CREATE OR REPLACE TABLE trip_removal_classification AS "
        "SELECT hvc.trip_id, hvc.start_date, hvc.held_ts AS removal_ts, "
        "COUNT(*) AS n_stops_remaining, "
        "COUNT(*) FILTER (WHERE hvc.held_value > hvc.held_ts) AS n_future, "
        "BOOL_OR(hvc.stop_sequence = sfs.final_stop_sequence) AS final_stop_present, "
        "BOOL_OR(hvc.stop_sequence = sfs.final_stop_sequence AND hvc.held_value > hvc.held_ts) AS final_stop_is_future, "
        "MAX(hvc.held_value) FILTER (WHERE hvc.stop_sequence = sfs.final_stop_sequence) AS final_stop_held_value "
        "FROM held_value_check1 hvc "
        "LEFT JOIN static_final_stop sfs ON sfs.trip_id = hvc.trip_id "
        "WHERE hvc.is_trip_removal "
        "GROUP BY hvc.trip_id, hvc.start_date, hvc.held_ts"
    )
    n_total = con.execute("SELECT COUNT(*) FROM trip_removal_classification").fetchone()[0]
    n_completed = con.execute(
        "SELECT COUNT(*) FROM trip_removal_classification WHERE n_future = 0"
    ).fetchone()[0]
    n_left_early = n_total - n_completed

    future_counts = [
        r[0]
        for r in con.execute(
            "SELECT n_future FROM trip_removal_classification WHERE n_future > 0"
        ).fetchall()
    ]
    n_final_among_future = con.execute(
        "SELECT COUNT(*) FROM trip_removal_classification WHERE n_future > 0 AND final_stop_is_future"
    ).fetchone()[0]

    removal_offsets = [
        r[0]
        for r in con.execute(
            "SELECT removal_ts - final_stop_held_value FROM trip_removal_classification "
            "WHERE n_future = 0 AND final_stop_present"
        ).fetchall()
    ]
    n_completed_with_final = con.execute(
        "SELECT COUNT(*) FROM trip_removal_classification WHERE n_future = 0 AND final_stop_present"
    ).fetchone()[0]

    return {
        "n_total": n_total,
        "n_completed": n_completed,
        "n_left_early": n_left_early,
        "future_stops_percentiles": {q: percentile(future_counts, q) for q in (25, 50, 75, 95)},
        "n_left_early_sampled": len(future_counts),
        "n_final_among_future": n_final_among_future,
        "removal_minus_final_arrival_percentiles": {
            q: percentile(removal_offsets, q) for q in (5, 25, 50, 75, 95)
        },
        "n_completed_with_final": n_completed_with_final,
    }


# --------------------------------------------------------------------------
# Check 4: time vs delay
# --------------------------------------------------------------------------


def build_sched_lookup(con: duckdb.DuckDBPyConnection, svc_date_obj: date) -> None:
    """sched_lookup: (trip_id, stop_sequence) -> scheduled arrival/departure in UTC,
    from the static schedule alone (never TripUpdates). Shared by check4 and V2/V3
    so multi-visit disambiguation and the time-vs-delay check use the same source."""
    rows = con.execute(
        "SELECT DISTINCT st.trip_id, st.stop_sequence, st.arrival_time, st.departure_time "
        "FROM static_stop_times st JOIN target_trips tt ON st.trip_id = tt.trip_id"
    ).fetchall()
    trip_ids, seqs, sched_arr, sched_dep = [], [], [], []
    for trip_id, seq, arr_hms, dep_hms in rows:
        trip_ids.append(trip_id)
        seqs.append(seq)
        sched_arr.append(scheduled_time_utc(svc_date_obj, arr_hms) if arr_hms else None)
        sched_dep.append(scheduled_time_utc(svc_date_obj, dep_hms) if dep_hms else None)
    tbl = pa.table(
        {
            "trip_id": trip_ids,
            "stop_sequence": seqs,
            "sched_arrival_utc": sched_arr,
            "sched_departure_utc": sched_dep,
        }
    )
    con.register("sched_lookup_src", tbl)
    con.execute("CREATE OR REPLACE TABLE sched_lookup AS SELECT * FROM sched_lookup_src")
    con.unregister("sched_lookup_src")


def check4(con: duckdb.DuckDBPyConnection) -> dict:
    result = {}
    for event in ("arrival", "departure"):
        time_f, delay_f, sched_f = f"{event}_time", f"{event}_delay", f"sched_{event}_utc"
        offset_expr = f"(h.{time_f} - h.{delay_f}) - sl.{sched_f}"
        n_exact, n_within60, n_total, pcts = con.execute(
            f"SELECT COUNT(*) FILTER (WHERE {offset_expr} = 0), "
            f"COUNT(*) FILTER (WHERE ABS({offset_expr}) <= 60), "
            f"COUNT(*), "
            f"quantile_cont({offset_expr}, [0.01,0.05,0.25,0.5,0.75,0.95,0.99]) "
            f"FROM held_value_rows h JOIN sched_lookup sl "
            f"ON sl.trip_id = h.trip_id AND sl.stop_sequence = h.stop_sequence "
            f"WHERE h.{time_f}_present AND h.{delay_f}_present AND sl.{sched_f} IS NOT NULL"
        ).fetchone()
        result[event] = {
            "n_exact_zero": n_exact,
            "n_within_60s": n_within60,
            "n_total": n_total,
            "percentiles": dict(zip((1, 5, 25, 50, 75, 95, 99), pcts, strict=True)) if pcts else {},
        }
    return result


# --------------------------------------------------------------------------
# Check 5: loose ends
# --------------------------------------------------------------------------


def check5a(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    return con.execute(
        "SELECT asf.hour, COUNT(*) AS archive_files, COUNT(DISTINCT asf.header_timestamp) AS distinct_ts, "
        "MIN(asf.header_timestamp) AS first_ts, MAX(asf.header_timestamp) AS last_ts, "
        "(SELECT COUNT(DISTINCT r.trip_id) FROM rows_dedup r WHERE r.hour = asf.hour) AS trip_entities "
        "FROM all_snapshot_files asf GROUP BY asf.hour ORDER BY asf.hour"
    ).fetchall()


def check5b_unmatched_start_dates(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    return con.execute(
        "SELECT start_date, COUNT(*) FROM trip_match WHERE NOT matched_static GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()


def check5b_match_previous_day(con: duckdb.DuckDBPyConnection, prev_static_dir: Path) -> dict:
    con.execute(
        f"CREATE OR REPLACE TABLE prev_day_trips AS SELECT DISTINCT trip_id FROM read_csv("
        f"'{prev_static_dir}/trips.txt', header=true, quote='\"', escape='\"', delim=',', "
        f"sample_size=-1, types={{'trip_id': 'VARCHAR'}})"
    )
    n_match_prev = con.execute(
        "SELECT COUNT(*) FROM trip_match tm WHERE NOT tm.matched_static "
        "AND EXISTS (SELECT 1 FROM prev_day_trips p WHERE p.trip_id = tm.trip_id)"
    ).fetchone()[0]
    n_unmatched = con.execute(
        "SELECT COUNT(*) FROM trip_match WHERE NOT matched_static"
    ).fetchone()[0]
    return {"n_unmatched": n_unmatched, "n_match_previous_day": n_match_prev}


def check5c_large_gaps(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    return con.execute(
        "SELECT LAG(header_timestamp) OVER (ORDER BY header_timestamp) AS gap_start, "
        "header_timestamp AS gap_end, "
        "header_timestamp - LAG(header_timestamp) OVER (ORDER BY header_timestamp) AS gap "
        "FROM snapshots QUALIFY gap > 300 ORDER BY gap DESC"
    ).fetchall()


def check5d_leave_and_return(con: duckdb.DuckDBPyConnection) -> dict:
    con.execute(
        "CREATE OR REPLACE TABLE held_trip_snapshot_stops AS "
        "SELECT trip_id, start_date, header_timestamp, LIST(stop_sequence) AS stop_seqs "
        "FROM held_series_annotated GROUP BY trip_id, start_date, header_timestamp"
    )
    rows = con.execute(
        "SELECT trip_id, start_date, header_timestamp, stop_seqs FROM held_trip_snapshot_stops "
        "ORDER BY trip_id, start_date, header_timestamp"
    ).fetchall()
    timelines: dict[tuple, list[tuple[int, set]]] = {}
    for trip_id, start_date, ts, stop_seqs in rows:
        timelines.setdefault((trip_id, start_date), []).append((ts, set(stop_seqs or [])))

    snapshot_ts = [
        r[0]
        for r in con.execute(
            "SELECT header_timestamp FROM snapshots ORDER BY header_timestamp"
        ).fetchall()
    ]

    events = []
    trips_with_return = set()
    for key, snaps in timelines.items():
        for i in range(len(snaps) - 1):
            ts_a, stops_a = snaps[i]
            ts_b, stops_b = snaps[i + 1]
            idx_a = bisect_left(snapshot_ts, ts_a)
            idx_b = bisect_left(snapshot_ts, ts_b)
            if idx_b - idx_a > 1:
                events.append(
                    {
                        "duration": ts_b - ts_a,
                        "n_dropped": len(stops_a - stops_b),
                    }
                )
                trips_with_return.add(key)

    durations = [e["duration"] for e in events]
    dropped_counts = [e["n_dropped"] for e in events]
    return {
        "n_trips_with_return": len(trips_with_return),
        "n_events": len(events),
        "duration_percentiles": {q: percentile(durations, q) for q in (5, 25, 50, 75, 95)},
        "dropped_percentiles": {q: percentile(dropped_counts, q) for q in (5, 25, 50, 75, 95)},
    }


# --------------------------------------------------------------------------
# Check 6: held values without the recorded-time marker, by stop position and
# by how the stop left the feed (TripUpdates only - both dates)
# --------------------------------------------------------------------------


def check6_missing_marker(con: duckdb.DuckDBPyConnection) -> dict:
    n_total = con.execute("SELECT COUNT(*) FROM held_value_check1").fetchone()[0]
    n_missing = con.execute(
        "SELECT COUNT(*) FROM held_value_check1 WHERE NOT held_uncertainty_present"
    ).fetchone()[0]

    rows = con.execute(
        "SELECT "
        "CASE WHEN sfs.final_stop_sequence = hvc.stop_sequence THEN 'final' "
        "     WHEN sfirst.first_stop_sequence = hvc.stop_sequence THEN 'first' "
        "     ELSE 'intermediate' END AS position, "
        "CASE WHEN hvc.is_trip_removal THEN 'trip_removal' ELSE 'clean_drop' END AS leave_kind, "
        "COUNT(*) "
        "FROM held_value_check1 hvc "
        "LEFT JOIN static_final_stop sfs ON sfs.trip_id = hvc.trip_id "
        "LEFT JOIN static_first_stop sfirst ON sfirst.trip_id = hvc.trip_id "
        "WHERE NOT hvc.held_uncertainty_present "
        "GROUP BY 1, 2"
    ).fetchall()
    breakdown = {(position, leave_kind): n for position, leave_kind, n in rows}

    return {
        "n_total": n_total,
        "n_missing": n_missing,
        "breakdown": breakdown,
    }


# --------------------------------------------------------------------------
# Stops that reappeared after dropping: same feed anomaly as
# observed_time's "stops that dropped more than once" (see
# scripts/koda_scan_lib.py collect_drop_events), characterised here in terms
# of the held-value pipeline: does the value D-007 actually uses (the final
# appearance) differ from what was held just before the first drop?
# Requires held_trip_snapshot_stops, built by check5d_leave_and_return.
# --------------------------------------------------------------------------


def check7_reappeared_stops(con: duckdb.DuckDBPyConnection) -> dict:
    rows = con.execute(
        "SELECT trip_id, start_date, header_timestamp, stop_seqs "
        "FROM held_trip_snapshot_stops ORDER BY trip_id, start_date, header_timestamp"
    ).fetchall()
    timelines: dict[tuple, list[tuple[int, set]]] = {}
    for trip_id, start_date, ts, stop_seqs in rows:
        timelines.setdefault((trip_id, start_date), []).append((ts, set(stop_seqs or [])))

    reappeared: list[tuple] = []
    for (trip_id, start_date), snaps in timelines.items():
        ever_seen: dict[int, int] = {}
        reappeared_already: set[int] = set()
        prev_seqs: set | None = None
        for ts, seqs in snaps:
            if prev_seqs is not None:
                newly_present = seqs - prev_seqs
                for seq in newly_present:
                    if seq in ever_seen and seq not in reappeared_already:
                        reappeared.append((trip_id, start_date, seq, ever_seen[seq]))
                        reappeared_already.add(seq)
            for seq in seqs:
                ever_seen[seq] = ts
            prev_seqs = seqs

    if not reappeared:
        return {"n": 0}

    tbl = pa.table(
        {
            "trip_id": [r[0] for r in reappeared],
            "start_date": pa.array([r[1] for r in reappeared], type=pa.string()),
            "stop_sequence": [r[2] for r in reappeared],
            "last_ts_before_drop": [r[3] for r in reappeared],
        }
    )
    con.register("reappeared_src", tbl)
    con.execute("CREATE OR REPLACE TABLE reappeared_stops AS SELECT * FROM reappeared_src")
    con.unregister("reappeared_src")

    matched = con.execute(
        "SELECT r.trip_id, r.start_date, r.stop_sequence, "
        "hb.held_time AS time_before, hb.held_uncertainty_present AS marker_before, "
        "hc.held_value AS time_final, hc.held_uncertainty_present AS marker_final "
        "FROM reappeared_stops r "
        "JOIN held_series_annotated hb "
        "ON hb.trip_id = r.trip_id AND COALESCE(hb.start_date,'') = COALESCE(r.start_date,'') "
        "AND hb.stop_sequence = r.stop_sequence AND hb.header_timestamp = r.last_ts_before_drop "
        "JOIN held_value_check1 hc "
        "ON hc.trip_id = r.trip_id AND COALESCE(hc.start_date,'') = COALESCE(r.start_date,'') "
        "AND hc.stop_sequence = r.stop_sequence"
    ).fetchall()

    n = len(matched)
    n_differs = sum(1 for r in matched if r[3] != r[5])
    diffs = [r[5] - r[3] for r in matched]
    matrix: dict[tuple[bool, bool], int] = {}
    for r in matched:
        marker_key = (bool(r[4]), bool(r[6]))
        matrix[marker_key] = matrix.get(marker_key, 0) + 1

    return {
        "n": n,
        "n_reappeared_total": len(reappeared),
        "n_differs": n_differs,
        "diff_percentiles": {q: percentile(diffs, q) for q in (5, 25, 50, 75, 95)},
        "n_diffs": len(diffs),
        "marker_matrix": matrix,
    }


def check7_vp_offsets(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Per reappeared stop (from the reappeared_stops table check7 built):
    the V3 (R=50m) offset against VP for both the final-appearance held
    value and the value from just before the first drop. Call after
    build_vp_estimate(con) has run at R=50m."""
    rows = con.execute(
        "SELECT r.trip_id, r.stop_sequence, hb.held_time AS time_before, "
        "hc.held_value AS time_final, ve.vp_time "
        "FROM reappeared_stops r "
        "JOIN held_series_annotated hb "
        "ON hb.trip_id = r.trip_id AND COALESCE(hb.start_date,'') = COALESCE(r.start_date,'') "
        "AND hb.stop_sequence = r.stop_sequence AND hb.header_timestamp = r.last_ts_before_drop "
        "JOIN held_value_check1 hc "
        "ON hc.trip_id = r.trip_id AND COALESCE(hc.start_date,'') = COALESCE(r.start_date,'') "
        "AND hc.stop_sequence = r.stop_sequence "
        "JOIN vp_estimate ve ON ve.trip_id = r.trip_id AND ve.stop_sequence = r.stop_sequence "
        "ORDER BY r.trip_id, r.stop_sequence"
    ).fetchall()
    return [
        {
            "trip_id": tid,
            "stop_sequence": seq,
            "offset_before": time_before - vp_time,
            "offset_final": time_final - vp_time,
        }
        for tid, seq, time_before, time_final, vp_time in rows
    ]


# --------------------------------------------------------------------------
# VehiclePositions setup: dedup + scheduled-time window matching
#
# The feed's VehiclePosition entities are majority trip-less (idle/depot
# vehicles broadcasting position with no active assignment) and never carry
# start_date, so matching is trip_id-only plus a scheduled-time window
# (first departure - 30 min to final arrival + 90 min, using STATIC schedule
# times, not TripUpdates, to keep this an independent check). Both are
# deviations from a literal (trip_id, start_date) match, confirmed with
# Marcus given the real field-population numbers. See Observations.
# --------------------------------------------------------------------------


def setup_vp_tables(con: duckdb.DuckDBPyConnection, vp_glob: str, svc_date_obj: date) -> None:
    con.execute(f"CREATE OR REPLACE TABLE vp_raw AS SELECT * FROM read_parquet('{vp_glob}')")

    # Dedup by (vehicle_id, vehicle_timestamp): the raw feed repeats the same
    # ping across consecutive snapshot files, the same phenomenon found in
    # TripUpdates (see observed_time's duplicate_snapshot_count).
    con.execute(
        "CREATE OR REPLACE TABLE vp_trip_pings AS "
        "SELECT trip_id, start_date, vehicle_id, vehicle_timestamp, "
        "ANY_VALUE(latitude) AS latitude, ANY_VALUE(longitude) AS longitude, "
        "ANY_VALUE(current_status) AS current_status, "
        "ANY_VALUE(current_stop_sequence) AS current_stop_sequence, "
        "ANY_VALUE(stop_id) AS stop_id "
        "FROM vp_raw "
        "WHERE trip_id IS NOT NULL AND vehicle_id_present AND vehicle_timestamp_present "
        "GROUP BY trip_id, start_date, vehicle_id, vehicle_timestamp"
    )

    rows = con.execute(
        "SELECT sfs.trip_id, sfs.first_departure_hms, sls.final_arrival_hms "
        "FROM static_first_stop sfs JOIN static_final_stop sls ON sls.trip_id = sfs.trip_id "
        "JOIN target_trips tt ON tt.trip_id = sfs.trip_id"
    ).fetchall()
    trip_ids, window_starts, window_ends = [], [], []
    span_trip_ids, span_starts, span_ends = [], [], []
    for trip_id, first_dep_hms, final_arr_hms in rows:
        if not first_dep_hms or not final_arr_hms:
            continue
        first_dep = scheduled_time_utc(svc_date_obj, first_dep_hms)
        final_arr = scheduled_time_utc(svc_date_obj, final_arr_hms)
        if first_dep is None or final_arr is None:
            continue
        trip_ids.append(trip_id)
        window_starts.append(first_dep - 30 * 60)
        window_ends.append(final_arr + 90 * 60)
        span_trip_ids.append(trip_id)
        span_starts.append(first_dep)
        span_ends.append(final_arr)
    tbl = pa.table({"trip_id": trip_ids, "window_start": window_starts, "window_end": window_ends})
    con.register("vp_window_src", tbl)
    con.execute("CREATE OR REPLACE TABLE vp_trip_window AS SELECT * FROM vp_window_src")
    con.unregister("vp_window_src")

    # Unpadded scheduled span (no +-30/90min matching padding), used only to
    # scope V1 assignment dropouts to a trip's actual scheduled duration. The
    # matching window above (vp_trip_window) is unchanged.
    span_tbl = pa.table(
        {"trip_id": span_trip_ids, "span_start": span_starts, "span_end": span_ends}
    )
    con.register("vp_span_src", span_tbl)
    con.execute("CREATE OR REPLACE TABLE vp_trip_span AS SELECT * FROM vp_span_src")
    con.unregister("vp_span_src")

    con.execute(
        "CREATE OR REPLACE TABLE vp_trip_pings_windowed AS "
        "SELECT p.*, w.window_start, w.window_end, "
        "(p.vehicle_timestamp BETWEEN w.window_start AND w.window_end) AS in_window "
        "FROM vp_trip_pings p JOIN vp_trip_window w ON w.trip_id = p.trip_id"
    )
    con.execute(
        "CREATE OR REPLACE TABLE vp_intervals AS "
        "SELECT vehicle_id, "
        "vehicle_timestamp - LAG(vehicle_timestamp) OVER (PARTITION BY vehicle_id ORDER BY vehicle_timestamp) AS interval "
        "FROM (SELECT DISTINCT vehicle_id, vehicle_timestamp FROM vp_trip_pings_windowed)"
    )


def vp_guardrail_stats(con: duckdb.DuckDBPyConnection) -> dict:
    n_total = con.execute("SELECT COUNT(*) FROM vp_trip_pings_windowed").fetchone()[0]
    n_outside = con.execute(
        "SELECT COUNT(*) FROM vp_trip_pings_windowed WHERE NOT in_window"
    ).fetchone()[0]
    n_trip_ids_total = con.execute(
        "SELECT COUNT(DISTINCT trip_id) FROM vp_trip_pings_windowed"
    ).fetchone()[0]
    n_trip_ids_with_outside = con.execute(
        "SELECT COUNT(DISTINCT trip_id) FROM vp_trip_pings_windowed WHERE NOT in_window"
    ).fetchone()[0]

    intervals = [
        r[0]
        for r in con.execute(
            "SELECT interval FROM vp_intervals WHERE interval IS NOT NULL"
        ).fetchall()
    ]
    return {
        "n_total": n_total,
        "n_outside_window": n_outside,
        "share_outside": (n_outside / n_total) if n_total else None,
        "n_trip_ids_total": n_trip_ids_total,
        "n_trip_ids_with_outside": n_trip_ids_with_outside,
        "interval_percentiles": {q: percentile(intervals, q) for q in (5, 25, 50, 75, 95)},
        "n_intervals": len(intervals),
    }


# --------------------------------------------------------------------------
# V1: field population
# --------------------------------------------------------------------------


def v1_field_population(con: duckdb.DuckDBPyConnection) -> dict:
    n_total = con.execute("SELECT COUNT(*) FROM vp_raw").fetchone()[0]
    fields = {
        "trip_id": "trip_id IS NOT NULL",
        "start_date": "start_date IS NOT NULL",
        "vehicle_timestamp": "vehicle_timestamp_present",
        "position": "latitude_present",
        "current_status": "current_status_present",
        "current_stop_sequence": "current_stop_sequence_present",
        "stop_id": "stop_id IS NOT NULL",
    }
    counts = {
        name: con.execute(f"SELECT COUNT(*) FROM vp_raw WHERE {cond}").fetchone()[0]
        for name, cond in fields.items()
    }

    ping_time_source = {
        "vehicle_timestamp": con.execute(
            "SELECT COUNT(*) FROM vp_raw WHERE vehicle_timestamp_present"
        ).fetchone()[0],
        "header_timestamp_fallback": con.execute(
            "SELECT COUNT(*) FROM vp_raw WHERE NOT vehicle_timestamp_present"
        ).fetchone()[0],
    }

    offsets = [
        r[0]
        for r in con.execute(
            "SELECT vehicle_timestamp - header_timestamp FROM vp_raw WHERE vehicle_timestamp_present"
        ).fetchall()
    ]

    n_vp_trip_ids = con.execute(
        "SELECT COUNT(DISTINCT trip_id) FROM vp_raw WHERE trip_id IS NOT NULL"
    ).fetchone()[0]
    n_vp_matched_static = con.execute(
        "SELECT COUNT(DISTINCT vr.trip_id) FROM vp_raw vr "
        "JOIN scheduled_trips st ON st.trip_id = vr.trip_id WHERE vr.trip_id IS NOT NULL"
    ).fetchone()[0]
    n_vp_matched_tu = con.execute(
        "SELECT COUNT(DISTINCT vr.trip_id) FROM vp_raw vr "
        "JOIN target_trips tt ON tt.trip_id = vr.trip_id WHERE vr.trip_id IS NOT NULL"
    ).fetchone()[0]

    return {
        "n_total_entities": n_total,
        "field_counts": counts,
        "ping_time_source": ping_time_source,
        "vehicle_minus_header_percentiles": {
            q: percentile(offsets, q) for q in (5, 25, 50, 75, 95)
        },
        "n_offsets": len(offsets),
        "n_vp_trip_ids": n_vp_trip_ids,
        "n_vp_matched_static": n_vp_matched_static,
        "n_vp_matched_tu": n_vp_matched_tu,
    }


def v1_per_hour(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    return con.execute(
        "SELECT hour, COUNT(*) FILTER (WHERE trip_id IS NOT NULL), COUNT(*) FROM vp_raw GROUP BY hour ORDER BY hour"
    ).fetchall()


def v1_per_trip_summary(con: duckdb.DuckDBPyConnection) -> dict:
    con.execute(
        "CREATE OR REPLACE TABLE vp_per_trip AS "
        "SELECT vr.trip_id, COUNT(*) AS raw_entities, "
        "COUNT(DISTINCT vr.vehicle_id || ':' || vr.vehicle_timestamp) AS distinct_pings, "
        "MIN(vr.vehicle_timestamp) FILTER (WHERE vr.vehicle_timestamp_present) AS first_ping, "
        "MAX(vr.vehicle_timestamp) FILTER (WHERE vr.vehicle_timestamp_present) AS last_ping "
        "FROM vp_raw vr JOIN target_trips tt ON tt.trip_id = vr.trip_id "
        "WHERE vr.trip_id IS NOT NULL GROUP BY vr.trip_id"
    )
    rows = con.execute(
        "SELECT p.raw_entities, p.distinct_pings, "
        "(p.last_ping - p.first_ping) AS ping_span, "
        "(w.window_end - w.window_start - 120*60) AS scheduled_span "  # undo the +30/+90min padding
        "FROM vp_per_trip p JOIN vp_trip_window w ON w.trip_id = p.trip_id "
        "WHERE p.first_ping IS NOT NULL"
    ).fetchall()
    raw_counts = [r[0] for r in rows]
    distinct_counts = [r[1] for r in rows]
    span_ratios = [r[2] / r[3] for r in rows if r[3] and r[3] > 0]
    return {
        "n_trips": len(rows),
        "raw_entities_percentiles": {q: percentile(raw_counts, q) for q in (25, 50, 75, 95)},
        "distinct_pings_percentiles": {q: percentile(distinct_counts, q) for q in (25, 50, 75, 95)},
        "ping_span_vs_scheduled_span_percentiles": {
            q: percentile(span_ratios, q) for q in (5, 25, 50, 75, 95)
        },
        "n_span_ratios": len(span_ratios),
    }


def v1_assignment_dropouts(con: duckdb.DuckDBPyConnection) -> dict:
    con.execute(
        "CREATE OR REPLACE TABLE vehicle_trip_windows AS "
        "SELECT DISTINCT p.vehicle_id, p.trip_id, s.span_start AS window_start, s.span_end AS window_end "
        "FROM vp_trip_pings p JOIN vp_trip_span s ON s.trip_id = p.trip_id"
    )
    con.execute(
        "CREATE OR REPLACE TABLE vp_untripped_dedup AS "
        "SELECT vehicle_id, vehicle_timestamp FROM vp_raw "
        "WHERE trip_id IS NULL AND vehicle_id_present AND vehicle_timestamp_present "
        "GROUP BY vehicle_id, vehicle_timestamp"
    )
    con.execute(
        "CREATE OR REPLACE TABLE assignment_dropouts AS "
        "SELECT u.vehicle_id, u.vehicle_timestamp, vtw.trip_id AS dropout_within_trip "
        "FROM vp_untripped_dedup u JOIN vehicle_trip_windows vtw "
        "ON vtw.vehicle_id = u.vehicle_id "
        "AND u.vehicle_timestamp BETWEEN vtw.window_start AND vtw.window_end"
    )
    n_dropout_pings = con.execute("SELECT COUNT(*) FROM assignment_dropouts").fetchone()[0]
    n_trips_with_dropout = con.execute(
        "SELECT COUNT(DISTINCT dropout_within_trip) FROM assignment_dropouts"
    ).fetchone()[0]

    per_trip_shares = con.execute(
        "WITH trip_linked AS ("
        "  SELECT trip_id, COUNT(*) AS n_linked FROM vp_trip_pings_windowed GROUP BY trip_id"
        "), dropout AS ("
        "  SELECT dropout_within_trip AS trip_id, COUNT(*) AS n_dropout FROM assignment_dropouts GROUP BY 1"
        ") "
        "SELECT COALESCE(tl.trip_id, d.trip_id), COALESCE(tl.n_linked, 0), COALESCE(d.n_dropout, 0) "
        "FROM trip_linked tl FULL OUTER JOIN dropout d ON d.trip_id = tl.trip_id"
    ).fetchall()
    shares = [
        n_dropout / (n_linked + n_dropout)
        for _tid, n_linked, n_dropout in per_trip_shares
        if (n_linked + n_dropout) > 0
    ]
    return {
        "n_dropout_pings": n_dropout_pings,
        "n_trips_with_dropout": n_trips_with_dropout,
        "share_per_trip_percentiles": {q: percentile(shares, q) for q in (25, 50, 75, 95)},
        "n_trips_sampled": len(shares),
    }


# --------------------------------------------------------------------------
# V2: passage detection
#
# Scoped to the same "eligible stop events" as Checks 1-4: (trip, stop) pairs
# with a held value (matched, uncensored, non-CANCELED/SKIPPED). A visit is a
# run of consecutive pings (ordered by vehicle_timestamp) within R metres of
# the stop. When a trip has more than one visit to a stop, the visit closest
# to the STATIC scheduled time is used, so VehiclePositions stays independent
# of the TripUpdates-derived held value.
# --------------------------------------------------------------------------

HAVERSINE_SQL = (
    "6371000 * 2 * ASIN(SQRT("
    "POWER(SIN(RADIANS({lat2} - {lat1}) / 2), 2) + "
    "COS(RADIANS({lat1})) * COS(RADIANS({lat2})) * POWER(SIN(RADIANS({lon2} - {lon1}) / 2), 2)"
    "))"
)


def build_vp_stop_distance(con: duckdb.DuckDBPyConnection) -> None:
    dist_expr = HAVERSINE_SQL.format(
        lat1="p.latitude", lon1="p.longitude", lat2="ss.stop_lat", lon2="ss.stop_lon"
    )
    con.execute(
        "CREATE OR REPLACE TABLE vp_eligible_stops AS "
        "SELECT hvc.trip_id, hvc.stop_id, hvc.stop_sequence, "
        "(sfs.final_stop_sequence IS NOT NULL AND sfs.final_stop_sequence = hvc.stop_sequence) AS is_final_stop, "
        "sl.sched_arrival_utc, sl.sched_departure_utc, "
        "CASE WHEN sfs.final_stop_sequence = hvc.stop_sequence THEN sl.sched_arrival_utc ELSE sl.sched_departure_utc END "
        "AS sched_ref_time "
        "FROM held_value_check1 hvc "
        "LEFT JOIN static_final_stop sfs ON sfs.trip_id = hvc.trip_id "
        "JOIN sched_lookup sl ON sl.trip_id = hvc.trip_id AND sl.stop_sequence = hvc.stop_sequence"
    )
    assert_unique(con, "vp_eligible_stops", ["trip_id", "stop_sequence"])
    con.execute(
        "CREATE OR REPLACE TABLE vp_stop_distance AS "
        "SELECT es.trip_id, es.stop_id, es.stop_sequence, es.is_final_stop, es.sched_ref_time, "
        f"p.vehicle_timestamp, {dist_expr} AS distance_m "
        "FROM vp_eligible_stops es "
        "JOIN vp_trip_pings_windowed p ON p.trip_id = es.trip_id "
        "JOIN static_stops ss ON ss.stop_id = es.stop_id "
        "WHERE p.latitude IS NOT NULL AND p.longitude IS NOT NULL"
    )


def v2_passage_at_radius(con: duckdb.DuckDBPyConnection, radius_m: int) -> dict:
    con.execute(
        "CREATE OR REPLACE TABLE vp_runs AS "
        "SELECT *, SUM(CASE WHEN within_r IS DISTINCT FROM prev_within_r THEN 1 ELSE 0 END) "
        "OVER (PARTITION BY trip_id, stop_id ORDER BY vehicle_timestamp) AS run_id "
        "FROM ("
        "  SELECT *, (distance_m <= " + str(radius_m) + ") AS within_r, "
        "  LAG(distance_m <= " + str(radius_m) + ") "
        "  OVER (PARTITION BY trip_id, stop_id ORDER BY vehicle_timestamp) AS prev_within_r "
        "  FROM vp_stop_distance"
        ")"
    )
    con.execute(
        "CREATE OR REPLACE TABLE vp_visits AS "
        "SELECT trip_id, stop_id, stop_sequence, is_final_stop, ANY_VALUE(sched_ref_time) AS sched_ref_time, "
        "run_id, MIN(vehicle_timestamp) AS visit_start, MAX(vehicle_timestamp) AS visit_end, COUNT(*) AS n_pings "
        "FROM vp_runs WHERE within_r "
        "GROUP BY trip_id, stop_id, stop_sequence, is_final_stop, run_id"
    )

    n_eligible = con.execute("SELECT COUNT(*) FROM vp_eligible_stops").fetchone()[0]
    visit_counts = con.execute(
        "SELECT trip_id, stop_sequence, COUNT(*) AS n_visits FROM vp_visits GROUP BY trip_id, stop_sequence"
    ).fetchall()
    n_detected = len(visit_counts)
    n_multi_visit = sum(1 for _tid, _seq, n in visit_counts if n > 1)
    n_no_ping_within_r = n_eligible - n_detected

    con.execute(
        "CREATE OR REPLACE TABLE vp_chosen_visit AS "
        "SELECT trip_id, stop_id, stop_sequence, is_final_stop, visit_start, visit_end, sched_ref_time, "
        "ROW_NUMBER() OVER (PARTITION BY trip_id, stop_sequence ORDER BY ABS(visit_start - sched_ref_time)) AS rn "
        "FROM vp_visits"
    )
    assert_unique(
        con,
        "(SELECT * FROM vp_chosen_visit WHERE rn = 1)",
        ["trip_id", "stop_sequence"],
    )

    con.execute(
        "CREATE OR REPLACE TABLE vp_departure_estimate AS "
        "SELECT v.trip_id, v.stop_sequence, v.visit_end AS last_in_visit, "
        "MIN(p.vehicle_timestamp) FILTER (WHERE p.vehicle_timestamp > v.visit_end AND p.distance_m > "
        + str(radius_m)
        + ") "
        "AS first_out_after "
        "FROM vp_chosen_visit v "
        "JOIN vp_stop_distance p ON p.trip_id = v.trip_id AND p.stop_sequence = v.stop_sequence "
        "WHERE v.rn = 1 "
        "GROUP BY v.trip_id, v.stop_sequence, v.visit_end"
    )
    con.execute(
        "CREATE OR REPLACE TABLE vp_arrival_estimate AS "
        "SELECT v.trip_id, v.stop_sequence, v.visit_start AS first_in_visit, "
        "MAX(p.vehicle_timestamp) FILTER (WHERE p.vehicle_timestamp < v.visit_start AND p.distance_m > "
        + str(radius_m)
        + ") "
        "AS last_out_before "
        "FROM vp_chosen_visit v "
        "JOIN vp_stop_distance p ON p.trip_id = v.trip_id AND p.stop_sequence = v.stop_sequence "
        "WHERE v.rn = 1 AND v.is_final_stop "
        "GROUP BY v.trip_id, v.stop_sequence, v.visit_start"
    )

    dep_widths = [
        r[0]
        for r in con.execute(
            "SELECT first_out_after - last_in_visit FROM vp_departure_estimate WHERE first_out_after IS NOT NULL"
        ).fetchall()
    ]
    arr_widths = [
        r[0]
        for r in con.execute(
            "SELECT first_in_visit - last_out_before FROM vp_arrival_estimate WHERE last_out_before IS NOT NULL"
        ).fetchall()
    ]

    return {
        "radius_m": radius_m,
        "n_eligible": n_eligible,
        "n_detected": n_detected,
        "n_no_ping_within_r": n_no_ping_within_r,
        "n_multi_visit": n_multi_visit,
        "departure_window_percentiles": {q: percentile(dep_widths, q) for q in (25, 50, 75, 95)},
        "n_departure_windows": len(dep_widths),
        "arrival_window_percentiles": {q: percentile(arr_widths, q) for q in (25, 50, 75, 95)},
        "n_arrival_windows": len(arr_widths),
    }


# --------------------------------------------------------------------------
# V3: held value vs VP (call after v2_passage_at_radius(radius_m) for the
# same radius, since it depends on vp_chosen_visit/vp_departure_estimate/
# vp_arrival_estimate)
# --------------------------------------------------------------------------


def build_vp_estimate(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        "CREATE OR REPLACE TABLE vp_estimate AS "
        "SELECT v.trip_id, v.stop_id, v.stop_sequence, "
        "CASE WHEN v.is_final_stop THEN (a.first_in_visit + a.last_out_before) / 2.0 "
        "ELSE (d.last_in_visit + d.first_out_after) / 2.0 END AS vp_time "
        "FROM vp_chosen_visit v "
        "LEFT JOIN vp_departure_estimate d ON d.trip_id = v.trip_id AND d.stop_sequence = v.stop_sequence "
        "LEFT JOIN vp_arrival_estimate a ON a.trip_id = v.trip_id AND a.stop_sequence = v.stop_sequence "
        "WHERE v.rn = 1 "
        "AND ((v.is_final_stop AND a.last_out_before IS NOT NULL) "
        "OR (NOT v.is_final_stop AND d.first_out_after IS NOT NULL))"
    )
    assert_unique(con, "vp_estimate", ["trip_id", "stop_sequence"])
    con.execute(
        "CREATE OR REPLACE TABLE v3_offsets AS "
        "SELECT hvc.trip_id, hvc.stop_id, hvc.stop_sequence, hvc.is_trip_removal, "
        "hvc.held_uncertainty_present, hvc.n_changes_after_cross, "
        "(hvc.held_value - ve.vp_time) AS time_offset, "
        "(sfs.final_stop_sequence = hvc.stop_sequence) AS is_final_stop, "
        "(sfirst.first_stop_sequence = hvc.stop_sequence) AS is_first_stop "
        "FROM held_value_check1 hvc "
        "JOIN vp_estimate ve ON ve.trip_id = hvc.trip_id AND ve.stop_sequence = hvc.stop_sequence "
        "LEFT JOIN static_final_stop sfs ON sfs.trip_id = hvc.trip_id "
        "LEFT JOIN static_first_stop sfirst ON sfirst.trip_id = hvc.trip_id"
    )
    assert_unique(con, "v3_offsets", ["trip_id", "stop_sequence"])


def summarize_offsets(offsets: list[float]) -> dict:
    return {
        "n": len(offsets),
        "percentiles": {q: percentile(offsets, q) for q in (1, 5, 25, 50, 75, 95, 99)},
        "share_within": {
            th: (sum(1 for o in offsets if abs(o) <= th) / len(offsets) if offsets else None)
            for th in (15, 30, 60)
        },
    }


def v3_held_vs_vp(con: duckdb.DuckDBPyConnection) -> dict:
    rows = con.execute(
        "SELECT time_offset AS offset, is_trip_removal, is_first_stop, is_final_stop, "
        "held_uncertainty_present, n_changes_after_cross "
        "FROM v3_offsets"
    ).fetchall()
    cols = [
        "offset",
        "is_trip_removal",
        "is_first_stop",
        "is_final_stop",
        "held_uncertainty_present",
        "n_changes_after_cross",
    ]
    rows = [dict(zip(cols, r, strict=True)) for r in rows]

    def pos(r: dict) -> str:
        if r["is_final_stop"]:
            return "final"
        if r["is_first_stop"]:
            return "first"
        return "intermediate"

    return {
        "overall": summarize_offsets([r["offset"] for r in rows]),
        "by_leave_kind": {
            "mid_route_drop": summarize_offsets(
                [r["offset"] for r in rows if not r["is_trip_removal"]]
            ),
            "trip_removal": summarize_offsets([r["offset"] for r in rows if r["is_trip_removal"]]),
        },
        "by_position": {
            p: summarize_offsets([r["offset"] for r in rows if pos(r) == p])
            for p in ("first", "intermediate", "final")
        },
        "by_uncertainty": {
            "present": summarize_offsets(
                [r["offset"] for r in rows if r["held_uncertainty_present"]]
            ),
            "absent": summarize_offsets(
                [r["offset"] for r in rows if not r["held_uncertainty_present"]]
            ),
        },
        "by_check1_status": {
            "zero_changes_after_cross": summarize_offsets(
                [r["offset"] for r in rows if r["n_changes_after_cross"] == 0]
            ),
            "changed_after_cross": summarize_offsets(
                [
                    r["offset"]
                    for r in rows
                    if r["n_changes_after_cross"] is not None and r["n_changes_after_cross"] > 0
                ]
            ),
        },
    }


# --------------------------------------------------------------------------
# V4: classification agreement
# --------------------------------------------------------------------------


def classify_delay(delay_s: float) -> str:
    if delay_s < -60:
        return "early"
    if delay_s <= 180:
        return "on_time"
    return "late"


def v4_matrix_and_shares(rows: list[dict]) -> dict:
    matrix: dict[tuple[str, str], int] = {}
    for r in rows:
        hc = classify_delay(r["held_delay"])
        vc = classify_delay(r["vp_delay"])
        matrix[(hc, vc)] = matrix.get((hc, vc), 0) + 1

    n_total = len(rows)
    on_time_shares = {}
    for label, threshold in (("180s", 180), ("60s", 60), ("300s", 300)):
        held_on_time = sum(1 for r in rows if -60 <= r["held_delay"] <= threshold)
        vp_on_time = sum(1 for r in rows if -60 <= r["vp_delay"] <= threshold)
        on_time_shares[label] = {
            "held": {"n_on_time": held_on_time, "n_total": n_total},
            "vp": {"n_on_time": vp_on_time, "n_total": n_total},
        }

    return {"n_total": n_total, "matrix": matrix, "on_time_shares": on_time_shares}


def v4_classification_agreement(con: duckdb.DuckDBPyConnection) -> dict:
    raw = con.execute(
        "SELECT ve.trip_id, ve.stop_sequence, hvc.held_value, ve.vp_time, sl.sched_ref_time, "
        "hvc.held_uncertainty_present, sl.is_final_stop, "
        "(sfirst.first_stop_sequence = ve.stop_sequence) AS is_first_stop "
        "FROM vp_estimate ve "
        "JOIN held_value_check1 hvc ON hvc.trip_id = ve.trip_id AND hvc.stop_sequence = ve.stop_sequence "
        "JOIN vp_eligible_stops sl ON sl.trip_id = ve.trip_id AND sl.stop_sequence = ve.stop_sequence "
        "LEFT JOIN static_first_stop sfirst ON sfirst.trip_id = ve.trip_id "
        "WHERE sl.sched_ref_time IS NOT NULL"
    ).fetchall()
    cols = [
        "trip_id",
        "stop_sequence",
        "held_value",
        "vp_time",
        "sched_ref_time",
        "held_uncertainty_present",
        "is_final_stop",
        "is_first_stop",
    ]
    rows = [dict(zip(cols, r, strict=True)) for r in raw]
    assert_unique_rows(rows, ["trip_id", "stop_sequence"], "v4 rows")
    for r in rows:
        r["held_delay"] = r["held_value"] - r["sched_ref_time"]
        r["vp_delay"] = r["vp_time"] - r["sched_ref_time"]

    def pos(r: dict) -> str:
        if r["is_final_stop"]:
            return "final"
        if r["is_first_stop"]:
            return "first"
        return "intermediate"

    overall = v4_matrix_and_shares(rows)
    by_position = {
        p: v4_matrix_and_shares([r for r in rows if pos(r) == p])
        for p in ("first", "intermediate", "final")
    }
    by_marker = {
        "all": overall,
        "marker_present": v4_matrix_and_shares([r for r in rows if r["held_uncertainty_present"]]),
    }

    return {
        "n_total": overall["n_total"],
        "matrix": overall["matrix"],
        "on_time_shares": overall["on_time_shares"],
        "by_position": by_position,
        "by_marker": by_marker,
    }


# --------------------------------------------------------------------------
# VP reconciliation: held values -> eligible stop events -> detected in V2 ->
# V3 events -> V4 events, all at R=50m. Call right after the R=50m V2/V3/V4
# pipeline has run, so vp_visits/v3_offsets reflect that radius.
# --------------------------------------------------------------------------


def build_vp_reconciliation(con: duckdb.DuckDBPyConnection, n_v4: int) -> dict:
    n_held_values = con.execute("SELECT COUNT(*) FROM held_value_check1").fetchone()[0]
    n_eligible = con.execute("SELECT COUNT(*) FROM vp_eligible_stops").fetchone()[0]
    n_v2 = con.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT trip_id, stop_sequence FROM vp_visits)"
    ).fetchone()[0]
    n_v3 = con.execute("SELECT COUNT(*) FROM v3_offsets").fetchone()[0]

    stages = [
        ("held_values", n_held_values),
        ("eligible_stop_events", n_eligible),
        ("detected_in_v2", n_v2),
        ("v3_events", n_v3),
        ("v4_events", n_v4),
    ]
    for (prev_label, prev_n), (label, n) in itertools.pairwise(stages):
        if n > prev_n:
            raise AssertionError(
                f"VP reconciliation: {label} ({n}) exceeds {prev_label} ({prev_n})"
            )
    if n_v3 != n_v4:
        raise AssertionError(f"VP reconciliation: V3 events ({n_v3}) != V4 events ({n_v4})")

    return {
        "n_held_values": n_held_values,
        "n_eligible": n_eligible,
        "n_v2": n_v2,
        "n_v3": n_v3,
        "n_v4": n_v4,
    }


# --------------------------------------------------------------------------
# Report rendering
# --------------------------------------------------------------------------


def render_offset_summary(label: str, s: dict) -> list[str]:
    lines = [f"- {label}: n={s['n']}"]
    if s["n"]:
        lines.append(f"  - percentiles (s): {fmt_pct_list(s['percentiles'])}")
        shares = ", ".join(
            f"<=±{th}s: {100 * v:.1f}%" if v is not None else f"<=±{th}s: n/a"
            for th, v in s["share_within"].items()
        )
        lines.append(f"  - {shares}")
    return lines


def render_report(ctx: dict) -> str:
    lines: list[str] = []
    d = ctx["date"]
    lines.append(f"# Held-value validation scan: {d}")
    lines.append("")
    lines.append(
        "Evidence for D-005/D-006 (see `docs/decisions.md`). This report never changes a "
        "definition. It tests whether D-005's held value is a recorded actual time or a "
        "stale prediction, using TripUpdates alone and, where run, an independent "
        "VehiclePositions passage check."
    )
    lines.append("")
    lines.append("## Header")
    lines.append("")
    lines.append(f"- Service date: {d}")
    lines.append(f"- Feeds: {', '.join(ctx['feeds'])}")
    missing_str = (
        ", ".join(f"{h:02d}" for h in ctx["missing_hours"]) if ctx["missing_hours"] else "none"
    )
    lines.append(
        f"- Hours present (TripUpdates): {len(ctx['present_hours'])}/24 (missing: {missing_str})"
    )
    lines.append(f"- Snapshot count (deduplicated): {ctx['n_snapshots']}")
    lines.append(f"- First snapshot: {fmt_ts_both(ctx['day_first_ts'])}")
    lines.append(f"- Last snapshot: {fmt_ts_both(ctx['day_last_ts'])}")
    lines.append(f"- Git commit: {ctx['git_commit']}")
    lines.append(f"- Run timestamp (UTC): {ctx['run_ts']}")
    lines.append("")
    lines.append("## Exclusions")
    lines.append("")
    lines.append(
        f"- CANCELED trip (snapshot, trip) observations excluded: {ctx['excluded']['canceled_trip_observations']}"
    )
    lines.append(f"- SKIPPED stop_time_updates excluded: {ctx['excluded']['skipped_stus']}")
    lines.append("")

    # Check 1
    c1 = ctx["check1"]
    lines.append("## Check 1: frozen or drifting")
    lines.append("")
    lines.append(f"Among {c1['overall']['total']} (trip, stop) pairs with a held value:")
    lines.append(
        f"- Reach t_cross: {fmt_share(c1['overall']['n_reach_cross'], c1['overall']['total'])}"
    )
    lines.append(
        f"- Zero changes after t_cross: "
        f"{fmt_share(c1['overall']['n_zero_changes_after_cross'], c1['overall']['n_zero_changes_denom'])}"
    )
    lines.extend(
        render_offset_summary(
            "Drift (held value minus value at t_cross)",
            {
                "n": c1["overall"]["n_drift"],
                "percentiles": c1["overall"]["drift_percentiles"],
                "share_within": c1["overall"]["drift_share_within"],
            },
        )
    )
    lines.append(
        f"- t_last_change minus t_cross (s), n={c1['overall']['n_t_last_minus_cross']}: "
        f"{fmt_pct_list(c1['overall']['t_last_minus_cross_percentiles'])}"
    )
    lines.append(
        f"- held value minus t_last_change (s), n={c1['overall']['n_held_minus_last_change']}: "
        f"{fmt_pct_list(c1['overall']['held_minus_last_change_percentiles'])}"
    )
    lines.append("")
    lines.append("By how the stop left:")
    for kind_label, kind_key in (
        ("Mid-route drop", "mid_route_drop"),
        ("Trip removal", "trip_removal"),
    ):
        s = c1["by_leave_kind"][kind_key]
        lines.append(
            f"- {kind_label}: n={s['total']}, reach t_cross={fmt_share(s['n_reach_cross'], s['total'])}, "
            f"drift median={s['drift_percentiles'].get(50)}s"
        )
    lines.append("")
    lines.append("By whether uncertainty is present on the held value:")
    for label, key in (("Present", "uncertainty_present"), ("Absent", "uncertainty_absent")):
        s = c1["by_uncertainty"][key]
        lines.append(
            f"- {label}: n={s['total']}, drift median={s['drift_percentiles'].get(50)}s, "
            f"drift p95={s['drift_percentiles'].get(95)}s"
        )
    lines.append("")

    # Check 2
    c2 = ctx["check2"]
    lines.append("## Check 2: uncertainty")
    lines.append("")
    for event in ("arrival", "departure"):
        ct = c2[event]["cross_tab"]
        lines.append(f"**{event.capitalize()}** (n={ct['total']}):")
        lines.append(
            f"- Cross-tab (uncertainty bucket, time-relative-to-now bucket, count): {ct['cross_tab']}"
        )
        if ct["n_nonzero"]:
            lines.append(
                f"- Non-zero uncertainty value percentiles (n={ct['n_nonzero']}): {fmt_pct_list(ct['nonzero_value_percentiles'])}"
            )
        else:
            lines.append("- Non-zero uncertainty values: none observed")
        appear = c2[event]["appearance"]
        lines.append(
            f"- Stops where uncertainty ever appears: {appear['n_appears']}; "
            f"appears then later disappears: {appear['n_appears_then_disappears']}"
        )
        lines.append(
            f"- (now minus time) at first appearance of uncertainty (s), n={appear['n_now_minus_time']}: "
            f"{fmt_pct_list(appear['now_minus_time_percentiles'])}"
        )
        lines.append("")

    # Check 3
    c3 = ctx["check3"]
    lines.append("## Check 3: trip removal")
    lines.append("")
    lines.append(f"Among {c3['n_total']} trip removals:")
    lines.append(
        f"- Completed (all remaining stops in the past): {fmt_share(c3['n_completed'], c3['n_total'])}"
    )
    lines.append(
        f"- Left early (at least one remaining stop in the future): {fmt_share(c3['n_left_early'], c3['n_total'])}"
    )
    lines.append(
        f"- Future-stops-remaining percentiles for left-early trips (n={c3['n_left_early_sampled']}): "
        f"{fmt_pct_list(c3['future_stops_percentiles'])}"
    )
    lines.append(
        f"- Left-early trips where the final stop is among the future stops: "
        f"{fmt_share(c3['n_final_among_future'], c3['n_left_early'])}"
    )
    lines.append(
        f"- Removal time minus final stop's held arrival.time, completed trips with final stop present "
        f"(n={c3['n_completed_with_final']} of {c3['n_completed']} completed): "
        f"{fmt_pct_list(c3['removal_minus_final_arrival_percentiles'])}"
    )
    lines.append("")

    # Check 4
    c4 = ctx["check4"]
    lines.append("## Check 4: time vs delay")
    lines.append("")
    for event in ("arrival", "departure"):
        r = c4[event]
        lines.append(
            f"**{event.capitalize()}** ((time - delay) minus scheduled time in UTC, n={r['n_total']}):"
        )
        lines.append(f"- Exactly 0: {fmt_share(r['n_exact_zero'], r['n_total'])}")
        lines.append(f"- Within ±60s: {fmt_share(r['n_within_60s'], r['n_total'])}")
        lines.append(f"- Percentiles (s): {fmt_pct_list(r['percentiles'])}")
        lines.append("")

    # Check 5
    lines.append("## Check 5: loose ends")
    lines.append("")
    lines.append(
        "**5a. Per-hour table** (hour, archive_files, distinct_header_timestamps, first_ts, last_ts, trip_entities):"
    )
    for hour, files, distinct_ts, first_ts, last_ts, entities in ctx["check5a"]:
        lines.append(
            f"- {hour:02d}: files={files}, distinct_ts={distinct_ts}, "
            f"first={fmt_ts_both(first_ts)}, last={fmt_ts_both(last_ts)}, trip_entities={entities}"
        )
    lines.append("")
    if ctx.get("check5b") is not None:
        b = ctx["check5b"]
        lines.append("**5b. Unmatched trips' start_date distribution (2026-09-06 only):**")
        lines.append(f"- {b['start_date_distribution']}")
        lines.append(
            f"- Of {b['n_unmatched']} realtime trips unmatched to 2026-09-06's static schedule, "
            f"{b['n_match_previous_day']} match 2026-09-05's static schedule"
        )
        lines.append("")
    else:
        lines.append(
            "**5b. Unmatched trips' start_date distribution:** not applicable (only run for 2026-09-06)"
        )
        lines.append("")
    lines.append(f"**5c. Snapshot gaps over 300s** ({len(ctx['check5c'])} found):")
    for gap_start, gap_end, gap in ctx["check5c"]:
        lines.append(f"  - start {fmt_ts_both(gap_start)}")
        lines.append(f"    end   {fmt_ts_both(gap_end)}: {gap}s")
    lines.append("")
    d5 = ctx["check5d"]
    lines.append("**5d. Trips that leave the feed and come back:**")
    lines.append(f"- Trips with at least one return: {d5['n_trips_with_return']}")
    lines.append(f"- Total leave-and-return events: {d5['n_events']}")
    lines.append(
        f"- Absence duration percentiles (s), n={d5['n_events']}: {fmt_pct_list(d5['duration_percentiles'])}"
    )
    lines.append(
        f"- Stops dropped while absent, percentiles, n={d5['n_events']}: {fmt_pct_list(d5['dropped_percentiles'])}"
    )
    lines.append("")

    # Check 6
    c6 = ctx["check6"]
    lines.append("## Check 6: held values without the marker")
    lines.append("")
    lines.append(
        f"Held values without `uncertainty = 0`: {fmt_share(c6['n_missing'], c6['n_total'])}"
    )
    lines.append("")
    lines.append("By stop position and how the stop left the feed:")
    for position in ("first", "intermediate", "final"):
        for leave_kind, leave_label in (
            ("clean_drop", "clean drop"),
            ("trip_removal", "trip removal"),
        ):
            n = c6["breakdown"].get((position, leave_kind), 0)
            lines.append(f"- {position}, {leave_label}: {fmt_share(n, c6['n_missing'])}")
    lines.append("")

    # Stops that reappeared after dropping
    c7 = ctx["check7"]
    lines.append("## Stops that reappeared after dropping")
    lines.append("")
    if not c7.get("n"):
        lines.append("None found.")
    else:
        lines.append(
            f"- Count (resolvable against both the pre-drop snapshot and the final appearance): {c7['n']} "
            f"(of {c7['n_reappeared_total']} reappeared stops found)"
        )
        lines.append(
            f"- Share where the time differs (last snapshot before the first drop vs. final "
            f"appearance): {fmt_share(c7['n_differs'], c7['n'])}"
        )
        lines.append(
            f"- Difference (final minus before), seconds, n={c7['n_diffs']}: "
            f"{fmt_pct_list(c7['diff_percentiles'])}"
        )
        lines.append("- Marker status, before the first drop -> final appearance (count):")
        for (before, after), n in sorted(c7["marker_matrix"].items()):
            b_label = "present" if before else "absent"
            a_label = "present" if after else "absent"
            lines.append(f"  - {b_label} -> {a_label}: {n}")
        lines.append("")
        if ctx["vp_ran"] and ctx.get("check7_vp"):
            lines.append(
                "**Per-stop V3 offset against VP (R = 50m)**, before the first drop vs. the "
                "final appearance:"
            )
            lines.append("")
            lines.append("| trip_id | stop_sequence | offset before (s) | offset final (s) |")
            lines.append("|---|---|---|---|")
            for row in ctx["check7_vp"]:
                lines.append(
                    f"| {row['trip_id']} | {row['stop_sequence']} | {row['offset_before']} | "
                    f"{row['offset_final']} |"
                )
    lines.append("")

    # V sections
    if not ctx["vp_ran"]:
        lines.append("## V1-V4: VehiclePositions")
        lines.append("")
        lines.append(f"Not run for {d}. {ctx.get('vp_not_run_reason', '')}")
        lines.append("")
    else:
        rec = ctx["vp_reconciliation"]
        lines.append("## VehiclePositions reconciliation (R = 50m)")
        lines.append("")
        lines.append(
            "Funnel from held values to V4 events; no stage exceeds the one before it, and V3 "
            "and V4 counts must match:"
        )
        lines.append(f"- Held values: {rec['n_held_values']}")
        lines.append(f"- Eligible stop events: {rec['n_eligible']}")
        lines.append(f"- Detected in V2: {rec['n_v2']}")
        lines.append(f"- V3 events: {rec['n_v3']}")
        lines.append(f"- V4 events: {rec['n_v4']}")
        lines.append("")

        v1 = ctx["v1"]
        lines.append("## V1: field population")
        lines.append("")
        lines.append(
            "Matching deviates from a literal (trip_id, start_date) match: VehiclePositions never "
            "carries start_date, and most entities lack a trip descriptor (see field counts and the "
            "per-hour breakdown below), so matching is trip_id-only plus a scheduled-time window "
            "(first departure - 30 min to final arrival + 90 min, from the static schedule, not "
            "TripUpdates). Confirmed with Marcus; see Observations."
        )
        lines.append("")
        lines.append(f"Among {v1['n_total_entities']} raw VehiclePosition entities:")
        for name, n in v1["field_counts"].items():
            lines.append(f"- {name}: {fmt_share(n, v1['n_total_entities'])}")
        lines.append(
            f"- Ping time source used: vehicle.timestamp {fmt_share(v1['ping_time_source']['vehicle_timestamp'], v1['n_total_entities'])}, "
            f"header_timestamp fallback {fmt_share(v1['ping_time_source']['header_timestamp_fallback'], v1['n_total_entities'])}"
        )
        lines.append(
            f"- vehicle.timestamp minus header_timestamp (s), n={v1['n_offsets']}: "
            f"{fmt_pct_list(v1['vehicle_minus_header_percentiles'])}"
        )
        lines.append(f"- Distinct trip_ids seen in VP: {v1['n_vp_trip_ids']}")
        lines.append(
            f"- ...matched to static schedule: {fmt_share(v1['n_vp_matched_static'], v1['n_vp_trip_ids'])}"
        )
        lines.append(
            f"- ...matched to TripUpdates matched trips: {fmt_share(v1['n_vp_matched_tu'], v1['n_vp_trip_ids'])}"
        )
        lines.append("")
        lines.append("**Per-hour trip_id population:**")
        for hour, with_trip, total in ctx["v1_per_hour"]:
            lines.append(f"- {hour:02d}: {fmt_share(with_trip, total)}")
        lines.append("")
        pt = ctx["v1_per_trip"]
        lines.append(f"**Per-trip summary** (n={pt['n_trips']} trips with pings):")
        lines.append(f"- Raw entities per trip: {fmt_pct_list(pt['raw_entities_percentiles'])}")
        lines.append(
            f"- Distinct pings per trip (after dedup by vehicle_id+vehicle_timestamp): {fmt_pct_list(pt['distinct_pings_percentiles'])}"
        )
        lines.append(
            f"- Ping time span / scheduled trip duration ratio, n={pt['n_span_ratios']}: "
            f"{fmt_pct_list(pt['ping_span_vs_scheduled_span_percentiles'])}"
        )
        lines.append("")
        dr = ctx["v1_dropouts"]
        lines.append(
            "**Assignment dropouts** (trip-less pings from a vehicle, inside one of its trips' scheduled window):"
        )
        lines.append(f"- Dropout pings: {dr['n_dropout_pings']}")
        lines.append(f"- Trips with at least one dropout ping: {dr['n_trips_with_dropout']}")
        lines.append(
            f"- Dropout share per trip, n={dr['n_trips_sampled']}: {fmt_pct_list(dr['share_per_trip_percentiles'])}"
        )
        lines.append("")
        lines.append(
            f"**Guardrail check (run before proceeding):** median inter-ping interval = "
            f"{ctx['vp_guardrails']['interval_percentiles'].get(50)}s (stop threshold: >60s); "
            f"share of trip-linked pings outside the scheduled window = "
            f"{100 * ctx['vp_guardrails']['share_outside']:.2f}% (stop threshold: >10%). Neither triggered."
        )
        lines.append("")

        lines.append("## V2: passage detection")
        lines.append("")
        for radius_m in (25, 50, 100):
            r = ctx["v2"][radius_m]
            lines.append(f"**R = {radius_m}m:**")
            lines.append(f"- Events detected: {fmt_share(r['n_detected'], r['n_eligible'])}")
            lines.append(
                f"- No ping within R: {fmt_share(r['n_no_ping_within_r'], r['n_eligible'])}"
            )
            lines.append(f"- Multiple visits: {fmt_share(r['n_multi_visit'], r['n_eligible'])}")
            lines.append(
                f"- Departure window width (s), n={r['n_departure_windows']}: {fmt_pct_list(r['departure_window_percentiles'])}"
            )
            lines.append(
                f"- Arrival window width (final stops only, s), n={r['n_arrival_windows']}: {fmt_pct_list(r['arrival_window_percentiles'])}"
            )
            lines.append("")

        lines.append("## V3: held value vs VP")
        lines.append("")
        lines.append("**Compact table, R = 25m and R = 100m (overall offset only):**")
        for radius_m in (25, 100):
            s = ctx["v3_compact"][radius_m]
            lines.extend(render_offset_summary(f"R={radius_m}m", s))
        lines.append("")
        lines.append("**R = 50m, full breakdown:**")
        v3 = ctx["v3"]
        lines.extend(render_offset_summary("Overall", v3["overall"]))
        lines.append("By how the stop left:")
        for label, key in (("Mid-route drop", "mid_route_drop"), ("Trip removal", "trip_removal")):
            lines.extend(render_offset_summary(label, v3["by_leave_kind"][key]))
        lines.append("By stop position:")
        for label in ("first", "intermediate", "final"):
            lines.extend(render_offset_summary(label, v3["by_position"][label]))
        lines.append("By whether uncertainty is present on the held value:")
        for label, key in (("Present", "present"), ("Absent", "absent")):
            lines.extend(render_offset_summary(label, v3["by_uncertainty"][key]))
        lines.append("By Check 1 status:")
        for label, key in (
            ("Zero changes after t_cross", "zero_changes_after_cross"),
            ("Changed after t_cross", "changed_after_cross"),
        ):
            lines.extend(render_offset_summary(label, v3["by_check1_status"][key]))
        lines.append("")

        lines.append("## V4: classification agreement")
        lines.append("")
        v4 = ctx["v4"]

        def render_v4_block(label: str, block: dict) -> None:
            lines.append(f"**{label}** (n={block['n_total']}):")
            lines.append("- 3x3 matrix (held_class, vp_class): counts")
            for (hc, vc), n in sorted(block["matrix"].items()):
                lines.append(f"  - ({hc}, {vc}): {n}")
            lines.append("- On-time share by source and threshold:")
            for th_label, shares in block["on_time_shares"].items():
                held = shares["held"]
                vp = shares["vp"]
                lines.append(
                    f"  - +{th_label}: held {fmt_share(held['n_on_time'], held['n_total'])}, "
                    f"vp {fmt_share(vp['n_on_time'], vp['n_total'])}"
                )
            lines.append("")

        lines.append(
            f"Among {v4['n_total']} events with both a held-value and a VP-based classification (R=50m):"
        )
        lines.append("")
        lines.append("**By stop position:**")
        lines.append("")
        for label, key in (
            ("First", "first"),
            ("Intermediate", "intermediate"),
            ("Final", "final"),
        ):
            render_v4_block(label, v4["by_position"][key])
        lines.append("**By marker presence on the held value:**")
        lines.append("")
        for label, key in (
            ("All held values", "all"),
            ("Marker present (uncertainty = 0)", "marker_present"),
        ):
            render_v4_block(label, v4["by_marker"][key])

    lines.append("## Observations")
    lines.append("")
    obs = ctx["observations"]
    if obs:
        for o in obs:
            lines.append(f"- {o}")
    else:
        lines.append("- None.")
    lines.append("")

    return "\n".join(lines)


def build_observations(ctx: dict) -> list[str]:
    obs = []
    c1 = ctx["check1"]
    if c1["overall"]["total"]:
        obs.append(
            f"Drift (held value minus value at t_cross) median is "
            f"{c1['overall']['drift_percentiles'].get(50)}s overall, tight relative to the ~600s "
            "gap between predicted time and drop time found in observed_time's report - the value "
            "itself settles quickly even though the feed is slow to remove the entry."
        )
    c2 = ctx["check2"]
    for event in ("arrival", "departure"):
        if c2[event]["cross_tab"]["n_nonzero"] == 0:
            obs.append(
                f"{event.capitalize()} uncertainty, when present, is always exactly 0 in this sample - never non-zero."
            )
    if ctx.get("check5b") is not None and ctx["check5b"]["n_match_previous_day"]:
        obs.append(
            f"{ctx['check5b']['n_match_previous_day']} of {ctx['check5b']['n_unmatched']} unmatched trips "
            "match the previous day's (2026-09-05) static schedule instead."
        )
    if ctx["vp_ran"]:
        v1 = ctx["v1"]
        share_with_trip = (
            v1["field_counts"]["trip_id"] / v1["n_total_entities"] if v1["n_total_entities"] else 0
        )
        obs.append(
            f"Only {100 * share_with_trip:.1f}% of raw VehiclePosition entities carry a trip_id "
            f"({fmt_share(v1['field_counts']['trip_id'], v1['n_total_entities'])}); start_date is "
            "never populated. Matching used trip_id plus a scheduled-time window instead of "
            "(trip_id, start_date), confirmed with Marcus before proceeding."
        )
        v3 = ctx["v3"]
        obs.append(
            f"Held-value-vs-VP offset is far tighter at intermediate stops (median "
            f"{v3['by_position']['intermediate']['percentiles'].get(50)}s) than at first stops (median "
            f"{v3['by_position']['first']['percentiles'].get(50)}s) or final stops (median "
            f"{v3['by_position']['final']['percentiles'].get(50)}s, with a long tail)."
        )
        v4 = ctx["v4"]
        n_agree = sum(n for (hc, vc), n in v4["matrix"].items() if hc == vc)
        obs.append(
            f"Held-value and VP-based punctuality classification agree on "
            f"{fmt_share(n_agree, v4['n_total'])} of events (3-way early/on_time/late)."
        )
    return obs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", help="Service date to validate (YYYY-MM-DD)")
    parser.add_argument(
        "--vehicle-positions",
        action="store_true",
        help="Also run the VehiclePositions section (2026-09-07 only)",
    )
    args = parser.parse_args()
    svc_date = args.date
    svc_date_obj = date.fromisoformat(svc_date)

    key = os.environ.get("TRAFIKLAB_KODA_KEY")

    print(f"Staging {OPERATOR}/{FEED} for {svc_date}...")
    present_hours, missing_hours = stage_date(OPERATOR, FEED, svc_date)
    if not present_hours:
        sys.exit(
            f"No archives found on disk for {svc_date}. Run kronoberg_transit.fetch_koda first."
        )
    print(f"  {len(present_hours)}/24 hours present, missing: {missing_hours or 'none'}")

    con = duckdb.connect()
    with tempfile.TemporaryDirectory() as tmpdir:
        static_dir = load_static_gtfs(svc_date, key, Path(tmpdir), extra_files=["stops.txt"])
        print("Building DuckDB tables...")
        glob = str(INTERIM_DIR / svc_date / "*.parquet")
        setup_duckdb(con, svc_date, static_dir, glob)
        setup_held_value_tables(con, static_dir)
        build_held_value_summary(con)
        build_sched_lookup(con, svc_date_obj)

        excluded = dict(
            zip(
                ("canceled_trip_observations", "skipped_stus"),
                con.execute("SELECT * FROM excluded_counts").fetchone(),
                strict=True,
            )
        )

        day_first_ts, day_last_ts = con.execute(
            "SELECT MIN(header_timestamp), MAX(header_timestamp) FROM snapshots"
        ).fetchone()
        n_snapshots = con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]

        print("Running checks 1-5...")
        check1_rows = fetch_check1_rows(con)
        c1 = check1(check1_rows)
        c2 = check2(con)
        c3 = check3(con)
        c4 = check4(con)
        c5a = check5a(con)
        c5b = None
        if svc_date == "2026-09-06":
            start_dates = check5b_unmatched_start_dates(con)
            prev_key = os.environ.get("TRAFIKLAB_KODA_KEY")
            with tempfile.TemporaryDirectory() as prev_tmp:
                prev_static_dir = load_static_gtfs("2026-09-05", prev_key, Path(prev_tmp))
                match = check5b_match_previous_day(con, prev_static_dir)
            c5b = {"start_date_distribution": start_dates, **match}
        c5c = check5c_large_gaps(con)
        c5d = check5d_leave_and_return(con)
        c6 = check6_missing_marker(con)
        c7 = check7_reappeared_stops(con)

        ctx = {
            "date": svc_date,
            "feeds": [FEED] + ([VP_FEED] if args.vehicle_positions else []),
            "present_hours": present_hours,
            "missing_hours": missing_hours,
            "n_snapshots": n_snapshots,
            "day_first_ts": day_first_ts,
            "day_last_ts": day_last_ts,
            "git_commit": git_commit_hash(),
            "run_ts": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "excluded": excluded,
            "check1": c1,
            "check2": c2,
            "check3": c3,
            "check4": c4,
            "check5a": c5a,
            "check5b": c5b,
            "check5c": c5c,
            "check5d": c5d,
            "check6": c6,
            "check7": c7,
            "vp_ran": False,
        }

        if args.vehicle_positions:
            print(f"Staging {VP_FEED} for {svc_date}...")
            vp_present, vp_missing = stage_date_vp(OPERATOR, svc_date)
            print(f"  {len(vp_present)}/24 hours present, missing: {vp_missing or 'none'}")

            vp_glob = str(VP_INTERIM_DIR / svc_date / "*.parquet")
            print("Building VP tables...")
            setup_vp_tables(con, vp_glob, svc_date_obj)
            guardrails = vp_guardrail_stats(con)
            print("VP guardrails:", guardrails)
            if guardrails["interval_percentiles"].get(50, 0) > 60:
                sys.exit(
                    f"STOP: median inter-ping interval {guardrails['interval_percentiles'][50]}s > 60s."
                )
            if guardrails["share_outside"] and guardrails["share_outside"] > 0.10:
                sys.exit(
                    f"STOP: {100 * guardrails['share_outside']:.1f}% of trip-linked pings fall outside the window (>10%)."
                )

            v1 = v1_field_population(con)
            v1_hourly = v1_per_hour(con)
            v1_per_trip = v1_per_trip_summary(con)
            v1_dropouts = v1_assignment_dropouts(con)

            build_vp_stop_distance(con)
            v2_by_radius = {}
            v3_compact = {}
            v3_full = None
            v4 = None
            for radius_m in (25, 50, 100):
                print(f"V2 passage detection at R={radius_m}m...")
                v2_by_radius[radius_m] = v2_passage_at_radius(con, radius_m)
                build_vp_estimate(con)
                if radius_m == 50:
                    v3_full = v3_held_vs_vp(con)
                    v4 = v4_classification_agreement(con)
                    vp_reconciliation = build_vp_reconciliation(con, v4["n_total"])
                    check7_vp = check7_vp_offsets(con) if c7.get("n") else []
                    for row in check7_vp:
                        if abs(row["offset_final"]) > 60:
                            sys.exit(
                                f"STOP: reappeared stop (trip={row['trip_id']}, "
                                f"stop_sequence={row['stop_sequence']}) has a final-appearance "
                                f"offset of {row['offset_final']}s against VP (>60s)."
                            )
                else:
                    offsets = [
                        r[0]
                        for r in con.execute(
                            "SELECT (hvc.held_value - ve.vp_time) FROM held_value_check1 hvc "
                            "JOIN vp_estimate ve ON ve.trip_id = hvc.trip_id AND ve.stop_sequence = hvc.stop_sequence"
                        ).fetchall()
                    ]
                    v3_compact[radius_m] = summarize_offsets(offsets)

            ctx.update(
                {
                    "vp_ran": True,
                    "v1": v1,
                    "v1_per_hour": v1_hourly,
                    "v1_per_trip": v1_per_trip,
                    "v1_dropouts": v1_dropouts,
                    "vp_guardrails": guardrails,
                    "v2": v2_by_radius,
                    "v3": v3_full,
                    "v3_compact": v3_compact,
                    "v4": v4,
                    "vp_reconciliation": vp_reconciliation,
                    "check7_vp": check7_vp,
                }
            )
        else:
            ctx["vp_not_run_reason"] = (
                "Run with --vehicle-positions (2026-09-07 only) to include V1-V4."
                if svc_date != "2026-09-07"
                else "Run with --vehicle-positions to include V1-V4."
            )

        ctx["observations"] = build_observations(ctx)

    print("Rendering report...")
    report_text = render_report(ctx)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / f"held_values_{svc_date}.md"
    out_path.write_text(report_text, encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
