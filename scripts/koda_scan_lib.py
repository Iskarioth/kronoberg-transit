"""
koda_scan_lib.py - shared infrastructure for the KoDa TripUpdates validation
scripts (scripts/validate_observed_time.py, scripts/validate_held_values.py):
protobuf staging, static GTFS loading, DuckDB setup, trip lifecycle helpers,
and small report-formatting utilities.

Not a script itself - has no __main__ entry point.
"""

import subprocess
import sys
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import py7zr
import pyarrow as pa
import pyarrow.parquet as pq
from google.transit import gtfs_realtime_pb2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from smoke_trafiklab import extract_gtfs_text_files, fetch_koda_static

RAW_DIR = Path("data/raw/koda")
INTERIM_DIR = Path("data/interim/observed_time_scan")
STATIC_DIR = Path("data/static")
REPORT_DIR = Path("docs/validation")
OPERATOR = "krono"
FEED = "TripUpdates"
HOURS = range(24)
STOCKHOLM = ZoneInfo("Europe/Stockholm")

TRIP_SR = gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship
STU_SR = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship

STAGING_SCHEMA = pa.schema(
    [
        ("header_timestamp", pa.int64()),
        ("hour", pa.int8()),
        ("snapshot_file", pa.string()),
        ("trip_id", pa.string()),
        ("start_date", pa.string()),
        ("route_id", pa.string()),
        ("trip_schedule_relationship", pa.string()),
        ("tu_timestamp_present", pa.bool_()),
        ("tu_timestamp", pa.int64()),
        ("stop_sequence", pa.int32()),
        ("stop_id", pa.string()),
        ("stop_schedule_relationship", pa.string()),
        ("arrival_time_present", pa.bool_()),
        ("arrival_time", pa.int64()),
        ("arrival_delay_present", pa.bool_()),
        ("arrival_delay", pa.int32()),
        ("arrival_uncertainty_present", pa.bool_()),
        ("arrival_uncertainty", pa.int32()),
        ("departure_time_present", pa.bool_()),
        ("departure_time", pa.int64()),
        ("departure_delay_present", pa.bool_()),
        ("departure_delay", pa.int32()),
        ("departure_uncertainty_present", pa.bool_()),
        ("departure_uncertainty", pa.int32()),
    ]
)


# --------------------------------------------------------------------------
# Staging: protobuf snapshots -> long-format Parquet, one hour at a time
# --------------------------------------------------------------------------


def _event_fields(prefix: str, event, has_event: bool) -> dict:
    time_present = has_event and event.HasField("time")
    delay_present = has_event and event.HasField("delay")
    uncertainty_present = has_event and event.HasField("uncertainty")
    return {
        f"{prefix}_time_present": time_present,
        f"{prefix}_time": event.time if time_present else None,
        f"{prefix}_delay_present": delay_present,
        f"{prefix}_delay": event.delay if delay_present else None,
        f"{prefix}_uncertainty_present": uncertainty_present,
        f"{prefix}_uncertainty": event.uncertainty if uncertainty_present else None,
    }


def _stu_fields(stu) -> dict:
    has_arrival = stu.HasField("arrival")
    has_departure = stu.HasField("departure")
    return {
        "stop_sequence": stu.stop_sequence if stu.HasField("stop_sequence") else None,
        "stop_id": stu.stop_id if stu.HasField("stop_id") else None,
        "stop_schedule_relationship": STU_SR.Name(stu.schedule_relationship),
        **_event_fields("arrival", stu.arrival, has_arrival),
        **_event_fields("departure", stu.departure, has_departure),
    }


def _empty_stu_fields() -> dict:
    return {
        "stop_sequence": None,
        "stop_id": None,
        "stop_schedule_relationship": None,
        **_event_fields("arrival", None, False),
        **_event_fields("departure", None, False),
    }


