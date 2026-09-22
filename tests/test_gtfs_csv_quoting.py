"""Regression test for reading GTFS CSVs with a quoted, comma-containing field.

Fixture cut from data/static/krono_2026-09-06.zip: trip 76110000040138578
(route 69, 2026-09-07) has every stop_headsign quoted with an embedded comma
("Bergvägen, Ljungby"). Without quote='"', escape='"', DuckDB's default quote
detection mis-parses these rows, shifting every column after stop_headsign.
"""

from pathlib import Path

import duckdb

from kronoberg_transit.transform import render_sql

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "transform" / "static_quoting_2026-09-07"

TRIP_ID = "76110000040138578"
EXPECTED_STOPS = [
    (1, "9022007081328002", "07:25:00", "07:25:00"),
    (2, "9022007081327001", "07:27:22", "07:27:22"),
    (3, "9022007081355001", "07:28:42", "07:28:42"),
    (4, "9022007081348001", "07:30:27", "07:30:27"),
    (5, "9022007081347001", "07:33:00", "07:33:00"),
]


def test_quoted_comma_stop_headsign_does_not_shift_columns():
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute(
        render_sql(
            "static_schedule.sql",
            static_dir=FIXTURES.as_posix(),
            date_int=20260907,
            weekday_col="monday",
        )
    )

    rows = con.execute(
        "SELECT stop_sequence, stop_id, arrival_time, departure_time, stop_headsign "
        "FROM scheduled_stop_times WHERE trip_id = ? ORDER BY stop_sequence",
        [TRIP_ID],
    ).fetchall()

    assert [r[:4] for r in rows] == EXPECTED_STOPS
    assert all(headsign == "Bergvägen, Ljungby" for *_, headsign in rows)
