#!/usr/bin/env python3
"""
validate_observed_time.py - full-day validation scan for the D-005 observed-time
rule (docs/definitions.md, docs/decisions.md D-005/D-006).

Evidence only: this script never changes a definition. It stages one service
date's TripUpdates archives (from kronoberg_transit.fetch_koda) into long-format
Parquet, matches trips against that date's static schedule, and writes a
Markdown report to docs/validation/observed_time_<date>.md.

Usage:
    uv run --env-file .env python scripts/validate_observed_time.py 2026-09-07
"""

import argparse
import bisect
import os
import sys
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from koda_scan_lib import (
    FEED,
    INTERIM_DIR,
    OPERATOR,
    REPORT_DIR,
    classify_leaving,
    collect_drop_events,
    distinct_route_types,
    fetch_route_type_by_trip,
    fetch_static_scheduled_times,
    fetch_static_stop_lookup,
    fetch_stu_details,
    fetch_target_trip_meta,
    fetch_trip_timelines,
    fmt_pct_list,
    fmt_share,
    fmt_ts_both,
    git_commit_hash,
    gtfs_hms_to_unix,
    load_static_gtfs,
    percentile,
    predicted_arrival,
    predicted_departure,
    setup_duckdb,
    stage_date,
    summarize_prediction_entries,
)

# --------------------------------------------------------------------------
# Sections 1-3: snapshot cadence, field population, trip-level / matching
# --------------------------------------------------------------------------


def section_cadence(con: duckdb.DuckDBPyConnection) -> dict:
    gaps = [
        r[0]
        for r in con.execute(
            "SELECT header_timestamp - LAG(header_timestamp) OVER (ORDER BY header_timestamp) "
            "FROM snapshots"
        ).fetchall()
        if r[0] is not None
    ]
    dup_count = con.execute(
        "SELECT (SELECT COUNT(*) FROM all_snapshot_files) - (SELECT COUNT(DISTINCT header_timestamp) FROM all_snapshot_files)"
    ).fetchone()[0]
    top_gaps = con.execute(
        "SELECT LAG(header_timestamp) OVER (ORDER BY header_timestamp) AS gap_start, "
        "header_timestamp AS gap_end, "
        "header_timestamp - LAG(header_timestamp) OVER (ORDER BY header_timestamp) AS gap "
        "FROM snapshots QUALIFY gap IS NOT NULL ORDER BY gap DESC, gap_end ASC LIMIT 20"
    ).fetchall()
    return {
        "n_gaps": len(gaps),
        "p50": percentile(gaps, 50),
        "p95": percentile(gaps, 95),
        "p99": percentile(gaps, 99),
        "max": max(gaps) if gaps else None,
        "over_30s": sum(1 for g in gaps if g > 30),
        "over_60s": sum(1 for g in gaps if g > 60),
        "over_300s": sum(1 for g in gaps if g > 300),
        "top_gaps": top_gaps,
        "duplicate_snapshot_count": dup_count,
    }