def stage_hour(operator: str, feed: str, svc_date: str, hour: int, out_dir: Path) -> Path | None:
    """Parse one hour's KoDa archive into a long-format Parquet file.

    Returns None (and writes nothing) if the hour's archive is not on disk.
    One row per snapshot x trip x stop_time_update; trips with an empty
    stop_time_update list still get one placeholder row (stop_sequence NULL)
    so trip presence can be tracked independent of stop-level detail.
    """
    archive_path = RAW_DIR / operator / feed / svc_date / f"{hour:02d}.7z"
    if not archive_path.exists():
        return None

    rows: list[dict] = []
    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        names = sorted(n for n in archive.getnames() if n.endswith(".pb"))
        with tempfile.TemporaryDirectory() as tmpdir:
            archive.extract(path=tmpdir, targets=names)
            for name in names:
                body = (Path(tmpdir) / name).read_bytes()
                feed_msg = gtfs_realtime_pb2.FeedMessage()
                feed_msg.ParseFromString(body)
                header_ts = feed_msg.header.timestamp

                for entity in feed_msg.entity:
                    if not entity.HasField("trip_update"):
                        continue
                    tu = entity.trip_update
                    td = tu.trip
                    base = {
                        "header_timestamp": header_ts,
                        "hour": hour,
                        "snapshot_file": name,
                        "trip_id": td.trip_id if td.HasField("trip_id") else None,
                        "start_date": td.start_date if td.HasField("start_date") else None,
                        "route_id": td.route_id if td.HasField("route_id") else None,
                        "trip_schedule_relationship": TRIP_SR.Name(td.schedule_relationship),
                        "tu_timestamp_present": tu.HasField("timestamp"),
                        "tu_timestamp": tu.timestamp if tu.HasField("timestamp") else None,
                    }
                    if not tu.stop_time_update:
                        rows.append({**base, **_empty_stu_fields()})
                        continue
                    for stu in tu.stop_time_update:
                        rows.append({**base, **_stu_fields(stu)})

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{hour:02d}.parquet"
    table = pa.Table.from_pylist(rows, schema=STAGING_SCHEMA)
    pq.write_table(table, out_path)
    return out_path


def stage_date(operator: str, feed: str, svc_date: str) -> tuple[list[int], list[int]]:
    """Stage every available hour for a service date. Returns (present_hours, missing_hours)."""
    out_dir = INTERIM_DIR / svc_date
    present, missing = [], []
    for hour in HOURS:
        result = stage_hour(operator, feed, svc_date, hour, out_dir)
        (present if result is not None else missing).append(hour)
    return present, missing


# --------------------------------------------------------------------------
# VehiclePositions staging: separate schema, separate interim directory
# --------------------------------------------------------------------------

VP_FEED = "VehiclePositions"
VP_INTERIM_DIR = Path("data/interim/vehicle_positions_scan")
VP_STOP_STATUS = gtfs_realtime_pb2.VehiclePosition.VehicleStopStatus

VP_STAGING_SCHEMA = pa.schema(
    [
        ("header_timestamp", pa.int64()),
        ("hour", pa.int8()),
        ("snapshot_file", pa.string()),
        ("trip_id", pa.string()),
        ("start_date", pa.string()),
        ("vehicle_id_present", pa.bool_()),
        ("vehicle_id", pa.string()),
        ("vehicle_timestamp_present", pa.bool_()),
        ("vehicle_timestamp", pa.int64()),
        ("latitude_present", pa.bool_()),
        ("latitude", pa.float64()),
        ("longitude_present", pa.bool_()),
        ("longitude", pa.float64()),
        ("current_status_present", pa.bool_()),
        ("current_status", pa.string()),
        ("current_stop_sequence_present", pa.bool_()),
        ("current_stop_sequence", pa.int32()),
        ("stop_id", pa.string()),
    ]
)


def stage_hour_vp(operator: str, svc_date: str, hour: int, out_dir: Path) -> Path | None:
    """Parse one hour's KoDa VehiclePositions archive into a long-format Parquet
    file. Returns None (and writes nothing) if the hour's archive is not on disk."""
    archive_path = RAW_DIR / operator / VP_FEED / svc_date / f"{hour:02d}.7z"
    if not archive_path.exists():
        return None

    rows: list[dict] = []
    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        names = sorted(n for n in archive.getnames() if n.endswith(".pb"))
        with tempfile.TemporaryDirectory() as tmpdir:
            archive.extract(path=tmpdir, targets=names)
            for name in names:
                body = (Path(tmpdir) / name).read_bytes()
                feed_msg = gtfs_realtime_pb2.FeedMessage()
                feed_msg.ParseFromString(body)
                header_ts = feed_msg.header.timestamp

                for entity in feed_msg.entity:
                    if not entity.HasField("vehicle"):
                        continue
                    vp = entity.vehicle
                    td = vp.trip
                    vd = vp.vehicle
                    has_pos = vp.HasField("position")
                    has_vehicle_id = vp.HasField("vehicle") and vd.HasField("id")
                    rows.append(
                        {
                            "header_timestamp": header_ts,
                            "hour": hour,
                            "snapshot_file": name,
                            "trip_id": td.trip_id if td.HasField("trip_id") else None,
                            "start_date": td.start_date if td.HasField("start_date") else None,
                            "vehicle_id_present": has_vehicle_id,
                            "vehicle_id": vd.id if has_vehicle_id else None,
                            "vehicle_timestamp_present": vp.HasField("timestamp"),
                            "vehicle_timestamp": vp.timestamp if vp.HasField("timestamp") else None,
                            "latitude_present": has_pos,
                            "latitude": vp.position.latitude if has_pos else None,
                            "longitude_present": has_pos,
                            "longitude": vp.position.longitude if has_pos else None,
                            "current_status_present": vp.HasField("current_status"),
                            "current_status": (
                                VP_STOP_STATUS.Name(vp.current_status)
                                if vp.HasField("current_status")
                                else None
                            ),
                            "current_stop_sequence_present": vp.HasField("current_stop_sequence"),
                            "current_stop_sequence": (
                                vp.current_stop_sequence
                                if vp.HasField("current_stop_sequence")
                                else None
                            ),
                            "stop_id": vp.stop_id if vp.HasField("stop_id") else None,
                        }
                    )

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{hour:02d}.parquet"
    table = pa.Table.from_pylist(rows, schema=VP_STAGING_SCHEMA)
    pq.write_table(table, out_path)
    return out_path


