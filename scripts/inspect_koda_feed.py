#!/usr/bin/env python3
"""
inspect_koda_feed.py - trace one trip's StopTimeUpdate list across consecutive
TripUpdates snapshots for a downloaded KoDa day, to answer the open question in
docs/definitions.md: does the feed retain a value for a stop after the vehicle
has passed it, or does the stop simply disappear from stop_time_update?

Requires archives already fetched with kronoberg_transit.fetch_koda into
data/raw/koda/<operator>/<feed>/<date>/<hour>.7z.

Usage:
    uv run python scripts/inspect_koda_feed.py 2026-09-07 --hours 8 9
    uv run python scripts/inspect_koda_feed.py 2026-09-07 --hours 8 9 --trip-id <id>
"""

import argparse
import sys
import tempfile
from pathlib import Path

import py7zr
from google.transit import gtfs_realtime_pb2

RAW_DIR = Path("data/raw/koda")


def load_snapshots(operator: str, feed: str, date: str, hour: int) -> list[tuple[str, bytes]]:
    """Return [(filename, protobuf_bytes), ...] for one hour, in chronological order."""
    archive_path = RAW_DIR / operator / feed / date / f"{hour:02d}.7z"
    if not archive_path.exists():
        sys.exit(f"Missing {archive_path}. Run fetch_koda for this date/hour first.")

    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        names = sorted(n for n in archive.getnames() if n.endswith(".pb"))
        with tempfile.TemporaryDirectory() as tmpdir:
            archive.extract(path=tmpdir, targets=names)
            return [(name, (Path(tmpdir) / name).read_bytes()) for name in names]


def parse_feed(body: bytes) -> gtfs_realtime_pb2.FeedMessage:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(body)
    return feed


def pick_trip_id(snapshots: list[tuple[str, bytes]]) -> str:
    """Pick a trip_id that appears in the first snapshot and persists across several."""
    _, body = snapshots[0]
    feed = parse_feed(body)
    for entity in feed.entity:
        if entity.HasField("trip_update") and len(entity.trip_update.stop_time_update) > 3:
            return entity.trip_update.trip.trip_id
    sys.exit("No trip with more than 3 stop_time_updates found in the first snapshot.")


def describe_stop_time_update(stu) -> str:
    parts = [f"seq={stu.stop_sequence}", f"stop={stu.stop_id}"]
    if stu.HasField("arrival"):
        parts.append(
            f"arr_delay={stu.arrival.delay}s"
            if stu.arrival.delay
            else f"arr_time={stu.arrival.time}"
        )
    if stu.HasField("departure"):
        parts.append(
            f"dep_delay={stu.departure.delay}s"
            if stu.departure.delay
            else f"dep_time={stu.departure.time}"
        )
    if stu.schedule_relationship != stu.SCHEDULED:
        parts.append(f"relationship={stu.ScheduleRelationship.Name(stu.schedule_relationship)}")
    return " ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", help="Service date already fetched (YYYY-MM-DD)")
    parser.add_argument("--operator", default="krono")
    parser.add_argument("--feed", default="TripUpdates")
    parser.add_argument("--hours", type=int, nargs="+", default=[8], help="Hours to load, in order")
    parser.add_argument("--trip-id", default=None, help="Trip to trace; auto-picked if omitted")
    args = parser.parse_args()

    snapshots: list[tuple[str, bytes]] = []
    for hour in args.hours:
        snapshots.extend(load_snapshots(args.operator, args.feed, args.date, hour))

    trip_id = args.trip_id or pick_trip_id(snapshots)
    print(f"Tracing trip_id={trip_id} across {len(snapshots)} snapshots\n")

    last_stop_ids: list[str] = []
    for name, body in snapshots:
        feed = parse_feed(body)
        match = next(
            (
                e.trip_update
                for e in feed.entity
                if e.HasField("trip_update") and e.trip_update.trip.trip_id == trip_id
            ),
            None,
        )
        ts = name.rsplit("/", 1)[-1]

        if match is None:
            print(f"{ts}: trip_id not present in this snapshot")
            continue

        stop_ids = [stu.stop_id for stu in match.stop_time_update]
        if stop_ids != last_stop_ids:
            dropped = [s for s in last_stop_ids if s not in stop_ids]
            added = [s for s in stop_ids if s not in last_stop_ids]
            print(f"{ts}: {len(stop_ids)} stops remaining", end="")
            if dropped:
                print(f"  DROPPED: {dropped}", end="")
            if added:
                print(f"  ADDED: {added}", end="")
            print()
            if match.stop_time_update:
                print(f"    next: {describe_stop_time_update(match.stop_time_update[0])}")
            last_stop_ids = stop_ids

    return 0


if __name__ == "__main__":
    sys.exit(main())
