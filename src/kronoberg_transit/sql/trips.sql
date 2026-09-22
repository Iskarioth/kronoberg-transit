-- One row per trip scheduled on D. trip_status: out_of_scope if the trip is
-- out of scope (D-013, takes precedence over everything else); cancelled if
-- the trip's last appearance is CANCELED (D-009); no_realtime_data if it
-- never appeared with start_date D (D-010); otherwise in_feed. first_seen_utc
-- and last_seen_utc are populated whenever the trip appeared, regardless of
-- scope. no_data_in_outage (D-016) is true for a no_realtime_data trip whose
-- whole scheduled span falls inside one feed_gaps window, false for other
-- no_realtime_data trips, null otherwise.
--
-- Requires the `scheduled_time_utc(hms)` scalar UDF and the `feed_gaps` table
-- to already exist.
-- Params: $svc_date (ISO date, e.g. 2026-09-07)

CREATE OR REPLACE TABLE trips AS
WITH base AS (
    SELECT
        st.trip_id,
        st.route_id,
        st.direction_id,
        to_timestamp(scheduled_time_utc(ff.departure_time))::TIMESTAMP AS scheduled_first_departure_utc,
        to_timestamp(scheduled_time_utc(fl.arrival_time))::TIMESTAMP AS scheduled_last_arrival_utc,
        b.scheduled_stops,
        ts.operator,
        ts.in_scope,
        CASE
            WHEN NOT ts.in_scope THEN 'out_of_scope'
            WHEN tla.trip_id IS NULL THEN 'no_realtime_data'
            WHEN tla.last_trip_schedule_relationship = 'CANCELED' THEN 'cancelled'
            ELSE 'in_feed'
        END AS trip_status,
        CASE WHEN tla.first_seen_ts IS NOT NULL THEN to_timestamp(tla.first_seen_ts)::TIMESTAMP END AS first_seen_utc,
        CASE WHEN tla.last_seen_ts IS NOT NULL THEN to_timestamp(tla.last_seen_ts)::TIMESTAMP END AS last_seen_utc
    FROM scheduled_trips st
    JOIN trip_stop_bounds b ON b.trip_id = st.trip_id
    JOIN static_first_stop ff ON ff.trip_id = st.trip_id
    JOIN static_final_stop fl ON fl.trip_id = st.trip_id
    JOIN trip_scope ts ON ts.trip_id = st.trip_id
    LEFT JOIN trip_last_appearance tla ON tla.trip_id = st.trip_id
)
SELECT
    DATE '$svc_date' AS service_date,
    trip_id,
    route_id,
    direction_id,
    scheduled_first_departure_utc,
    scheduled_last_arrival_utc,
    scheduled_stops,
    operator,
    in_scope,
    trip_status,
    first_seen_utc,
    last_seen_utc,
    CASE
        WHEN trip_status != 'no_realtime_data' THEN NULL
        WHEN EXISTS (
            SELECT 1 FROM feed_gaps fg
            WHERE fg.gap_start_utc <= base.scheduled_first_departure_utc
              AND base.scheduled_last_arrival_utc <= fg.gap_end_utc
        ) THEN TRUE
        ELSE FALSE
    END AS no_data_in_outage
FROM base;