def stage_date_vp(operator: str, svc_date: str) -> tuple[list[int], list[int]]:
    """Stage every available hour of VehiclePositions for a service date."""
    out_dir = VP_INTERIM_DIR / svc_date
    present, missing = [], []
    for hour in HOURS:
        result = stage_hour_vp(operator, svc_date, hour, out_dir)
        (present if result is not None else missing).append(hour)
    return present, missing


# --------------------------------------------------------------------------
# Static schedule loading (reuses smoke_trafiklab's download/extract code)
# --------------------------------------------------------------------------

STATIC_FILES = ["trips.txt", "stop_times.txt", "calendar_dates.txt", "routes.txt"]


def load_static_gtfs(
    svc_date: str, key: str | None, tmpdir: Path, extra_files: list[str] | None = None
) -> Path:
    """Return a directory containing this date's trips/stop_times/calendar_dates/routes
    (plus any extra_files, e.g. stops.txt) as plain CSV files, fetching from KoDa
    (or reusing a cached data/static/ archive) only if not already cached."""
    cached = None
    for ext in (".zip", ".7z"):
        candidate = STATIC_DIR / f"{OPERATOR}_{svc_date}{ext}"
        if candidate.exists():
            cached = candidate
            break

    if cached is not None:
        body = cached.read_bytes()
    else:
        if not key:
            sys.exit(f"No cached static GTFS for {svc_date} and TRAFIKLAB_KODA_KEY is not set.")
        body = fetch_koda_static(svc_date, key)
        STATIC_DIR.mkdir(parents=True, exist_ok=True)
        (STATIC_DIR / f"{OPERATOR}_{svc_date}.zip").write_bytes(body)

    wanted = list(STATIC_FILES) + [f for f in (extra_files or []) if f not in STATIC_FILES]
    files = extract_gtfs_text_files(body, wanted)
    static_dir = tmpdir / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        # KoDa's static archives carry "\r\r\n" line endings, which trips up
        # DuckDB's CSV sniffer even though Python's csv module tolerates it.
        (static_dir / name).write_text(text.replace("\r", ""), encoding="utf-8")
    return static_dir


def gtfs_hms_to_unix(svc_date: date, hms: str) -> int | None:
    """Convert a GTFS HH:MM:SS local time (hours may exceed 23) on a service
    date, in Europe/Stockholm, to a unix timestamp."""
    if not hms:
        return None
    parts = hms.split(":")
    if len(parts) != 3:
        return None
    h, m, s = (int(p) for p in parts)
    extra_days, h = divmod(h, 24)
    local_dt = datetime(svc_date.year, svc_date.month, svc_date.day, h, m, s, tzinfo=STOCKHOLM)
    local_dt += timedelta(days=extra_days)
    return int(local_dt.timestamp())


def git_commit_hash() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - report generation must not fail on git metadata
        return "unknown"


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolation percentile (q in [0, 100]) over a possibly-unsorted list."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (q / 100)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


# --------------------------------------------------------------------------
# DuckDB setup: staged snapshots + static schedule, joined and de-duplicated
# --------------------------------------------------------------------------


