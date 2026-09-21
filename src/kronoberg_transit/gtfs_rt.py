"""gtfs_rt.py - decode KoDa GTFS-Realtime archives (TripUpdates, VehiclePositions)
into long-format Parquet, one hour at a time.

One row per snapshot x trip x stop_time_update (TripUpdates) or per snapshot x
vehicle (VehiclePositions). Trips with an empty stop_time_update list still get
one placeholder row (stop_sequence NULL) so trip presence can be tracked
independent of stop-level detail.
"""

import tempfile
from pathlib import Path

import py7zr
import pyarrow as pa
import pyarrow.parquet as pq
from google.transit import gtfs_realtime_pb2

RAW_DIR = Path("data/raw/koda")
HOURS = range(24)

TRIP_SR = gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship
STU_SR = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship
VP_STOP_STATUS = gtfs_realtime_pb2.VehiclePosition.VehicleStopStatus

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


# --------------------------------------------------------------------------
# TripUpdates
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


def stage_hour(
    operator: str, feed: str, svc_date: str, hour: int, out_dir: Path, raw_dir: Path = RAW_DIR
) -> Path | None:
    """Parse one hour's KoDa TripUpdates archive into a long-format Parquet file.

    Returns None (and writes nothing) if the hour's archive is not on disk.
    """
    archive_path = raw_dir / operator / feed / svc_date / f"{hour:02d}.7z"
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


def stage_date(
    operator: str,
    feed: str,
    svc_date: str,
    out_dir: Path,
    hours=HOURS,
    raw_dir: Path = RAW_DIR,
) -> tuple[list[int], list[int]]:
    """Stage the requested hours (default all 24) for a service date into
    out_dir. Returns (present_hours, missing_hours)."""
    present, missing = [], []
    for hour in hours:
        result = stage_hour(operator, feed, svc_date, hour, out_dir, raw_dir=raw_dir)
        (present if result is not None else missing).append(hour)
    return present, missing


# --------------------------------------------------------------------------
# VehiclePositions: separate schema, same staging shape
# --------------------------------------------------------------------------


def stage_hour_vp(
    operator: str, svc_date: str, hour: int, out_dir: Path, raw_dir: Path = RAW_DIR
) -> Path | None:
    """Parse one hour's KoDa VehiclePositions archive into a long-format Parquet
    file. Returns None (and writes nothing) if the hour's archive is not on disk."""
    archive_path = raw_dir / operator / "VehiclePositions" / svc_date / f"{hour:02d}.7z"
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
                            "vehicle_timestamp": (
                                vp.timestamp if vp.HasField("timestamp") else None
                            ),
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


def stage_date_vp(
    operator: str, svc_date: str, out_dir: Path, hours=HOURS, raw_dir: Path = RAW_DIR
) -> tuple[list[int], list[int]]:
    """Stage the requested hours (default all 24) of VehiclePositions for a
    service date. Returns (present_hours, missing_hours)."""
    present, missing = [], []
    for hour in hours:
        result = stage_hour_vp(operator, svc_date, hour, out_dir, raw_dir=raw_dir)
        (present if result is not None else missing).append(hour)
    return present, missing