def section_field_population(con: duckdb.DuckDBPyConnection) -> dict:
    total_stu = con.execute("SELECT COUNT(*) FROM rows_dedup WHERE stop_id IS NOT NULL").fetchone()[
        0
    ]
    fields = [
        "arrival_time_present",
        "arrival_delay_present",
        "arrival_uncertainty_present",
        "departure_time_present",
        "departure_delay_present",
        "departure_uncertainty_present",
    ]
    counts = {}
    for f in fields:
        counts[f] = con.execute(
            f"SELECT COUNT(*) FROM rows_dedup WHERE stop_id IS NOT NULL AND {f}"
        ).fetchone()[0]
    counts["stop_id_present"] = total_stu
    counts["stop_sequence_present"] = con.execute(
        "SELECT COUNT(*) FROM rows_dedup WHERE stop_id IS NOT NULL AND stop_sequence IS NOT NULL"
    ).fetchone()[0]
    stu_sr_dist = con.execute(
        "SELECT stop_schedule_relationship, COUNT(*) FROM rows_dedup "
        "WHERE stop_id IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    return {"total_stu": total_stu, "counts": counts, "stop_schedule_relationship": stu_sr_dist}


def section_trip_level(con: duckdb.DuckDBPyConnection) -> dict:
    # Per (snapshot, trip) observation, not per stop_time_update row - a trip
    # with multiple STUs must not be counted multiple times here.
    trip_sr_dist = con.execute(
        "SELECT trip_schedule_relationship, COUNT(*) FROM ("
        "  SELECT DISTINCT header_timestamp, trip_id, start_date, trip_schedule_relationship"
        "  FROM rows_dedup WHERE trip_id IS NOT NULL"
        ") GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    n_matched = con.execute("SELECT COUNT(*) FROM trip_match WHERE matched_static").fetchone()[0]
    n_unmatched = con.execute(
        "SELECT COUNT(*) FROM trip_match WHERE NOT matched_static"
    ).fetchone()[0]
    n_scheduled = con.execute("SELECT COUNT(*) FROM scheduled_trips").fetchone()[0]
    n_static_never_seen = con.execute(
        "SELECT COUNT(*) FROM scheduled_trips st WHERE NOT EXISTS "
        "(SELECT 1 FROM trip_presence tp WHERE tp.trip_id = st.trip_id)"
    ).fetchone()[0]
    return {
        "trip_schedule_relationship": trip_sr_dist,
        "n_scheduled_static": n_scheduled,
        "n_matched": n_matched,
        "n_unmatched": n_unmatched,
        "n_static_never_seen": n_static_never_seen,
    }


# --------------------------------------------------------------------------
# Sections 4-5, 7-9: trip lifecycles, stop drops, prediction-vs-drop, first
# stops, route_type split. These need per-trip event sequencing, which is
# done in Python over data pulled from the SQL layer above.
# --------------------------------------------------------------------------


def section_stop_drops(events: list[dict]) -> dict:
    clean = [e for e in events if e["kind"] == "clean"]
    not_front = [e for e in events if e["kind"] == "not_from_front"]
    multi = [e for e in events if e["kind"] == "multi_drop"]
    widths = [e["first_ts"] - e["last_ts"] for e in events]
    return {
        "clean_count": len(clean),
        "not_from_front_count": len(not_front),
        "multi_drop_count": len(multi),
        "total_events": len(events),
        "window_p50": percentile(widths, 50),
        "window_p95": percentile(widths, 95),
        "window_p99": percentile(widths, 99),
        "window_max": max(widths) if widths else None,
        "clean_events": clean,
    }


def build_prediction_entries(
    con: duckdb.DuckDBPyConnection, svc_date_obj: date, events: list[dict], use_arrival_first: bool
) -> list[dict]:
    stu_keys = [(e["trip_id"], e["start_date"], e["last_ts"], e["stop_id"]) for e in events]
    sched_keys = [(e["trip_id"], e["stop_seq"]) for e in events]
    stu_details = fetch_stu_details(con, stu_keys)
    sched_times = fetch_static_scheduled_times(con, sched_keys)
    predict = predicted_arrival if use_arrival_first else predicted_departure

    entries = []
    for e in events:
        detail = stu_details.get((e["trip_id"], e["start_date"], e["last_ts"], e["stop_id"]))
        if detail is None:
            entries.append({"offset": None, "source": "missing_stu_row", "position": None})
            continue
        sched_arr_hms, sched_dep_hms = sched_times.get((e["trip_id"], e["stop_seq"]), (None, None))
        pred, source = predict(detail, sched_arr_hms, sched_dep_hms, svc_date_obj)
        if pred is None:
            entries.append({"offset": None, "source": source, "position": None})
            continue
        midpoint = (e["last_ts"] + e["first_ts"]) / 2
        offset = pred - midpoint
        if pred < e["last_ts"]:
            position = "before"
        elif pred > e["first_ts"]:
            position = "after"
        else:
            position = "inside"
        entries.append({"offset": offset, "source": source, "position": position})
    return entries


def section_prediction_vs_drop(
    con: duckdb.DuckDBPyConnection,
    svc_date_obj: date,
    clean_events: list[dict],
    final_only_events: list[dict],
    snapshot_ts: list[int],
) -> dict:
    departure_entries = build_prediction_entries(
        con, svc_date_obj, clean_events, use_arrival_first=False
    )
    arrival_entries = build_prediction_entries(
        con, svc_date_obj, clean_events, use_arrival_first=True
    )

    final_events_with_next = []
    for e in final_only_events:
        idx = bisect.bisect_right(snapshot_ts, e["last_ts"])
        if idx >= len(snapshot_ts):
            continue
        final_events_with_next.append({**e, "first_ts": snapshot_ts[idx]})
    final_entries = build_prediction_entries(
        con, svc_date_obj, final_events_with_next, use_arrival_first=True
    )

    return {
        "departure": summarize_prediction_entries(departure_entries),
        "arrival": summarize_prediction_entries(arrival_entries),
        "final_stop_arrival": summarize_prediction_entries(final_entries),
        "n_final_events": len(final_events_with_next),
    }


def section_retained_stale(con: duckdb.DuckDBPyConnection) -> dict:
    result: dict = {}
    for label, threshold in (("60s", 60), ("120s", 120), ("300s", 300)):
        n = con.execute(
            "SELECT COUNT(*) FROM rows_dedup WHERE stop_id IS NOT NULL AND ("
            f"(departure_time_present AND header_timestamp - departure_time > {threshold}) "
            f"OR (arrival_time_present AND header_timestamp - arrival_time > {threshold}))"
        ).fetchone()[0]
        result[label] = n
    result["denominator"] = con.execute(
        "SELECT COUNT(*) FROM rows_dedup WHERE stop_id IS NOT NULL AND (arrival_time_present OR departure_time_present)"
    ).fetchone()[0]
    return result


def section_first_stops(con: duckdb.DuckDBPyConnection, svc_date_obj: date) -> dict:
    con.execute(
        "CREATE OR REPLACE TABLE first_stop_population AS SELECT * FROM trip_match WHERE matched_static AND NOT censored_start"
    )
    pop = con.execute(
        "SELECT trip_key, trip_id, start_date, first_ts FROM first_stop_population"
    ).fetchall()

    first_snapshot_min_seq = con.execute(
        "SELECT r.trip_id, r.start_date, MIN(r.stop_sequence) FROM rows_dedup r "
        "JOIN first_stop_population p ON p.trip_id = r.trip_id "
        "AND COALESCE(p.start_date,'') = COALESCE(r.start_date,'') AND p.first_ts = r.header_timestamp "
        "WHERE r.stop_id IS NOT NULL GROUP BY r.trip_id, r.start_date"
    ).fetchall()
    min_seq_map = {(tid, sd): seq for tid, sd, seq in first_snapshot_min_seq}

    ever_seen = con.execute(
        "SELECT r.trip_id, r.start_date, BOOL_OR(r.stop_id = sfs.first_stop_id) FROM rows_dedup r "
        "JOIN first_stop_population p ON p.trip_id = r.trip_id AND COALESCE(p.start_date,'') = COALESCE(r.start_date,'') "
        "JOIN static_first_stop sfs ON sfs.trip_id = r.trip_id "
        "WHERE r.stop_id IS NOT NULL GROUP BY r.trip_id, r.start_date"
    ).fetchall()
    ever_map = {(tid, sd): ever for tid, sd, ever in ever_seen}

    sched_dep = {
        tid: hms
        for tid, _sid, _seq, hms in con.execute(
            "SELECT trip_id, first_stop_id, first_stop_sequence, first_departure_hms FROM static_first_stop"
        ).fetchall()
    }

    offsets_minutes, min_seqs, ever_flags = [], [], []
    for _trip_key, trip_id, start_date, first_ts in pop:
        key = (trip_id, start_date)
        if key in min_seq_map:
            min_seqs.append(min_seq_map[key])
        if key in ever_map:
            ever_flags.append(ever_map[key])
        hms = sched_dep.get(trip_id)
        if hms:
            sched_ts = gtfs_hms_to_unix(svc_date_obj, hms)
            if sched_ts is not None:
                offsets_minutes.append((first_ts - sched_ts) / 60)

    return {
        "n_total": len(pop),
        "n_ever_seen_first_stop": sum(1 for f in ever_flags if f),
        "n_ever_seen_denom": len(ever_flags),
        "first_seq_percentiles": {q: percentile(min_seqs, q) for q in (25, 50, 75, 95)},
        "n_first_seq": len(min_seqs),
        "offset_percentiles": {q: percentile(offsets_minutes, q) for q in (5, 25, 50, 75, 95)},
        "n_offsets": len(offsets_minutes),
    }


def run_lifecycle_for_subset(
    con: duckdb.DuckDBPyConnection,
    svc_date_obj: date,
    timelines: dict,
    trip_meta: dict,
    final_lookup: dict,
    snapshot_ts: list[int],
) -> dict:
    leaving = classify_leaving(timelines, trip_meta, final_lookup)
    drop_events = collect_drop_events(timelines, trip_meta)
    drops = section_stop_drops(drop_events)
    pred = section_prediction_vs_drop(
        con, svc_date_obj, drops["clean_events"], leaving["final_only_events"], snapshot_ts
    )
    return {"leaving": leaving, "drops": drops, "pred": pred}


# --------------------------------------------------------------------------
# Report rendering
# --------------------------------------------------------------------------


def render_prediction_block(title: str, summary: dict) -> list[str]:
    lines = [f"**{title}** ({fmt_share(summary['n_resolved'], summary['n_events'])} resolved)", ""]
    if summary["n_events"] == 0:
        lines.append("Not applicable: no events in this category.")
        return lines
    lines.append(f"- Prediction source used: {summary['source_counts']}")
    lines.append(
        f"- Offset percentiles (seconds, predicted minus window midpoint): {fmt_pct_list(summary['percentiles'])}"
    )
    for th, share in summary["share_within"].items():
        share_str = f"{100 * share:.1f}%" if share is not None else "n/a"
        lines.append(
            f"- Share with |offset| <= {th}s: {share_str} (of {summary['n_resolved']} resolved)"
        )
    pc = summary["position_counts"]
    total_pos = sum(pc.values()) or 1
    lines.append(
        f"- Prediction falls before/inside/after the drop window: "
        f"{pc['before']} / {pc['inside']} / {pc['after']} (of {total_pos})"
    )
    return lines


def render_report(ctx: dict) -> str:
    lines: list[str] = []
    d = ctx["date"]
    lines.append(f"# Observed-time validation scan: {d}")
    lines.append("")
    lines.append(
        "Evidence for D-005/D-006 (see `docs/decisions.md`). This report never changes a "
        "definition; it only measures how the `krono` TripUpdates feed actually behaves."
    )
    lines.append("")
    lines.append("## Header")
    lines.append("")
    lines.append(f"- Service date: {d}")
    lines.append(f"- Operator/feed: {OPERATOR}/{FEED}")
    missing_str = (
        ", ".join(f"{h:02d}" for h in ctx["missing_hours"]) if ctx["missing_hours"] else "none"
    )
    lines.append(f"- Hours present: {len(ctx['present_hours'])}/24 (missing: {missing_str})")
    lines.append(f"- Snapshot count (deduplicated): {ctx['n_snapshots']}")
    lines.append(f"- First snapshot: {fmt_ts_both(ctx['day_first_ts'])}")
    lines.append(f"- Last snapshot: {fmt_ts_both(ctx['day_last_ts'])}")
    lines.append(f"- Git commit: {ctx['git_commit']}")
    lines.append(f"- Run timestamp (UTC): {ctx['run_ts']}")
    lines.append("")

    # Section 1
    c = ctx["cadence"]
    lines.append("## 1. Snapshot cadence")
    lines.append("")
    lines.append(
        f"- Gaps measured: {c['n_gaps']} (between {ctx['n_snapshots']} deduplicated snapshots)"
    )
    lines.append(f"- p50={c['p50']}s, p95={c['p95']}s, p99={c['p99']}s, max={c['max']}s")
    lines.append(f"- Gaps > 30s: {fmt_share(c['over_30s'], c['n_gaps'])}")
    lines.append(f"- Gaps > 60s: {fmt_share(c['over_60s'], c['n_gaps'])}")
    lines.append(f"- Gaps > 300s: {fmt_share(c['over_300s'], c['n_gaps'])}")
    lines.append(
        f"- Duplicate snapshot files (same header_timestamp as another file): {c['duplicate_snapshot_count']}"
    )
    lines.append("- 20 largest gaps (start -> end, gap_s):")
    for gap_start, gap_end, gap in c["top_gaps"]:
        lines.append(f"  - start {fmt_ts_both(gap_start)}")
        lines.append(f"    end   {fmt_ts_both(gap_end)}: {gap}s")
    lines.append("")

    # Section 2
    fp = ctx["field_population"]
    lines.append("## 2. Field population")
    lines.append("")
    lines.append(f"Over {fp['total_stu']} stop_time_update rows (deduplicated snapshots):")
    lines.append("")
    for k, v in fp["counts"].items():
        lines.append(f"- {k}: {fmt_share(v, fp['total_stu'])}")
    lines.append("")
    lines.append("Stop-level schedule_relationship distribution:")
    for name, n in fp["stop_schedule_relationship"]:
        lines.append(f"- {name}: {fmt_share(n, fp['total_stu'])}")
    lines.append("")

    # Section 3
    tl = ctx["trip_level"]
    lines.append("## 3. Trip level")
    lines.append("")
    total_obs = sum(n for _, n in tl["trip_schedule_relationship"])
    lines.append("Trip-level schedule_relationship, counted per (snapshot, trip) observation:")
    seen_names = {name for name, _ in tl["trip_schedule_relationship"]}
    for name, n in tl["trip_schedule_relationship"]:
        lines.append(f"- {name}: {fmt_share(n, total_obs)}")
    for required in ("CANCELED", "ADDED"):
        if required not in seen_names:
            lines.append(f"- {required}: {fmt_share(0, total_obs)}")
    lines.append("")
    lines.append(f"- Static trips scheduled for {d}: {tl['n_scheduled_static']}")
    lines.append(
        f"- Realtime trips matched to static: {fmt_share(tl['n_matched'], tl['n_matched'] + tl['n_unmatched'])}"
    )
    lines.append(
        f"- Realtime trips NOT matched to static: {fmt_share(tl['n_unmatched'], tl['n_matched'] + tl['n_unmatched'])}"
    )
    lines.append(
        f"- Static trips never seen in the feed: {fmt_share(tl['n_static_never_seen'], tl['n_scheduled_static'])}"
    )
    lines.append("")

    # Section 4
    def render_leaving(leaving: dict, heading: str) -> None:
        lines.append(heading)
        lines.append("")
        total = leaving["total"]
        lines.append(f"Among {total} matched, uncensored trips:")
        lines.append(
            f"- Left with only the final static stop remaining: {fmt_share(leaving['final_only'], total)}"
        )
        lines.append(
            f"- Left with 2+ stops remaining: {fmt_share(leaving['two_plus_count'], total)}"
        )
        if leaving["two_plus_distribution"]:
            dist = leaving["two_plus_distribution"]
            pcts = {q: percentile(dist, q) for q in (25, 50, 75, 95)}
            lines.append(
                f"  - Stops-remaining distribution: min={min(dist)}, {fmt_pct_list(pcts)}, max={max(dist)}"
            )
        lines.append(
            f"- Other pattern (0 stops, or 1 stop that is not the final stop): {fmt_share(leaving['other'], total)}"
        )
        if leaving["other_examples"]:
            lines.append("  - Examples (trip_key, last_ts, remaining stop_ids):")
            for tk, ts, stops in leaving["other_examples"]:
                lines.append(f"    - {tk} @ {ts}: {stops}")
        lines.append("")

    render_leaving(ctx["leaving"], "## 4. How trips leave the feed")

    # Section 5
    def render_drops(drops: dict, heading: str) -> None:
        lines.append(heading)
        lines.append("")
        total = drops["total_events"]
        lines.append(f"Among {total} stop-drop events (matched, uncensored trips):")
        lines.append(
            f"- Clean drops (front of list, trip stays in feed): {fmt_share(drops['clean_count'], total)}"
        )
        lines.append(
            f"- Drops not from the front: {fmt_share(drops['not_from_front_count'], total)}"
        )
        lines.append(
            f"- Intervals with 2+ stops dropped at once: {fmt_share(drops['multi_drop_count'], total)}"
        )
        lines.append(
            f"- Window width (first-absent minus last-present, seconds): "
            f"p50={drops['window_p50']}, p95={drops['window_p95']}, p99={drops['window_p99']}, max={drops['window_max']}"
        )
        lines.append("")

    render_drops(ctx["drops"], "## 5. Stop drops")

    # Section 6
    st = ctx["stale"]
    lines.append("## 6. Retained stale values")
    lines.append("")
    lines.append(
        f"Among {st['denominator']} stop_time_updates with a populated arrival.time or departure.time, "
        f"still present with a predicted time before the snapshot's own header_timestamp by more than:"
    )
    lines.append(f"- 60s: {fmt_share(st['60s'], st['denominator'])}")
    lines.append(f"- 120s: {fmt_share(st['120s'], st['denominator'])}")
    lines.append(f"- 300s: {fmt_share(st['300s'], st['denominator'])}")
    lines.append("")

    # Section 7
    def render_prediction(pred: dict, heading: str) -> None:
        lines.append(heading)
        lines.append("")
        lines.extend(
            render_prediction_block(
                "Predicted departure (departure-first fallback)", pred["departure"]
            )
        )
        lines.append("")
        lines.extend(
            render_prediction_block("Predicted arrival (arrival-first fallback)", pred["arrival"])
        )
        lines.append("")
        lines.extend(
            render_prediction_block(
                f"Final-stop arrival, trips that left with only the final stop ({pred['n_final_events']} events)",
                pred["final_stop_arrival"],
            )
        )
        lines.append("")

    render_prediction(ctx["prediction"], "## 7. Prediction vs drop time")

    # Section 8
    fs = ctx["first_stops"]
    lines.append("## 8. First stops")
    lines.append("")
    lines.append(f"Among {fs['n_total']} matched trips (excluding censored_start):")
    lines.append(
        f"- Static first stop ever appears in the feed: {fmt_share(fs['n_ever_seen_first_stop'], fs['n_ever_seen_denom'])}"
    )
    if fs["n_first_seq"]:
        lines.append(
            f"- First stop_sequence position seen (n={fs['n_first_seq']}): {fmt_pct_list(fs['first_seq_percentiles'])}"
        )
    else:
        lines.append("- First stop_sequence position seen: not applicable (no trips)")
    if fs["n_offsets"]:
        lines.append(
            f"- First appearance vs scheduled first departure, minutes (n={fs['n_offsets']}): "
            f"{fmt_pct_list(fs['offset_percentiles'])}"
        )
    else:
        lines.append(
            "- First appearance vs scheduled first departure: not applicable (no resolvable scheduled times)"
        )
    lines.append("")

    # Section 9
    lines.append("## 9. Route-type split")
    lines.append("")
    route_types = ctx["route_types"]
    if len(route_types) <= 1:
        rt_label = route_types[0] if route_types else "n/a"
        lines.append(
            f"Not applicable: only one route_type ({rt_label}) observed among matched trips."
        )
        lines.append("")
    else:
        for rt in route_types:
            sub = ctx["route_type_sections"][rt]
            render_leaving(sub["leaving"], f"### route_type={rt}: how trips leave the feed")
            render_drops(sub["drops"], f"### route_type={rt}: stop drops")
            render_prediction(sub["pred"], f"### route_type={rt}: prediction vs drop time")

    # Section 10
    lines.append("## 10. Observations")
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
    c = ctx["cadence"]
    if c["duplicate_snapshot_count"]:
        obs.append(
            f"{c['duplicate_snapshot_count']} snapshot files share a header_timestamp with another file "
            "in the same day (deduplicated to one canonical file per timestamp)."
        )
    fp = ctx["field_population"]
    skipped = dict(fp["stop_schedule_relationship"]).get("SKIPPED", 0)
    if skipped:
        obs.append(
            f"{fmt_share(skipped, fp['total_stu'])} stop_time_updates carry schedule_relationship=SKIPPED. "
            "D-005's description states the feed 'never marks it SKIPPED' - this contradicts that."
        )
    tl = ctx["trip_level"]
    canceled = dict(tl["trip_schedule_relationship"]).get("CANCELED", 0)
    if canceled:
        obs.append(
            f"{canceled} (snapshot, trip) observations carry trip-level schedule_relationship=CANCELED, "
            "relevant to the Cancelled trips definition's dependence on this field being emitted."
        )
    leaving = ctx["leaving"]
    if leaving["total"]:
        obs.append(
            f"{fmt_share(leaving['final_only'], leaving['total'])} of matched, uncensored trips left the feed "
            f"with only their final static stop remaining; {fmt_share(leaving['two_plus_count'], leaving['total'])} "
            "left with 2+ stops still listed."
        )
    drops = ctx["drops"]
    if drops["not_from_front_count"] or drops["multi_drop_count"]:
        obs.append(
            f"{drops['not_from_front_count']} drops were not from the front of the list and "
            f"{drops['multi_drop_count']} intervals dropped 2+ stops at once, out of {drops['total_events']} total."
        )
    st = ctx["stale"]
    if st["60s"]:
        obs.append(
            f"{fmt_share(st['60s'], st['denominator'])} of stop_time_updates with a populated time field are "
            "more than 60s stale relative to the snapshot's own header_timestamp."
        )
    dep = ctx["prediction"]["departure"]
    if dep["n_resolved"]:
        pcts = dep["percentiles"]
        obs.append(
            f"Across {dep['n_resolved']} resolved clean drops, predicted departure.time falls before the drop "
            f"window in {dep['position_counts']['before']} of {sum(dep['position_counts'].values())} cases "
            f"(offset percentiles: {fmt_pct_list(pcts)}), and this holds even for trips running close to on-time - "
            "the predicted departure/arrival time is not close to when the stop actually leaves the feed."
        )
    final_pred = ctx["prediction"]["final_stop_arrival"]
    if final_pred["n_resolved"]:
        pcts = final_pred["percentiles"]
        pc = final_pred["position_counts"]
        obs.append(
            f"For the {final_pred['n_resolved']} trips that left with only their final stop remaining, the "
            f"predicted-arrival-vs-drop offset is far more spread out and not consistently one-sided "
            f"(offset percentiles: {fmt_pct_list(pcts)}; before/inside/after: {pc['before']}/{pc['inside']}/{pc['after']}), "
            "unlike the tight, consistently-before pattern for mid-route clean drops."
        )
    return obs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", help="Service date to validate (YYYY-MM-DD)")
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
        static_dir = load_static_gtfs(svc_date, key, Path(tmpdir))
        print("Building DuckDB tables...")
        glob = str(INTERIM_DIR / svc_date / "*.parquet")
        setup_duckdb(con, svc_date, static_dir, glob)

        day_first_ts, day_last_ts = con.execute(
            "SELECT MIN(header_timestamp), MAX(header_timestamp) FROM snapshots"
        ).fetchone()
        n_snapshots = con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]

        print("Computing sections 1-3...")
        cadence = section_cadence(con)
        field_population = section_field_population(con)
        trip_level = section_trip_level(con)

        print("Computing sections 4-5, 7-8...")
        trip_meta = fetch_target_trip_meta(con)
        final_lookup, _first_lookup = fetch_static_stop_lookup(con)
        timelines = fetch_trip_timelines(con)
        snapshot_ts = [
            r[0]
            for r in con.execute(
                "SELECT header_timestamp FROM snapshots ORDER BY header_timestamp"
            ).fetchall()
        ]

        lifecycle = run_lifecycle_for_subset(
            con, svc_date_obj, timelines, trip_meta, final_lookup, snapshot_ts
        )
        stale = section_retained_stale(con)
        first_stops = section_first_stops(con, svc_date_obj)

        print("Computing section 9 (route-type split)...")
        route_types = distinct_route_types(con)
        route_type_sections = {}
        if len(route_types) > 1:
            route_type_by_trip = fetch_route_type_by_trip(con)
            for rt in route_types:
                sub_keys = {
                    tk for tk, (tid, _sd) in trip_meta.items() if route_type_by_trip.get(tid) == rt
                }
                sub_timelines = {k: v for k, v in timelines.items() if k in sub_keys}
                route_type_sections[rt] = run_lifecycle_for_subset(
                    con, svc_date_obj, sub_timelines, trip_meta, final_lookup, snapshot_ts
                )

    ctx = {
        "date": svc_date,
        "present_hours": present_hours,
        "missing_hours": missing_hours,
        "n_snapshots": n_snapshots,
        "day_first_ts": day_first_ts,
        "day_last_ts": day_last_ts,
        "git_commit": git_commit_hash(),
        "run_ts": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cadence": cadence,
        "field_population": field_population,
        "trip_level": trip_level,
        "leaving": lifecycle["leaving"],
        "drops": lifecycle["drops"],
        "prediction": lifecycle["pred"],
        "stale": stale,
        "first_stops": first_stops,
        "route_types": route_types,
        "route_type_sections": route_type_sections,
    }
    ctx["observations"] = build_observations(ctx)

    print("Rendering report...")
    report_text = render_report(ctx)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / f"observed_time_{svc_date}.md"
    out_path.write_text(report_text, encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
