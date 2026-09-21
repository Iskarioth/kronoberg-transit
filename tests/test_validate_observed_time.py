import sys
from datetime import date
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from koda_scan_lib import (
    assert_unique_rows,
    classify_leaving,
    collect_drop_events,
    fetch_stu_details,
    find_reappearance_events,
    find_repeat_drops,
    predicted_arrival,
    predicted_departure,
    setup_duckdb,
)
from validate_observed_time import (
    section_cadence,
    section_retained_stale,
)

from kronoberg_transit.gtfs_rt import STAGING_SCHEMA

# --------------------------------------------------------------------------
# Stop drops: collect_drop_events over plain Python timeline structures
# --------------------------------------------------------------------------


def test_clean_drop_from_front_of_list():
    timelines = {"T1": [(100, ["A", "B", "C"], [1, 2, 3]), (120, ["B", "C"], [2, 3])]}
    trip_meta = {"T1": ("trip1", "20260101")}

    events = collect_drop_events(timelines, trip_meta)

    assert len(events) == 1
    assert events[0]["kind"] == "clean"
    assert events[0]["stop_id"] == "A"
    assert events[0]["stop_seq"] == 1
    assert events[0]["last_ts"] == 100
    assert events[0]["first_ts"] == 120


def test_two_stops_dropping_in_one_interval():
    timelines = {"T1": [(100, ["A", "B", "C"], [1, 2, 3]), (120, ["C"], [3])]}
    trip_meta = {"T1": ("trip1", "20260101")}

    events = collect_drop_events(timelines, trip_meta)

    assert len(events) == 2
    assert all(e["kind"] == "multi_drop" for e in events)
    assert all(e["n_together"] == 2 for e in events)
    assert {e["stop_id"] for e in events} == {"A", "B"}


def test_drop_not_from_the_front():
    timelines = {"T1": [(100, ["A", "B", "C"], [1, 2, 3]), (120, ["A", "C"], [1, 3])]}
    trip_meta = {"T1": ("trip1", "20260101")}

    events = collect_drop_events(timelines, trip_meta)

    assert len(events) == 1
    assert events[0]["kind"] == "not_from_front"
    assert events[0]["stop_id"] == "B"


def test_looping_trip_drop_is_keyed_on_stop_sequence_not_stop_id():
    # Stop "A" is visited twice, at stop_sequence 1 and 3. Between the two
    # snapshots, only the first occurrence (seq 1) drops; the second (seq 3)
    # is still listed. A stop_id-only comparison would see "A" present in
    # both snapshots and miss the first visit's drop entirely.
    timelines = {
        "T1": [
            (100, ["A", "B", "A", "C"], [1, 2, 3, 4]),
            (120, ["B", "A", "C"], [2, 3, 4]),
        ]
    }
    trip_meta = {"T1": ("trip1", "20260101")}

    events = collect_drop_events(timelines, trip_meta)

    assert len(events) == 1
    assert events[0]["kind"] == "clean"
    assert events[0]["stop_id"] == "A"
    assert events[0]["stop_seq"] == 1


def test_assert_unique_rows_fires_on_duplicate_trip_stop_sequence():
    rows = [
        {"trip_id": "T1", "start_date": "20260101", "stop_seq": 1},
        {"trip_id": "T1", "start_date": "20260101", "stop_seq": 1},
    ]

    with pytest.raises(AssertionError):
        assert_unique_rows(rows, ["trip_id", "start_date", "stop_seq"], "test rows")


def test_stop_that_drops_reappears_then_drops_again_is_a_repeat_drop():
    # ts=100: stop 1 present. ts=120: dropped. ts=140: reappears in one
    # snapshot. ts=160: dropped again. Must run without raising, and the
    # reappearance-in-between must be recognized as a repeat drop.
    timelines = {
        "T1": [
            (100, ["A"], [1]),
            (120, [], []),
            (140, ["A"], [1]),
            (160, [], []),
        ]
    }
    trip_meta = {"T1": ("trip1", "20260101")}

    events = collect_drop_events(timelines, trip_meta)

    assert len(events) == 2
    assert all(e["stop_seq"] == 1 for e in events)
    assert [e["last_ts"] for e in events] == [100, 140]

    repeat_drops = find_repeat_drops(events)
    key = ("trip1", "20260101", 1)
    assert key in repeat_drops
    assert len(repeat_drops[key]) == 2

    reappearances = find_reappearance_events(timelines, trip_meta, repeat_drops)
    assert len(reappearances) == 1
    assert reappearances[0]["reappear_ts"] == 140
    assert reappearances[0]["stop_seq"] == 1


def test_assert_unique_rows_passes_on_unique_keys():
    rows = [
        {"trip_id": "T1", "start_date": "20260101", "stop_seq": 1},
        {"trip_id": "T1", "start_date": "20260101", "stop_seq": 3},
    ]

    assert_unique_rows(rows, ["trip_id", "start_date", "stop_seq"], "test rows")  # must not raise