def setup_duckdb(
    con: duckdb.DuckDBPyConnection, svc_date: str, static_dir: Path, glob: str
) -> None:
    date_int = int(svc_date.replace("-", ""))
    csv_base = "header=true, quote='\"', escape='\"', delim=',', sample_size=-1"

    # All-digit GTFS IDs (trip_id, stop_id, route_id, service_id) get sniffed
    # as BIGINT by DuckDB's CSV reader unless pinned to VARCHAR. That silently
    # breaks Python-side dict lookups against the realtime feed's string IDs,
    # even though SQL-side joins tolerate the mismatch via implicit casts.
    con.execute(
        f"CREATE OR REPLACE TABLE static_trips AS SELECT * FROM read_csv("
        f"'{static_dir}/trips.txt', {csv_base}, "
        f"types={{'route_id': 'VARCHAR', 'service_id': 'VARCHAR', 'trip_id': 'VARCHAR'}})"
    )
    con.execute(
        f"CREATE OR REPLACE TABLE static_stop_times AS SELECT * FROM read_csv("
        f"'{static_dir}/stop_times.txt', {csv_base}, "
        f"types={{'trip_id': 'VARCHAR', 'stop_id': 'VARCHAR'}})"
    )
    con.execute(
        f"CREATE OR REPLACE TABLE static_calendar_dates AS SELECT * FROM read_csv("
        f"'{static_dir}/calendar_dates.txt', {csv_base}, types={{'service_id': 'VARCHAR'}})"
    )
    con.execute(
        f"CREATE OR REPLACE TABLE static_routes AS SELECT * FROM read_csv("
        f"'{static_dir}/routes.txt', {csv_base}, types={{'route_id': 'VARCHAR'}})"
    )

    con.execute(
        f"CREATE OR REPLACE VIEW active_service AS "
        f"SELECT service_id FROM static_calendar_dates WHERE date = {date_int} AND exception_type = 1"
    )
    con.execute(
        "CREATE OR REPLACE VIEW scheduled_trips AS "
        "SELECT t.trip_id, t.route_id, t.service_id FROM static_trips t "
        "JOIN active_service s USING (service_id)"
    )
    con.execute(
        "CREATE OR REPLACE VIEW static_first_stop AS "
        "SELECT trip_id, stop_id AS first_stop_id, stop_sequence AS first_stop_sequence, "
        "departure_time AS first_departure_hms FROM ("
        "  SELECT trip_id, stop_id, stop_sequence, departure_time,"
        "         ROW_NUMBER() OVER (PARTITION BY trip_id ORDER BY stop_sequence ASC) AS rn"
        "  FROM static_stop_times"
        ") WHERE rn = 1"
    )
    con.execute(
        "CREATE OR REPLACE VIEW static_final_stop AS "
        "SELECT trip_id, stop_id AS final_stop_id, stop_sequence AS final_stop_sequence, "
        "arrival_time AS final_arrival_hms, departure_time AS final_departure_hms FROM ("
        "  SELECT trip_id, stop_id, stop_sequence, arrival_time, departure_time,"
        "         ROW_NUMBER() OVER (PARTITION BY trip_id ORDER BY stop_sequence DESC) AS rn"
        "  FROM static_stop_times"
        ") WHERE rn = 1"
    )
    con.execute(
        "CREATE OR REPLACE VIEW trip_route_type AS "
        "SELECT t.trip_id, r.route_type FROM static_trips t JOIN static_routes r USING (route_id)"
    )

    con.execute(f"CREATE OR REPLACE TABLE raw_rows AS SELECT * FROM read_parquet('{glob}')")
    con.execute(
        "CREATE OR REPLACE TABLE all_snapshot_files AS "
        "SELECT DISTINCT snapshot_file, header_timestamp, hour FROM raw_rows"
    )
    con.execute(
        "CREATE OR REPLACE TABLE canonical_file AS "
        "SELECT header_timestamp, MIN(snapshot_file) AS snapshot_file "
        "FROM all_snapshot_files GROUP BY header_timestamp"
    )
    con.execute(
        "CREATE OR REPLACE TABLE rows_dedup AS "
        "SELECT r.* FROM raw_rows r JOIN canonical_file c "
        "ON r.header_timestamp = c.header_timestamp AND r.snapshot_file = c.snapshot_file"
    )
    con.execute(
        "CREATE OR REPLACE TABLE snapshots AS "
        "SELECT header_timestamp, ROW_NUMBER() OVER (ORDER BY header_timestamp) AS snap_idx "
        "FROM (SELECT DISTINCT header_timestamp FROM rows_dedup) ORDER BY header_timestamp"
    )
    con.execute(
        "CREATE OR REPLACE TABLE trip_presence AS "
        "SELECT DISTINCT header_timestamp, trip_id, start_date, "
        "trip_id || COALESCE('_' || start_date, '') AS trip_key "
        "FROM rows_dedup WHERE trip_id IS NOT NULL"
    )
    con.execute(
        "CREATE OR REPLACE TABLE trip_span AS "
        "SELECT trip_key, ANY_VALUE(trip_id) AS trip_id, ANY_VALUE(start_date) AS start_date, "
        "MIN(header_timestamp) AS first_ts, MAX(header_timestamp) AS last_ts, COUNT(*) AS n_snapshots "
        "FROM trip_presence GROUP BY trip_key"
    )

    day_first_ts, day_last_ts = con.execute(
        "SELECT MIN(header_timestamp), MAX(header_timestamp) FROM snapshots"
    ).fetchone()

    con.execute(
        f"CREATE OR REPLACE TABLE trip_censoring AS "
        f"SELECT *, (first_ts = {day_first_ts}) AS censored_start, "
        f"(last_ts = {day_last_ts}) AS censored_end FROM trip_span"
    )
    con.execute(
        "CREATE OR REPLACE TABLE trip_match AS "
        "SELECT tc.*, (st.trip_id IS NOT NULL) AS matched_static "
        "FROM trip_censoring tc LEFT JOIN scheduled_trips st ON tc.trip_id = st.trip_id"
    )
    con.execute(
        "CREATE OR REPLACE TABLE target_trips AS "
        "SELECT * FROM trip_match WHERE matched_static AND NOT censored_start AND NOT censored_end"
    )
    # LIST(... ORDER BY ...) is pathologically slow in this DuckDB build on a
    # group count this size (minutes instead of milliseconds). Aggregate
    # unordered STRUCT pairs instead and sort them in Python on read.
    con.execute(
        "CREATE OR REPLACE TABLE target_snapshot_stops AS "
        "SELECT r.trip_id, r.start_date, r.trip_id || COALESCE('_' || r.start_date, '') AS trip_key, "
        "r.header_timestamp, "
        "LIST(STRUCT_PACK(seq := r.stop_sequence, stop := r.stop_id)) "
        "FILTER (WHERE r.stop_id IS NOT NULL) AS stop_pairs "
        "FROM rows_dedup r JOIN target_trips tt "
        "ON r.trip_id = tt.trip_id AND COALESCE(r.start_date,'') = COALESCE(tt.start_date,'') "
        "GROUP BY r.trip_id, r.start_date, r.header_timestamp"
    )


