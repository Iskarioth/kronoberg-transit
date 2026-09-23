-- D-021: schedule mismatches, unmatched realtime trips, and dst_ambiguous
-- departures for feed_quality.
--
-- schedule_mismatch_*: compares the feed's own scheduled instant (time -
-- delay) against the pipeline's already-computed scheduled time
-- (stop_events.scheduled_*_utc), for every stop_time_update on a trip
-- assigned to D (realtime_trip_rows, start_date = D per D-011/
-- held_values.sql) that matches a scheduled trip on D, outside the
-- daylight-saving window (stop_events.status != 'dst_ambiguous'). Arrival
-- and departure are checked separately. Joins to stop_events' already-
-- computed scheduled times instead of calling the scheduled_time_utc Python
-- UDF per row. This counts matched trips only, by definition - a realtime
-- trip that matches no scheduled trip on D (e.g. tagged with the wrong
-- service date) is not, and cannot be, a schedule mismatch: with no
-- scheduled time to compare against, it is counted separately below.
--
-- unmatched_realtime_trips: distinct realtime trips with start_date = D
-- (D-011) that match no scheduled trip active on D (per calendar/
-- calendar_dates, i.e. not in scheduled_trips). The feed cannot tell a
-- genuinely added trip from one tagged with the wrong service date.
--
-- Requires realtime_trip_rows (held_values.sql), scheduled_trips
-- (static_schedule.sql) and stop_events to exist.

CREATE OR REPLACE TABLE dst_mismatch_checks AS
WITH arr AS (
    SELECT r.trip_id, r.stop_sequence,
           r.arrival_time - r.arrival_delay AS feed_scheduled_utc,
           epoch(se.scheduled_arrival_utc) AS pipeline_scheduled_utc
    FROM realtime_trip_rows r
    JOIN stop_events se ON se.trip_id = r.trip_id AND se.stop_sequence = r.stop_sequence
    WHERE r.arrival_time_present AND r.arrival_delay_present
      AND r.stop_sequence IS NOT NULL AND se.status != 'dst_ambiguous'
),
dep AS (
    SELECT r.trip_id, r.stop_sequence,
           r.departure_time - r.departure_delay AS feed_scheduled_utc,
           epoch(se.scheduled_departure_utc) AS pipeline_scheduled_utc
    FROM realtime_trip_rows r
    JOIN stop_events se ON se.trip_id = r.trip_id AND se.stop_sequence = r.stop_sequence
    WHERE r.departure_time_present AND r.departure_delay_present
      AND r.stop_sequence IS NOT NULL AND se.status != 'dst_ambiguous'
),
checks AS (SELECT * FROM arr UNION ALL SELECT * FROM dep)
SELECT trip_id, stop_sequence, feed_scheduled_utc, pipeline_scheduled_utc
FROM checks
WHERE pipeline_scheduled_utc IS DISTINCT FROM feed_scheduled_utc;

CREATE OR REPLACE TABLE schedule_mismatch_summary AS
SELECT
    COUNT(*) AS schedule_mismatch_updates,
    COUNT(DISTINCT (trip_id, stop_sequence)) AS schedule_mismatch_stop_events
FROM dst_mismatch_checks;

CREATE OR REPLACE TABLE unmatched_realtime_trips_summary AS
SELECT COUNT(DISTINCT trip_id) AS unmatched_realtime_trips
FROM realtime_trip_rows
WHERE trip_id NOT IN (SELECT trip_id FROM scheduled_trips);