# --------------------------------------------------------------------------
# fetch_stu_details: keyed on stop_sequence, so a looping trip's two visits
# to the same stop_id resolve to their own, correct row
# --------------------------------------------------------------------------


def test_fetch_stu_details_resolves_the_correct_visit_for_a_looping_stop_id():
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE rows_dedup (trip_id VARCHAR, start_date VARCHAR, header_timestamp BIGINT, "
        "stop_id VARCHAR, stop_sequence INTEGER, "
        "arrival_time_present BOOLEAN, arrival_time BIGINT, "
        "arrival_delay_present BOOLEAN, arrival_delay INTEGER, "
        "departure_time_present BOOLEAN, departure_time BIGINT, "
        "departure_delay_present BOOLEAN, departure_delay INTEGER)"
    )
    # Same trip, same snapshot, same stop_id "A" at two stop_sequences, with
    # different departure times.
    con.execute(
        "INSERT INTO rows_dedup VALUES "
        "('trip1', '20260101', 100, 'A', 1, false, NULL, false, NULL, true, 5000, false, NULL), "
        "('trip1', '20260101', 100, 'A', 3, false, NULL, false, NULL, true, 9000, false, NULL)"
    )

    result = fetch_stu_details(con, [("trip1", "20260101", 100, 1)])

    assert result[("trip1", "20260101", 100, 1)]["departure_time"] == 5000


# --------------------------------------------------------------------------
# How trips leave the feed
# --------------------------------------------------------------------------


def test_trip_leaves_with_only_final_stop_remaining():
    timelines = {"T1": [(100, ["A", "B"], [1, 2]), (120, ["B"], [2])]}
    trip_meta = {"T1": ("trip1", "20260101")}
    final_lookup = {"trip1": ("B", 2)}

    result = classify_leaving(timelines, trip_meta, final_lookup)

    assert result["final_only"] == 1
    assert result["two_plus_count"] == 0
    assert result["other"] == 0


def test_trip_leaves_with_two_or_more_stops_remaining():
    timelines = {"T1": [(100, ["A", "B", "C"], [1, 2, 3])]}
    trip_meta = {"T1": ("trip1", "20260101")}
    final_lookup = {"trip1": ("C", 3)}

    result = classify_leaving(timelines, trip_meta, final_lookup)

    assert result["final_only"] == 0
    assert result["two_plus_count"] == 1
    assert result["two_plus_distribution"] == [3]


# --------------------------------------------------------------------------
# Snapshot cadence: a gap wider than the normal cadence
# --------------------------------------------------------------------------


def test_gap_wider_than_normal_cadence_is_flagged():
    con = duckdb.connect()
    con.execute("CREATE TABLE snapshots (header_timestamp BIGINT, snap_idx BIGINT)")
    timestamps = [1000, 1015, 1030, 1045, 1145]  # last gap is 100s, rest are 15s
    con.executemany(
        "INSERT INTO snapshots VALUES (?, ?)", [(t, i + 1) for i, t in enumerate(timestamps)]
    )
    con.execute(
        "CREATE TABLE all_snapshot_files (snapshot_file VARCHAR, header_timestamp BIGINT, hour INTEGER)"
    )
    con.executemany(
        "INSERT INTO all_snapshot_files VALUES (?, ?, ?)", [(f"f{t}", t, 0) for t in timestamps]
    )

    result = section_cadence(con)

    assert result["max"] == 100
    assert result["over_60s"] == 1
    assert result["over_30s"] == 1
    assert result["duplicate_snapshot_count"] == 0


# --------------------------------------------------------------------------
# Censoring: trips already present at the day's first/last snapshot
# --------------------------------------------------------------------------


def _write_minimal_static(static_dir: Path) -> None:
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "trips.txt").write_text(
        "route_id,service_id,trip_id\nR1,SVC1,dummy_trip\n", encoding="utf-8"
    )
    (static_dir / "stop_times.txt").write_text(
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        "dummy_trip,00:00:00,00:00:00,S1,1\n",
        encoding="utf-8",
    )
    (static_dir / "calendar_dates.txt").write_text(
        "service_id,date,exception_type\nSVC1,20260101,1\n", encoding="utf-8"
    )
    (static_dir / "routes.txt").write_text("route_id,route_type\nR1,700\n", encoding="utf-8")


def _write_synthetic_snapshots(parquet_path: Path, rows: list[dict]) -> None:
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    full_rows = []
    for row in rows:
        full_row = dict.fromkeys(STAGING_SCHEMA.names)
        full_row.update(row)
        full_rows.append(full_row)
    table = pa.Table.from_pylist(full_rows, schema=STAGING_SCHEMA)
    pq.write_table(table, parquet_path)


def _placeholder_row(header_timestamp: int, trip_id: str, hour: int = 0) -> dict:
    # snapshot_file depends only on header_timestamp: one real .pb snapshot
    # carries every trip entity visible at that moment, so all their rows
    # share a single snapshot_file value.
    return {
        "header_timestamp": header_timestamp,
        "hour": hour,
        "snapshot_file": f"f{header_timestamp}",
        "trip_id": trip_id,
        "start_date": "20260101",
        "trip_schedule_relationship": "SCHEDULED",
    }