# --------------------------------------------------------------------------
# Trip lifecycle helpers
# --------------------------------------------------------------------------


def fetch_target_trip_meta(con: duckdb.DuckDBPyConnection) -> dict[str, tuple[str, str | None]]:
    return {
        tk: (tid, sd)
        for tk, tid, sd in con.execute(
            "SELECT trip_key, trip_id, start_date FROM target_trips"
        ).fetchall()
    }


def fetch_static_stop_lookup(con: duckdb.DuckDBPyConnection):
    final = {
        tid: (sid, seq)
        for tid, sid, seq, *_ in con.execute(
            "SELECT trip_id, final_stop_id, final_stop_sequence FROM static_final_stop"
        ).fetchall()
    }
    first = {
        tid: (sid, seq, hms)
        for tid, sid, seq, hms in con.execute(
            "SELECT trip_id, first_stop_id, first_stop_sequence, first_departure_hms FROM static_first_stop"
        ).fetchall()
    }
    return final, first


def fetch_trip_timelines(
    con: duckdb.DuckDBPyConnection, trip_keys: set[str] | None = None
) -> dict[str, list[tuple[int, list[str], list[int]]]]:
    rows = con.execute(
        "SELECT trip_key, header_timestamp, stop_pairs FROM target_snapshot_stops ORDER BY trip_key, header_timestamp"
    ).fetchall()
    timelines: dict[str, list[tuple[int, list[str], list[int]]]] = {}
    for trip_key, ts, stop_pairs in rows:
        if trip_keys is not None and trip_key not in trip_keys:
            continue
        ordered = sorted(stop_pairs or [], key=lambda p: p["seq"])
        stop_ids = [p["stop"] for p in ordered]
        stop_seqs = [p["seq"] for p in ordered]
        timelines.setdefault(trip_key, []).append((ts, stop_ids, stop_seqs))
    return timelines


def assert_unique_rows(rows: list[dict], key_cols: list[str], label: str) -> None:
    """Fails loudly if `rows` is not unique on key_cols. A stop_id alone can
    repeat within one trip on a looping route, so stop_id-keyed dicts/joins
    silently fan out or overwrite - every stop event here is keyed on
    (trip, stop_sequence) instead."""
    keys = [tuple(r[c] for c in key_cols) for r in rows]
    if len(keys) != len(set(keys)):
        raise AssertionError(
            f"{label} violates uniqueness on {key_cols}: {len(keys)} rows, {len(set(keys))} distinct"
        )


