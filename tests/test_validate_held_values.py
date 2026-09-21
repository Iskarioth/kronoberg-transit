import math
import sys
from datetime import date
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from validate_held_values import (
    HAVERSINE_SQL,
    assert_unique,
    build_held_value_summary,
    build_vp_reconciliation,
    check2_cross_tab,
    check3,
    check7_reappeared_stops,
    scheduled_time_utc,
    v2_passage_at_radius,
)

# --------------------------------------------------------------------------
# Scheduled-time conversion
# --------------------------------------------------------------------------


def test_scheduled_time_past_24h_rolls_over_to_next_day():
    # 25:15:00 on 2026-01-01 is 01:15:00 on 2026-01-02, Europe/Stockholm (no DST
    # in January), i.e. 00:15:00 UTC on 2026-01-02.
    result = scheduled_time_utc(date(2026, 1, 1), "25:15:00")

    from datetime import UTC, datetime

    expected = datetime(2026, 1, 2, 0, 15, 0, tzinfo=UTC)
    assert result == int(expected.timestamp())


# --------------------------------------------------------------------------
# Check 1: t_cross / drift, via a hand-built held_series_annotated
# --------------------------------------------------------------------------


def _register_held_series_annotated(con: duckdb.DuckDBPyConnection, rows: list[dict]) -> None:
    tbl = pa.table(
        {
            "trip_id": [r["trip_id"] for r in rows],
            "start_date": pa.array([r.get("start_date") for r in rows], type=pa.string()),
            "stop_id": [r["stop_id"] for r in rows],
            "stop_sequence": [r["stop_sequence"] for r in rows],
            "header_timestamp": [r["header_timestamp"] for r in rows],
            "held_time": [r["held_time"] for r in rows],
            "held_uncertainty_present": [r.get("held_uncertainty_present", False) for r in rows],
            "changed": [r["changed"] for r in rows],
        }
    )
    con.register("src", tbl)
    con.execute("CREATE OR REPLACE TABLE held_series_annotated AS SELECT * FROM src")
    con.unregister("src")


def test_stop_time_freezes_at_t_cross():
    con = duckdb.connect()
    rows = [
        {
            "trip_id": "T1",
            "stop_id": "S1",
            "stop_sequence": 1,
            "header_timestamp": 100,
            "held_time": 150,
            "changed": True,
        },
        {
            "trip_id": "T1",
            "stop_id": "S1",
            "stop_sequence": 1,
            "header_timestamp": 110,
            "held_time": 140,
            "changed": True,
        },
        {
            "trip_id": "T1",
            "stop_id": "S1",
            "stop_sequence": 1,
            "header_timestamp": 120,
            "held_time": 120,
            "changed": True,
        },
        {
            "trip_id": "T1",
            "stop_id": "S1",
            "stop_sequence": 1,
            "header_timestamp": 130,
            "held_time": 120,
            "changed": False,
        },
        {
            "trip_id": "T1",
            "stop_id": "S1",
            "stop_sequence": 1,
            "header_timestamp": 140,
            "held_time": 120,
            "changed": False,
        },
    ]
    _register_held_series_annotated(con, rows)

    build_held_value_summary(con)

    result = con.execute(
        "SELECT t_cross, held_value, n_changes_after_cross, drift FROM held_value_check1"
    ).fetchone()
    t_cross, held_value, n_changes_after_cross, drift = result

    assert t_cross == 120
    assert held_value == 120
    assert n_changes_after_cross == 0
    assert drift == 0


def test_stop_time_keeps_changing_after_t_cross():
    con = duckdb.connect()
    rows = [
        {
            "trip_id": "T2",
            "stop_id": "S2",
            "stop_sequence": 1,
            "header_timestamp": 200,
            "held_time": 230,
            "changed": True,
        },
        {
            "trip_id": "T2",
            "stop_id": "S2",
            "stop_sequence": 1,
            "header_timestamp": 210,
            "held_time": 215,
            "changed": True,
        },
        {
            "trip_id": "T2",
            "stop_id": "S2",
            "stop_sequence": 1,
            "header_timestamp": 220,
            "held_time": 220,
            "changed": True,
        },
        {
            "trip_id": "T2",
            "stop_id": "S2",
            "stop_sequence": 1,
            "header_timestamp": 230,
            "held_time": 225,
            "changed": True,
        },
        {
            "trip_id": "T2",
            "stop_id": "S2",
            "stop_sequence": 1,
            "header_timestamp": 240,
            "held_time": 225,
            "changed": False,
        },
    ]
    _register_held_series_annotated(con, rows)

    build_held_value_summary(con)

    t_cross, held_value, n_changes_after_cross, drift = con.execute(
        "SELECT t_cross, held_value, n_changes_after_cross, drift FROM held_value_check1"
    ).fetchone()

    assert t_cross == 220
    assert held_value == 225
    assert n_changes_after_cross == 1
    assert drift == 5