def test_censored_start_and_censored_end_trips(tmp_path):
    static_dir = tmp_path / "static"
    _write_minimal_static(static_dir)

    interim_dir = tmp_path / "interim"
    rows = [
        _placeholder_row(100, "CS"),  # present at the day's first snapshot (100) and mid-day (110)
        _placeholder_row(110, "CS"),
        _placeholder_row(110, "MID"),  # present only mid-day: neither censored
        _placeholder_row(110, "CE"),  # present mid-day and through the day's last snapshot (120)
        _placeholder_row(120, "CE"),
    ]
    _write_synthetic_snapshots(interim_dir / "00.parquet", rows)

    con = duckdb.connect()
    setup_duckdb(con, "2026-01-01", static_dir, str(interim_dir / "*.parquet"))

    censoring = {
        trip_id: (start, end)
        for trip_id, start, end in con.execute(
            "SELECT trip_id, censored_start, censored_end FROM trip_censoring"
        ).fetchall()
    }

    assert censoring["CS"] == (True, False)
    assert censoring["CE"] == (False, True)
    assert censoring["MID"] == (False, False)


# --------------------------------------------------------------------------
# Retained stale values
# --------------------------------------------------------------------------


def test_retained_stale_value_is_counted():
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE rows_dedup (header_timestamp BIGINT, stop_id VARCHAR, "
        "arrival_time_present BOOLEAN, arrival_time BIGINT, "
        "departure_time_present BOOLEAN, departure_time BIGINT)"
    )
    con.execute("INSERT INTO rows_dedup VALUES (1000, 'S1', true, 900, true, 900)")  # 100s stale
    con.execute("INSERT INTO rows_dedup VALUES (1000, 'S2', true, 990, true, 990)")  # 10s, fresh

    result = section_retained_stale(con)

    assert result["60s"] == 1
    assert result["300s"] == 0
    assert result["denominator"] == 2


# --------------------------------------------------------------------------
# Predicted-time source fallback: time, then scheduled + delay, then arrival
# --------------------------------------------------------------------------


def test_predicted_departure_uses_time_when_present():
    detail = {
        "departure_time_present": True,
        "departure_time": 5000,
        "departure_delay_present": False,
        "departure_delay": None,
        "arrival_time_present": True,
        "arrival_time": 4900,
        "arrival_delay_present": False,
        "arrival_delay": None,
    }

    pred, source = predicted_departure(detail, None, None, date(2026, 1, 1))

    assert source == "departure.time"
    assert pred == 5000


def test_predicted_departure_falls_back_to_scheduled_plus_delay():
    detail = {
        "departure_time_present": False,
        "departure_time": None,
        "departure_delay_present": True,
        "departure_delay": 30,
        "arrival_time_present": True,
        "arrival_time": 4900,
        "arrival_delay_present": False,
        "arrival_delay": None,
    }
    sched_dep_hms = "00:05:00"

    pred, source = predicted_departure(detail, None, sched_dep_hms, date(2026, 1, 1))

    assert source == "scheduled_departure+delay"
    assert pred == 5 * 60 + 30 + _stockholm_offset_seconds(date(2026, 1, 1))


def test_predicted_departure_falls_back_to_arrival_when_no_departure_info():
    detail = {
        "departure_time_present": False,
        "departure_time": None,
        "departure_delay_present": False,
        "departure_delay": None,
        "arrival_time_present": True,
        "arrival_time": 4800,
        "arrival_delay_present": False,
        "arrival_delay": None,
    }

    pred, source = predicted_departure(detail, None, None, date(2026, 1, 1))

    assert source == "arrival.time"
    assert pred == 4800


def test_predicted_arrival_mirrors_the_fallback_order():
    detail = {
        "departure_time_present": True,
        "departure_time": 5000,
        "departure_delay_present": False,
        "departure_delay": None,
        "arrival_time_present": False,
        "arrival_time": None,
        "arrival_delay_present": False,
        "arrival_delay": None,
    }

    pred, source = predicted_arrival(detail, None, None, date(2026, 1, 1))

    assert source == "departure.time"
    assert pred == 5000


def test_predicted_time_unavailable_when_no_source_resolves():
    detail = {
        "departure_time_present": False,
        "departure_time": None,
        "departure_delay_present": False,
        "departure_delay": None,
        "arrival_time_present": False,
        "arrival_time": None,
        "arrival_delay_present": False,
        "arrival_delay": None,
    }

    pred, source = predicted_departure(detail, None, None, date(2026, 1, 1))

    assert pred is None
    assert source == "unavailable"


def _stockholm_offset_seconds(svc_date_obj: date) -> int:
    """Local midnight (Europe/Stockholm) as a unix timestamp, for building expected values."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    midnight = datetime(
        svc_date_obj.year, svc_date_obj.month, svc_date_obj.day, tzinfo=ZoneInfo("Europe/Stockholm")
    )
    return int(midnight.timestamp())
