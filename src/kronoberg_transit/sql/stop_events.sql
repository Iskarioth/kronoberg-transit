-- One row per stop_times row of a trip active on D. Status is checked in
-- order out_of_scope (D-013) -> cancelled -> skipped -> observed ->
-- unobserved (D-009) and applies to non-final stops only; final stops carry
-- their held values and marker but no status (D-008). delay_s is set only
-- when status = observed. in_scope is copied straight from the trip.
--
-- Requires the `scheduled_time_utc(hms)` scalar UDF to be registered first,
-- and the `trips` table to already exist.
-- Params: $svc_date (ISO date, e.g. 2026-09-07)

CREATE OR REPLACE TABLE stop_events AS
WITH base AS (
    SELECT
        st.trip_id,
        sched.route_id,
        st.stop_sequence,
        st.stop_id,
        CASE
            WHEN st.stop_sequence = b.first_stop_sequence THEN 'first'
            WHEN st.stop_sequence = b.last_stop_sequence THEN 'final'
            ELSE 'intermediate'
        END AS stop_position,
        (st.stop_sequence = b.last_stop_sequence) AS is_final,
        to_timestamp(scheduled_time_utc(st.arrival_time))::TIMESTAMP AS scheduled_arrival_utc,
        to_timestamp(scheduled_time_utc(st.departure_time))::TIMESTAMP AS scheduled_departure_utc,
        CASE WHEN sla.held_arrival_time IS NOT NULL
             THEN to_timestamp(sla.held_arrival_time)::TIMESTAMP END AS held_arrival_utc,
        CASE WHEN sla.held_departure_time IS NOT NULL
             THEN to_timestamp(sla.held_departure_time)::TIMESTAMP END AS held_departure_utc,
        CASE WHEN sla.held_arrival_time IS NULL THEN NULL
             ELSE (sla.held_arrival_uncertainty_present AND sla.held_arrival_uncertainty = 0)
        END AS arrival_marker,
        CASE WHEN sla.held_departure_time IS NULL THEN NULL
             ELSE (sla.held_departure_uncertainty_present AND sla.held_departure_uncertainty = 0)
        END AS departure_marker,
        sla.last_stop_relationship,
        CASE WHEN sla.last_seen_ts IS NOT NULL
             THEN to_timestamp(sla.last_seen_ts)::TIMESTAMP END AS last_seen_utc,
        t.trip_status,
        t.in_scope
    FROM scheduled_stop_times st
    JOIN scheduled_trips sched ON sched.trip_id = st.trip_id
    JOIN trip_stop_bounds b ON b.trip_id = st.trip_id
    JOIN trips t ON t.trip_id = st.trip_id
    LEFT JOIN stop_last_appearance sla ON sla.trip_id = st.trip_id AND sla.stop_sequence = st.stop_sequence
),
with_status AS (
    SELECT *,
        CASE
            WHEN is_final THEN NULL
            WHEN NOT in_scope THEN 'out_of_scope'
            WHEN trip_status = 'cancelled' THEN 'cancelled'
            WHEN last_stop_relationship = 'SKIPPED' THEN 'skipped'
            WHEN departure_marker THEN 'observed'
            ELSE 'unobserved'
        END AS status
    FROM base
)
SELECT
    DATE '$svc_date' AS service_date,
    trip_id,
    route_id,
    stop_sequence,
    stop_id,
    stop_position,
    scheduled_arrival_utc,
    scheduled_departure_utc,
    held_arrival_utc,
    held_departure_utc,
    arrival_marker,
    departure_marker,
    last_stop_relationship,
    last_seen_utc,
    in_scope,
    status,
    CASE WHEN status = 'observed'
         THEN date_diff('second', scheduled_departure_utc, held_departure_utc)
    END AS delay_s
FROM with_status;