# --------------------------------------------------------------------------
# Check 3: trip removal classification
# --------------------------------------------------------------------------


def test_trip_removed_with_all_stops_in_the_past_is_completed():
    con = duckdb.connect()
    rows = [
        {
            "trip_id": "TA",
            "stop_id": "S1",
            "stop_sequence": 1,
            "header_timestamp": 1000,
            "held_time": 900,
            "changed": True,
        },
        {
            "trip_id": "TA",
            "stop_id": "S2",
            "stop_sequence": 2,
            "header_timestamp": 1000,
            "held_time": 950,
            "changed": True,
        },
    ]
    _register_held_series_annotated(con, rows)
    build_held_value_summary(con)

    tbl = pa.table({"trip_id": ["TA"], "final_stop_id": ["S2"], "final_stop_sequence": [2]})
    con.register("sfs_src", tbl)
    con.execute("CREATE OR REPLACE TABLE static_final_stop AS SELECT * FROM sfs_src")
    con.unregister("sfs_src")

    result = check3(con)

    assert result["n_total"] == 1
    assert result["n_completed"] == 1
    assert result["n_left_early"] == 0
    assert result["n_completed_with_final"] == 1
    assert result["removal_minus_final_arrival_percentiles"][50] == 1000 - 950


def test_trip_removed_with_a_future_stop_is_left_early():
    con = duckdb.connect()
    rows = [
        {
            "trip_id": "TB",
            "stop_id": "S3",
            "stop_sequence": 1,
            "header_timestamp": 1000,
            "held_time": 1100,
            "changed": True,
        },
    ]
    _register_held_series_annotated(con, rows)
    build_held_value_summary(con)

    tbl = pa.table({"trip_id": ["TB"], "final_stop_id": ["S3"], "final_stop_sequence": [1]})
    con.register("sfs_src", tbl)
    con.execute("CREATE OR REPLACE TABLE static_final_stop AS SELECT * FROM sfs_src")
    con.unregister("sfs_src")

    result = check3(con)

    assert result["n_total"] == 1
    assert result["n_completed"] == 0
    assert result["n_left_early"] == 1
    assert result["n_final_among_future"] == 1


# --------------------------------------------------------------------------
# Check 2: uncertainty cross-tab bucket assignment
# --------------------------------------------------------------------------


def test_uncertainty_cross_tab_bucket_assignment():
    con = duckdb.connect()
    # header_timestamp=1000 in every row.
    rows = [
        # absent, arrival_time=900 -> now-time=100 -> >60s_past
        (1000, True, 900, False, 0),
        # present_zero, arrival_time=970 -> now-time=30 -> 0-60s_past
        (1000, True, 970, True, 0),
        # present_nonzero, arrival_time=1010 -> now-time=-10 -> 0-60s_future
        (1000, True, 1010, True, 5),
    ]
    tbl = pa.table(
        {
            "header_timestamp": [r[0] for r in rows],
            "arrival_time_present": [r[1] for r in rows],
            "arrival_time": [r[2] for r in rows],
            "arrival_uncertainty_present": [r[3] for r in rows],
            "arrival_uncertainty": [r[4] for r in rows],
        }
    )
    con.register("src", tbl)
    con.execute("CREATE OR REPLACE TABLE held_value_rows AS SELECT * FROM src")
    con.unregister("src")

    result = check2_cross_tab(con, "arrival")

    cross_tab = {(bucket, time_bucket): n for bucket, time_bucket, n in result["cross_tab"]}
    assert cross_tab[("absent", ">60s_past")] == 1
    assert cross_tab[("present_zero", "0-60s_past")] == 1
    assert cross_tab[("present_nonzero", "0-60s_future")] == 1
    assert result["n_nonzero"] == 1
    assert result["nonzero_value_percentiles"][50] == 5


# --------------------------------------------------------------------------
# V2: passage detection via a hand-built vp_stop_distance / vp_eligible_stops
# --------------------------------------------------------------------------


