-- Load a service date's static schedule and compute the trips active on it.
--
-- GTFS active-service rule: a service_id is active on a date if calendar.txt
-- says so for that weekday and the date falls in [start_date, end_date], minus
-- any calendar_dates.txt exception_type=2 (removed) for that date, unioned
-- with any calendar_dates.txt exception_type=1 (added) for that date. In this
-- feed calendar.txt's weekday flags are always 0 (all service is expressed as
-- calendar_dates exceptions), so the UNION branch does the actual work, but
-- the full rule is implemented for correctness.
--
-- Trip scope (D-013): a trip's operator is the organization_name of its
-- attributions.txt row with is_operator = 1. A trip is out of scope when
-- that name exactly matches the agency_name of an agency.txt row other than
-- Länstrafiken Kronoberg. A trip without an operator row is in scope.
--
-- Params: $static_dir, $date_int (e.g. 20260907), $weekday_col (e.g. sunday)

CREATE OR REPLACE TABLE static_trips AS
SELECT * FROM read_csv(
    '$static_dir/trips.txt', header=true, quote='"', escape='"', delim=',', sample_size=-1,
    types={
        'route_id': 'VARCHAR', 'service_id': 'VARCHAR', 'trip_id': 'VARCHAR',
        'direction_id': 'VARCHAR', 'shape_id': 'VARCHAR'
    }
);

CREATE OR REPLACE TABLE static_stop_times AS
SELECT * FROM read_csv(
    '$static_dir/stop_times.txt', header=true, quote='"', escape='"', delim=',', sample_size=-1,
    types={
        'trip_id': 'VARCHAR', 'stop_id': 'VARCHAR',
        'arrival_time': 'VARCHAR', 'departure_time': 'VARCHAR',
        'pickup_booking_rule_id': 'VARCHAR', 'drop_off_booking_rule_id': 'VARCHAR',
        'timepoint': 'VARCHAR'
    }
);

CREATE OR REPLACE TABLE static_calendar AS
SELECT * FROM read_csv(
    '$static_dir/calendar.txt', header=true, quote='"', escape='"', delim=',', sample_size=-1,
    types={'service_id': 'VARCHAR'}
);

CREATE OR REPLACE TABLE static_calendar_dates AS
SELECT * FROM read_csv(
    '$static_dir/calendar_dates.txt', header=true, quote='"', escape='"', delim=',', sample_size=-1,
    types={'service_id': 'VARCHAR'}
);

CREATE OR REPLACE TABLE static_routes AS
SELECT * FROM read_csv(
    '$static_dir/routes.txt', header=true, quote='"', escape='"', delim=',', sample_size=-1,
    types={'route_id': 'VARCHAR', 'agency_id': 'VARCHAR'}
);

CREATE OR REPLACE TABLE static_stops AS
SELECT * FROM read_csv(
    '$static_dir/stops.txt', header=true, quote='"', escape='"', delim=',', sample_size=-1,
    types={'stop_id': 'VARCHAR', 'parent_station': 'VARCHAR', 'stop_lat': 'DOUBLE', 'stop_lon': 'DOUBLE'}
);

CREATE OR REPLACE TABLE static_agency AS
SELECT * FROM read_csv(
    '$static_dir/agency.txt', header=true, quote='"', escape='"', delim=',', sample_size=-1,
    types={'agency_id': 'VARCHAR'}
);

CREATE OR REPLACE TABLE static_attributions AS
SELECT * FROM read_csv(
    '$static_dir/attributions.txt', header=true, quote='"', escape='"', delim=',', sample_size=-1,
    types={'trip_id': 'VARCHAR', 'is_operator': 'INTEGER'}
);

CREATE OR REPLACE VIEW active_service AS
SELECT service_id FROM static_calendar
WHERE $weekday_col = 1 AND start_date <= $date_int AND end_date >= $date_int
  AND service_id NOT IN (
      SELECT service_id FROM static_calendar_dates WHERE date = $date_int AND exception_type = 2
  )
UNION
SELECT service_id FROM static_calendar_dates WHERE date = $date_int AND exception_type = 1;

CREATE OR REPLACE TABLE scheduled_trips AS
SELECT t.trip_id, t.route_id, t.service_id, TRY_CAST(NULLIF(t.direction_id, '') AS INTEGER) AS direction_id
FROM static_trips t
JOIN active_service s USING (service_id);

-- One row per (trip, operator) among is_operator=1 attribution rows, with a
-- count so the caller can fail the run if a trip has more than one.
CREATE OR REPLACE TABLE trip_operator AS
SELECT trip_id, organization_name AS operator,
       COUNT(*) OVER (PARTITION BY trip_id) AS n_operator_rows
FROM static_attributions
WHERE is_operator = 1;

CREATE OR REPLACE TABLE other_agency_names AS
SELECT agency_name FROM static_agency WHERE agency_name != 'Länstrafiken Kronoberg';

CREATE OR REPLACE TABLE trip_scope AS
SELECT
    st.trip_id,
    op.operator,
    op.n_operator_rows,
    (op.operator IS NULL OR op.operator NOT IN (SELECT agency_name FROM other_agency_names)) AS in_scope
FROM scheduled_trips st
LEFT JOIN trip_operator op ON op.trip_id = st.trip_id;

CREATE OR REPLACE TABLE scheduled_stop_times AS
SELECT st.*
FROM static_stop_times st
JOIN scheduled_trips t ON t.trip_id = st.trip_id;

CREATE OR REPLACE TABLE trip_stop_bounds AS
SELECT trip_id,
       COUNT(*) AS scheduled_stops,
       MIN(stop_sequence) AS first_stop_sequence,
       MAX(stop_sequence) AS last_stop_sequence
FROM scheduled_stop_times
GROUP BY trip_id;

CREATE OR REPLACE TABLE static_first_stop AS
SELECT st.trip_id, st.stop_sequence, st.departure_time
FROM scheduled_stop_times st
JOIN trip_stop_bounds b ON b.trip_id = st.trip_id AND b.first_stop_sequence = st.stop_sequence;

CREATE OR REPLACE TABLE static_final_stop AS
SELECT st.trip_id, st.stop_sequence, st.arrival_time
FROM scheduled_stop_times st
JOIN trip_stop_bounds b ON b.trip_id = st.trip_id AND b.last_stop_sequence = st.stop_sequence;