def collect_drop_events(timelines: dict, trip_meta: dict) -> list[dict]:
    """A 'drop' is a stop disappearing from a trip's stop_time_update list
    while the trip itself remains present in the next observed snapshot.
    Compared by stop_sequence, not stop_id: a looping trip can revisit the
    same stop_id at a later stop_sequence, and a stop_id-based comparison
    would wrongly treat the earlier occurrence's drop as "still listed"
    because of the later occurrence.

    A given (trip, stop_sequence) can legitimately appear more than once in
    the returned list: the feed occasionally re-lists a batch of already-
    dropped stops in one snapshot before dropping them again (see
    docs/validation/observed_time_*.md, "stops that dropped more than
    once"), so this is not asserted unique - unlike fetch_stu_details below,
    whose dict/join keying bug this same class of fix also covers."""
    events: list[dict] = []
    for trip_key, snaps in timelines.items():
        trip_id, start_date = trip_meta[trip_key]
        for i in range(len(snaps) - 1):
            ts_a, stops_a, seqs_a = snaps[i]
            ts_b, _stops_b, seqs_b = snaps[i + 1]
            set_seqs_b = set(seqs_b)
            dropped = [
                (sid, seq)
                for sid, seq in zip(stops_a, seqs_a, strict=True)
                if seq not in set_seqs_b
            ]
            if not dropped:
                continue
            if len(dropped) >= 2:
                kind = "multi_drop"
            else:
                min_seq_a = min(seqs_a) if seqs_a else None
                kind = "clean" if dropped[0][1] == min_seq_a else "not_from_front"
            for sid, seq in dropped:
                events.append(
                    {
                        "trip_key": trip_key,
                        "trip_id": trip_id,
                        "start_date": start_date,
                        "kind": kind,
                        "last_ts": ts_a,
                        "first_ts": ts_b,
                        "stop_id": sid,
                        "stop_seq": seq,
                        "n_together": len(dropped),
                    }
                )
    return events


def find_repeat_drops(events: list[dict]) -> dict[tuple, list[dict]]:
    """Groups drop events by (trip_id, start_date, stop_seq); returns only
    the keys with more than one drop event, each list ordered by last_ts."""
    groups: dict[tuple, list[dict]] = {}
    for e in events:
        key = (e["trip_id"], e["start_date"], e["stop_seq"])
        groups.setdefault(key, []).append(e)
    return {
        key: sorted(evs, key=lambda e: e["last_ts"]) for key, evs in groups.items() if len(evs) > 1
    }


def find_reappearance_events(
    timelines: dict, trip_meta: dict, repeat_drops: dict[tuple, list[dict]]
) -> list[dict]:
    """For every repeat-dropped (trip, stop_seq), finds each moment the stop
    reappears in the trip's own stop_time_update list having previously been
    absent (every False->True presence transition after the first drop)."""
    trip_stop_seqs: dict[str, set[int]] = {}
    for (_trip_id, _start_date, stop_seq), drops in repeat_drops.items():
        trip_stop_seqs.setdefault(drops[0]["trip_key"], set()).add(stop_seq)

    events: list[dict] = []
    for trip_key, stop_seqs in trip_stop_seqs.items():
        trip_id, start_date = trip_meta[trip_key]
        snaps = timelines[trip_key]
        for stop_seq in stop_seqs:
            prev_present = None
            for ts, _stops, seqs in snaps:
                present = stop_seq in seqs
                if prev_present is False and present:
                    events.append(
                        {
                            "trip_key": trip_key,
                            "trip_id": trip_id,
                            "start_date": start_date,
                            "stop_seq": stop_seq,
                            "reappear_ts": ts,
                        }
                    )
                prev_present = present
    return events


def group_reappearance_events(reappearances: list[dict]) -> dict[tuple, list[int]]:
    """Groups reappearance events by (trip_key, reappear_ts): stops that
    reappear together, in the same snapshot, form one reappearance event."""
    groups: dict[tuple, list[int]] = {}
    for e in reappearances:
        key = (e["trip_key"], e["reappear_ts"])
        groups.setdefault(key, []).append(e["stop_seq"])
    return groups


def classify_leaving(timelines: dict, trip_meta: dict, final_stop_lookup: dict) -> dict:
    final_only = 0
    two_plus: list[int] = []
    other = 0
    other_examples: list[tuple] = []
    final_only_events: list[dict] = []

    for trip_key, snaps in timelines.items():
        last_ts, last_stops, _last_seqs = snaps[-1]
        trip_id, start_date = trip_meta[trip_key]
        final = final_stop_lookup.get(trip_id)
        if final and len(last_stops) == 1 and last_stops[0] == final[0]:
            final_only += 1
            final_only_events.append(
                {
                    "trip_key": trip_key,
                    "trip_id": trip_id,
                    "start_date": start_date,
                    "last_ts": last_ts,
                    "stop_id": final[0],
                    "stop_seq": final[1],
                }
            )
        elif len(last_stops) >= 2:
            two_plus.append(len(last_stops))
        else:
            other += 1
            if len(other_examples) < 10:
                other_examples.append((trip_key, last_ts, last_stops))

    return {
        "total": len(timelines),
        "final_only": final_only,
        "two_plus_count": len(two_plus),
        "two_plus_distribution": two_plus,
        "other": other,
        "other_examples": other_examples,
        "final_only_events": final_only_events,
    }