def _register_vp_tables(
    con: duckdb.DuckDBPyConnection, eligible: list[dict], distances: list[dict]
) -> None:
    elig_tbl = pa.table(
        {
            "trip_id": [r["trip_id"] for r in eligible],
            "stop_id": [r["stop_id"] for r in eligible],
            "stop_sequence": [r["stop_sequence"] for r in eligible],
            "is_final_stop": [r["is_final_stop"] for r in eligible],
            "sched_arrival_utc": [r.get("sched_arrival_utc") for r in eligible],
            "sched_departure_utc": [r.get("sched_departure_utc") for r in eligible],
            "sched_ref_time": [r["sched_ref_time"] for r in eligible],
        }
    )
    con.register("elig_src", elig_tbl)
    con.execute("CREATE OR REPLACE TABLE vp_eligible_stops AS SELECT * FROM elig_src")
    con.unregister("elig_src")

    dist_tbl = pa.table(
        {
            "trip_id": [r["trip_id"] for r in distances],
            "stop_id": [r["stop_id"] for r in distances],
            "stop_sequence": [r["stop_sequence"] for r in distances],
            "is_final_stop": [r["is_final_stop"] for r in distances],
            "sched_ref_time": [r["sched_ref_time"] for r in distances],
            "vehicle_timestamp": [r["vehicle_timestamp"] for r in distances],
            "distance_m": [r["distance_m"] for r in distances],
        }
    )
    con.register("dist_src", dist_tbl)
    con.execute("CREATE OR REPLACE TABLE vp_stop_distance AS SELECT * FROM dist_src")
    con.unregister("dist_src")


def test_single_visit_with_dwell_gives_a_departure_window():
    con = duckdb.connect()
    eligible = [
        {
            "trip_id": "T1",
            "stop_id": "ST1",
            "stop_sequence": 1,
            "is_final_stop": False,
            "sched_ref_time": 110,
        }
    ]
    pings = [(100, 80), (110, 30), (120, 20), (130, 25), (140, 90)]
    distances = [
        {
            "trip_id": "T1",
            "stop_id": "ST1",
            "stop_sequence": 1,
            "is_final_stop": False,
            "sched_ref_time": 110,
            "vehicle_timestamp": ts,
            "distance_m": dist,
        }
        for ts, dist in pings
    ]
    _register_vp_tables(con, eligible, distances)

    result = v2_passage_at_radius(con, 50)

    assert result["n_eligible"] == 1
    assert result["n_detected"] == 1
    assert result["n_multi_visit"] == 0
    assert result["n_departure_windows"] == 1
    assert result["departure_window_percentiles"][50] == 140 - 130


def test_pass_through_with_no_ping_within_radius():
    con = duckdb.connect()
    eligible = [
        {
            "trip_id": "T2",
            "stop_id": "ST2",
            "stop_sequence": 1,
            "is_final_stop": False,
            "sched_ref_time": 110,
        }
    ]
    pings = [(100, 80), (110, 90), (120, 70)]
    distances = [
        {
            "trip_id": "T2",
            "stop_id": "ST2",
            "stop_sequence": 1,
            "is_final_stop": False,
            "sched_ref_time": 110,
            "vehicle_timestamp": ts,
            "distance_m": dist,
        }
        for ts, dist in pings
    ]
    _register_vp_tables(con, eligible, distances)

    result = v2_passage_at_radius(con, 50)

    assert result["n_eligible"] == 1
    assert result["n_detected"] == 0
    assert result["n_no_ping_within_r"] == 1


def test_two_visits_to_the_same_stop_is_multi_visit():
    con = duckdb.connect()
    eligible = [
        {
            "trip_id": "T3",
            "stop_id": "ST3",
            "stop_sequence": 1,
            "is_final_stop": False,
            "sched_ref_time": 100,
        }
    ]
    pings = [(100, 20), (110, 80), (120, 25)]
    distances = [
        {
            "trip_id": "T3",
            "stop_id": "ST3",
            "stop_sequence": 1,
            "is_final_stop": False,
            "sched_ref_time": 100,
            "vehicle_timestamp": ts,
            "distance_m": dist,
        }
        for ts, dist in pings
    ]
    _register_vp_tables(con, eligible, distances)

    result = v2_passage_at_radius(con, 50)

    assert result["n_eligible"] == 1
    assert result["n_detected"] == 1
    assert result["n_multi_visit"] == 1


def test_final_stop_arrival_window():
    con = duckdb.connect()
    eligible = [
        {
            "trip_id": "T4",
            "stop_id": "ST4",
            "stop_sequence": 5,
            "is_final_stop": True,
            "sched_ref_time": 220,
        }
    ]
    pings = [(200, 90), (210, 20), (220, 15), (230, 25), (240, 85)]
    distances = [
        {
            "trip_id": "T4",
            "stop_id": "ST4",
            "stop_sequence": 5,
            "is_final_stop": True,
            "sched_ref_time": 220,
            "vehicle_timestamp": ts,
            "distance_m": dist,
        }
        for ts, dist in pings
    ]
    _register_vp_tables(con, eligible, distances)

    result = v2_passage_at_radius(con, 50)

    assert result["n_arrival_windows"] == 1
    assert result["arrival_window_percentiles"][50] == 210 - 200


