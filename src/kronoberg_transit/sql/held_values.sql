-- Realtime trips matched to service date D (D-011: match on trip_id and
-- start_date) and, per matched (trip, stop_sequence), the held value from the
-- last snapshot in which it appears (D-007), across every archive read for D
-- (D's own hours plus any D+1 hours read).
--
-- Params: $date_str (GTFS start_date format, e.g. '20260907')

CREATE OR REPLACE TABLE realtime_trip_rows AS
SELECT * FROM rows_dedup WHERE trip_id IS NOT NULL AND start_date = '$date_str';

CREATE OR REPLACE TABLE ignored_prior_day_rows AS
SELECT * FROM rows_dedup WHERE trip_id IS NOT NULL AND start_date != '$date_str';

CREATE OR REPLACE TABLE trip_last_appearance AS
SELECT trip_id,
       ARG_MAX(trip_schedule_relationship, header_timestamp) AS last_trip_schedule_relationship,
       MIN(header_timestamp) AS first_seen_ts,
       MAX(header_timestamp) AS last_seen_ts
FROM realtime_trip_rows
GROUP BY trip_id;

-- One row per (trip, stop_sequence) that appeared with a stop_time_update at
-- least once: every field below comes from the single snapshot with the
-- highest header_timestamp (the last appearance, per D-007), not from
-- independently-chosen per-field snapshots. Held values use `time`, not
-- `time - delay`: docs/validation confirms the two are identical.
CREATE OR REPLACE TABLE stop_last_appearance AS
SELECT trip_id,
       stop_sequence,
       stop_id,
       stop_schedule_relationship AS last_stop_relationship,
       CASE WHEN arrival_time_present THEN arrival_time END AS held_arrival_time,
       CASE WHEN arrival_time_present THEN arrival_uncertainty_present END AS held_arrival_uncertainty_present,
       CASE WHEN arrival_time_present THEN arrival_uncertainty END AS held_arrival_uncertainty,
       CASE WHEN departure_time_present THEN departure_time END AS held_departure_time,
       CASE WHEN departure_time_present THEN departure_uncertainty_present END AS held_departure_uncertainty_present,
       CASE WHEN departure_time_present THEN departure_uncertainty END AS held_departure_uncertainty,
       header_timestamp AS last_seen_ts
FROM realtime_trip_rows
WHERE stop_id IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY trip_id, stop_sequence ORDER BY header_timestamp DESC) = 1;