def fetch_stu_details(con: duckdb.DuckDBPyConnection, keys: list[tuple]) -> dict:
    """keys: (trip_id, start_date, header_timestamp, stop_sequence) tuples.
    Keyed and joined on stop_sequence, not stop_id: a looping trip can
    revisit the same stop_id, and a stop_id-keyed join or dict would fan out
    or silently overwrite one occurrence's detail with the other's."""
    if not keys:
        return {}
    # con.executemany() is pathologically slow at this row count (tens of
    # thousands of individual INSERTs); register an Arrow table instead.
    key_table = pa.table(
        {
            "trip_id": [k[0] for k in keys],
            "start_date": [k[1] for k in keys],
            "header_timestamp": [k[2] for k in keys],
            "stop_sequence": [k[3] for k in keys],
        }
    )
    con.register("event_keys_src", key_table)
    con.execute("CREATE OR REPLACE TEMP TABLE event_keys AS SELECT * FROM event_keys_src")
    con.unregister("event_keys_src")
    rows = con.execute(
        "SELECT r.trip_id, r.start_date, r.header_timestamp, r.stop_sequence, "
        "r.arrival_time_present, r.arrival_time, r.arrival_delay_present, r.arrival_delay, "
        "r.departure_time_present, r.departure_time, r.departure_delay_present, r.departure_delay "
        "FROM rows_dedup r JOIN event_keys k "
        "ON r.trip_id = k.trip_id AND COALESCE(r.start_date,'') = COALESCE(k.start_date,'') "
        "AND r.header_timestamp = k.header_timestamp AND r.stop_sequence = k.stop_sequence"
    ).fetchall()
    out = {}
    for tid, sd, ts, seq, atp, at, adp, ad, dtp, dtt, ddp, dd in rows:
        out[(tid, sd, ts, seq)] = {
            "arrival_time_present": atp,
            "arrival_time": at,
            "arrival_delay_present": adp,
            "arrival_delay": ad,
            "departure_time_present": dtp,
            "departure_time": dtt,
            "departure_delay_present": ddp,
            "departure_delay": dd,
        }
    if len(rows) != len(out):
        raise AssertionError(
            f"fetch_stu_details join fanned out on (trip_id, start_date, header_timestamp, "
            f"stop_sequence): {len(rows)} rows, {len(out)} distinct keys"
        )
    return out


def fetch_trip_snapshot_signatures(
    con: duckdb.DuckDBPyConnection, trip_id: str, start_date: str | None
) -> dict[int, frozenset]:
    """Per-header_timestamp signature of one trip's active stop_time_update
    list: the set of (stop_sequence, arrival_time, arrival_uncertainty,
    departure_time, departure_uncertainty) tuples present at that snapshot.
    Used to test whether a reappearance snapshot exactly repeats an earlier
    one (a stale/duplicate publish), rather than being a fresh update."""
    rows = con.execute(
        "SELECT header_timestamp, stop_sequence, "
        "CASE WHEN arrival_time_present THEN arrival_time END, "
        "CASE WHEN arrival_uncertainty_present THEN arrival_uncertainty END, "
        "CASE WHEN departure_time_present THEN departure_time END, "
        "CASE WHEN departure_uncertainty_present THEN departure_uncertainty END "
        "FROM rows_dedup WHERE trip_id = ? AND COALESCE(start_date,'') = ? AND stop_id IS NOT NULL "
        "ORDER BY header_timestamp",
        [trip_id, start_date or ""],
    ).fetchall()
    sigs: dict[int, set] = {}
    for ts, seq, arr_t, arr_u, dep_t, dep_u in rows:
        sigs.setdefault(ts, set()).add((seq, arr_t, arr_u, dep_t, dep_u))
    return {ts: frozenset(s) for ts, s in sigs.items()}


def fetch_trip_tu_timestamps(
    con: duckdb.DuckDBPyConnection, trip_id: str, start_date: str | None
) -> dict[int, tuple[bool, int | None]]:
    """header_timestamp -> (tu_timestamp_present, tu_timestamp) for one trip:
    the trip-level TripUpdate.timestamp, not the feed header's timestamp."""
    rows = con.execute(
        "SELECT DISTINCT header_timestamp, tu_timestamp_present, tu_timestamp "
        "FROM rows_dedup WHERE trip_id = ? AND COALESCE(start_date,'') = ? "
        "ORDER BY header_timestamp",
        [trip_id, start_date or ""],
    ).fetchall()
    return {ts: (present, tut) for ts, present, tut in rows}