# --------------------------------------------------------------------------
# Haversine distance
# --------------------------------------------------------------------------


def test_haversine_matches_known_equatorial_distance():
    # One degree of longitude at the equator, using Earth radius 6371000m (the
    # same radius HAVERSINE_SQL uses): circumference / 360.
    expected = (2 * math.pi * 6_371_000) / 360

    con = duckdb.connect()
    dist_expr = HAVERSINE_SQL.format(lat1="0.0", lon1="0.0", lat2="0.0", lon2="1.0")
    (result,) = con.execute(f"SELECT {dist_expr}").fetchone()

    assert abs(result - expected) < 1.0


def test_haversine_zero_distance_for_identical_points():
    con = duckdb.connect()
    dist_expr = HAVERSINE_SQL.format(lat1="59.3293", lon1="18.0686", lat2="59.3293", lon2="18.0686")
    (result,) = con.execute(f"SELECT {dist_expr}").fetchone()

    assert abs(result) < 1e-6


# --------------------------------------------------------------------------
# Looping trip: same stop_id visited at two stop_sequences must produce two
# distinct stop events, not one merged event (the bug this commit fixes).
# --------------------------------------------------------------------------


def test_looping_trip_same_stop_id_two_sequences_produces_two_events():
    con = duckdb.connect()
    eligible = [
        {
            "trip_id": "TL",
            "stop_id": "SLOOP",
            "stop_sequence": 2,
            "is_final_stop": False,
            "sched_ref_time": 110,
        },
        {
            "trip_id": "TL",
            "stop_id": "SLOOP",
            "stop_sequence": 9,
            "is_final_stop": False,
            "sched_ref_time": 500,
        },
    ]
    # Both physical visits (near t=110 and near t=500) appear under both
    # stop_sequence tags, mirroring the cross join in build_vp_stop_distance:
    # every ping is tested against every eligible stop_sequence at that
    # stop_id, since a looping route can revisit the same physical stop.
    visit_a = [(100, 80), (110, 30), (120, 20), (130, 25), (140, 90)]
    visit_b = [(490, 85), (500, 25), (510, 15), (520, 30), (530, 95)]
    distances = [
        {
            "trip_id": "TL",
            "stop_id": "SLOOP",
            "stop_sequence": seq,
            "is_final_stop": False,
            "sched_ref_time": 110 if seq == 2 else 500,
            "vehicle_timestamp": ts,
            "distance_m": dist,
        }
        for seq in (2, 9)
        for ts, dist in visit_a + visit_b
    ]
    _register_vp_tables(con, eligible, distances)

    result = v2_passage_at_radius(con, 50)

    assert result["n_eligible"] == 2
    assert result["n_detected"] == 2
    assert result["n_multi_visit"] == 2

    chosen = con.execute(
        "SELECT stop_sequence, visit_start FROM vp_chosen_visit WHERE rn = 1 ORDER BY stop_sequence"
    ).fetchall()
    assert chosen == [(2, 110), (9, 500)]


# --------------------------------------------------------------------------
# assert_unique: fails loudly on duplicated (trip, stop_sequence) keys
# --------------------------------------------------------------------------


def test_assert_unique_fires_on_duplicated_rows():
    con = duckdb.connect()
    tbl = pa.table({"trip_id": ["T1", "T1"], "stop_sequence": [1, 1]})
    con.register("src", tbl)
    con.execute("CREATE OR REPLACE TABLE dup_table AS SELECT * FROM src")
    con.unregister("src")

    with pytest.raises(AssertionError):
        assert_unique(con, "dup_table", ["trip_id", "stop_sequence"])


def test_assert_unique_passes_on_unique_rows():
    con = duckdb.connect()
    tbl = pa.table({"trip_id": ["T1", "T1"], "stop_sequence": [1, 2]})
    con.register("src", tbl)
    con.execute("CREATE OR REPLACE TABLE ok_table AS SELECT * FROM src")
    con.unregister("src")

    assert_unique(con, "ok_table", ["trip_id", "stop_sequence"])  # must not raise


# --------------------------------------------------------------------------
# VP reconciliation: stage counts must never increase
# --------------------------------------------------------------------------


