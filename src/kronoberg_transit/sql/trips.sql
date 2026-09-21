-- One row per trip scheduled on D. trip_status: cancelled if the trip's last
-- appearance is CANCELED (D-009); no_realtime_data if it never appeared with
-- start_date D (D-010); otherwise in_feed.
--
-- Requires the `scheduled_time_utc(hms)` scalar UDF to be registered first.
-- Params: $svc_date (ISO date, e.g. 2026-09-07)

CREATE OR REPLACE TABLE trips AS
SELECT
    DATE '$svc_date' AS service_date,
    st.trip_id,
    st.route_id,
    st.direction_id,
    to_timestamp(scheduled_time_utc(ff.departure_time))::TIMESTAMP AS scheduled_first_departure_utc,
    to_timestamp(scheduled_time_utc(fl.arrival_time))::TIMESTAMP AS scheduled_last_arrival_utc,
    b.scheduled_stops,
    CASE
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
LEFT JOIN trip_last_appearance tla ON tla.trip_id = st.trip_id;