def fetch_static_scheduled_times(con: duckdb.DuckDBPyConnection, keys: list[tuple]) -> dict:
    if not keys:
        return {}
    key_table = pa.table(
        {
            "trip_id": [k[0] for k in keys],
            "stop_sequence": [k[1] for k in keys],
        }
    )
    con.register("sched_keys_src", key_table)
    con.execute("CREATE OR REPLACE TEMP TABLE sched_keys AS SELECT * FROM sched_keys_src")
    con.unregister("sched_keys_src")
    rows = con.execute(
        "SELECT k.trip_id, k.stop_sequence, st.arrival_time, st.departure_time "
        "FROM sched_keys k JOIN static_stop_times st "
        "ON st.trip_id = k.trip_id AND st.stop_sequence = k.stop_sequence"
    ).fetchall()
    return {(tid, seq): (arr, dep) for tid, seq, arr, dep in rows}


def predicted_departure(detail: dict, sched_arr_hms, sched_dep_hms, svc_date_obj: date):
    if detail["departure_time_present"]:
        return detail["departure_time"], "departure.time"
    if detail["departure_delay_present"] and sched_dep_hms:
        base = gtfs_hms_to_unix(svc_date_obj, sched_dep_hms)
        if base is not None:
            return base + detail["departure_delay"], "scheduled_departure+delay"
    if detail["arrival_time_present"]:
        return detail["arrival_time"], "arrival.time"
    if detail["arrival_delay_present"] and sched_arr_hms:
        base = gtfs_hms_to_unix(svc_date_obj, sched_arr_hms)
        if base is not None:
            return base + detail["arrival_delay"], "scheduled_arrival+delay"
    return None, "unavailable"


def predicted_arrival(detail: dict, sched_arr_hms, sched_dep_hms, svc_date_obj: date):
    if detail["arrival_time_present"]:
        return detail["arrival_time"], "arrival.time"
    if detail["arrival_delay_present"] and sched_arr_hms:
        base = gtfs_hms_to_unix(svc_date_obj, sched_arr_hms)
        if base is not None:
            return base + detail["arrival_delay"], "scheduled_arrival+delay"
    if detail["departure_time_present"]:
        return detail["departure_time"], "departure.time"
    if detail["departure_delay_present"] and sched_dep_hms:
        base = gtfs_hms_to_unix(svc_date_obj, sched_dep_hms)
        if base is not None:
            return base + detail["departure_delay"], "scheduled_departure+delay"
    return None, "unavailable"


def summarize_prediction_entries(entries: list[dict]) -> dict:
    resolved = [e for e in entries if e["offset"] is not None]
    offsets = [e["offset"] for e in resolved]
    source_counts: dict[str, int] = {}
    for e in entries:
        source_counts[e["source"]] = source_counts.get(e["source"], 0) + 1
    position_counts = {"before": 0, "inside": 0, "after": 0}
    for e in resolved:
        position_counts[e["position"]] += 1
    return {
        "n_events": len(entries),
        "n_resolved": len(resolved),
        "source_counts": source_counts,
        "percentiles": {q: percentile(offsets, q) for q in (1, 5, 25, 50, 75, 95, 99)},
        "share_within": {
            th: (sum(1 for o in offsets if abs(o) <= th) / len(offsets) if offsets else None)
            for th in (15, 30, 60)
        },
        "position_counts": position_counts,
    }


def distinct_route_types(con: duckdb.DuckDBPyConnection) -> list[int]:
    rows = con.execute(
        "SELECT DISTINCT rt.route_type FROM target_trips tt JOIN trip_route_type rt ON rt.trip_id = tt.trip_id"
    ).fetchall()
    return sorted(r[0] for r in rows)


def fetch_route_type_by_trip(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return dict(con.execute("SELECT trip_id, route_type FROM trip_route_type").fetchall())


# --------------------------------------------------------------------------
# Report-formatting helpers
# --------------------------------------------------------------------------


def fmt_share(n: int, d: int) -> str:
    if not d:
        return f"{n} / {d}"
    return f"{n} / {d} ({100 * n / d:.1f}%)"


def fmt_ts_both(ts: int) -> str:
    u = datetime.fromtimestamp(ts, tz=UTC)
    s = u.astimezone(STOCKHOLM)
    return f"{u.isoformat()} UTC / {s.isoformat()} Europe/Stockholm"


def fmt_pct_list(percentiles: dict) -> str:
    return ", ".join(
        f"p{q}={v:.1f}" if v is not None else f"p{q}=n/a" for q, v in percentiles.items()
    )