def _register_reconciliation_tables(
    con: duckdb.DuckDBPyConnection,
    held_rows: list[tuple],
    eligible_rows: list[tuple],
    visit_rows: list[tuple],
    v3_rows: list[tuple],
) -> None:
    for table, rows in (
        ("held_value_check1", held_rows),
        ("vp_eligible_stops", eligible_rows),
        ("vp_visits", visit_rows),
        ("v3_offsets", v3_rows),
    ):
        tbl = pa.table(
            {
                "trip_id": [r[0] for r in rows],
                "stop_sequence": [r[1] for r in rows],
            }
        )
        con.register("src", tbl)
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM src")
        con.unregister("src")


def test_reconciliation_passes_when_each_stage_does_not_exceed_the_last():
    con = duckdb.connect()
    _register_reconciliation_tables(
        con,
        held_rows=[("T1", 1), ("T1", 2), ("T1", 3)],
        eligible_rows=[("T1", 1), ("T1", 2)],
        visit_rows=[("T1", 1), ("T1", 1)],  # two visit rows, one distinct event
        v3_rows=[("T1", 1)],
    )

    result = build_vp_reconciliation(con, n_v4=1)

    assert result == {"n_held_values": 3, "n_eligible": 2, "n_v2": 1, "n_v3": 1, "n_v4": 1}


def test_reconciliation_fails_loudly_when_a_later_stage_exceeds_an_earlier_one():
    con = duckdb.connect()
    _register_reconciliation_tables(
        con,
        held_rows=[("T1", 1)],
        eligible_rows=[("T1", 1), ("T1", 2)],  # more eligible events than held values
        visit_rows=[("T1", 1)],
        v3_rows=[("T1", 1)],
    )

    with pytest.raises(AssertionError):
        build_vp_reconciliation(con, n_v4=1)


def test_reconciliation_fails_loudly_when_v3_and_v4_counts_disagree():
    con = duckdb.connect()
    _register_reconciliation_tables(
        con,
        held_rows=[("T1", 1), ("T1", 2)],
        eligible_rows=[("T1", 1), ("T1", 2)],
        visit_rows=[("T1", 1), ("T1", 2)],
        v3_rows=[("T1", 1), ("T1", 2)],
    )

    with pytest.raises(AssertionError):
        build_vp_reconciliation(con, n_v4=1)


# --------------------------------------------------------------------------
# Stops that reappeared after dropping: the held value must come from the
# final appearance, even when an earlier snapshot's value differs.
# --------------------------------------------------------------------------


def test_held_value_is_taken_from_the_final_appearance_after_a_reappearance():
    con = duckdb.connect()
    rows = [
        {
            "trip_id": "TR",
            "stop_id": "S1",
            "stop_sequence": 1,
            "header_timestamp": 100,
            "held_time": 500,
            "changed": True,
        },
        # gap at ts=120: the stop dropped (no row - absence isn't a row here)
        {
            "trip_id": "TR",
            "stop_id": "S1",
            "stop_sequence": 1,
            "header_timestamp": 140,
            "held_time": 999,
            "changed": True,
        },
        # dropped again after ts=140 for good
    ]
    _register_held_series_annotated(con, rows)

    build_held_value_summary(con)

    held_value, held_ts = con.execute(
        "SELECT held_value, held_ts FROM held_value_check1"
    ).fetchone()

    assert held_value == 999
    assert held_ts == 140


def test_check7_compares_pre_drop_value_against_the_final_appearance():
    con = duckdb.connect()
    rows = [
        {
            "trip_id": "TR",
            "stop_id": "S1",
            "stop_sequence": 1,
            "header_timestamp": 100,
            "held_time": 500,
            "held_uncertainty_present": True,
            "changed": True,
        },
        {
            "trip_id": "TR",
            "stop_id": "S1",
            "stop_sequence": 1,
            "header_timestamp": 140,
            "held_time": 560,
            "held_uncertainty_present": False,
            "changed": True,
        },
    ]
    _register_held_series_annotated(con, rows)
    build_held_value_summary(con)

    snap_tbl = pa.table(
        {
            "trip_id": ["TR", "TR", "TR", "TR"],
            "start_date": pa.array([None, None, None, None], type=pa.string()),
            "header_timestamp": [100, 120, 140, 160],
            "stop_seqs": [[1], [], [1], []],
        }
    )
    con.register("snap_src", snap_tbl)
    con.execute("CREATE OR REPLACE TABLE held_trip_snapshot_stops AS SELECT * FROM snap_src")
    con.unregister("snap_src")

    result = check7_reappeared_stops(con)

    assert result["n"] == 1
    assert result["n_differs"] == 1
    assert result["diff_percentiles"][50] == 60
    assert result["marker_matrix"] == {(True, False): 1}
