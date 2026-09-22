-- data_quality (D-017): one row per service date. Snapshot/outage figures
-- (distinct/duplicate snapshots, first/last snapshot, local_hours_without_
-- snapshots, out_of_scope_trips_in_feed) come from feed_quality (D's own
-- archives only, unchanged by D-016). max_gap_s, largest_gap_start/end_local
-- and outages come from feed_gaps instead (D's own hours plus any D+1 hours
-- read, per D-016), since that is what actually bounds an outage. Requires
-- aggregate_base.sql to have run first.

CREATE OR REPLACE TABLE gap_agg AS
SELECT service_date,
       COUNT(*) AS outages,
       MAX(gap_s) AS max_gap_s
FROM all_feed_gaps
GROUP BY service_date;

CREATE OR REPLACE TABLE largest_gap AS
SELECT service_date, gap_start_utc, gap_end_utc
FROM (
    SELECT service_date, gap_start_utc, gap_end_utc,
           ROW_NUMBER() OVER (PARTITION BY service_date ORDER BY gap_s DESC) AS rn
    FROM all_feed_gaps
) WHERE rn = 1;

-- Overlap, in minutes, between each date's feed_gaps windows and that same
-- date's local 06:00-22:00 window.
CREATE OR REPLACE TABLE outage_minutes_06_22_agg AS
WITH day_window AS (
    SELECT service_date,
           ((service_date + INTERVAL 6 HOUR) AT TIME ZONE 'Europe/Stockholm') AT TIME ZONE 'UTC' AS window_start_utc,
           ((service_date + INTERVAL 22 HOUR) AT TIME ZONE 'Europe/Stockholm') AT TIME ZONE 'UTC' AS window_end_utc
    FROM service_dates
)
SELECT fg.service_date,
       SUM(GREATEST(0, date_diff('second',
           GREATEST(fg.gap_start_utc, dw.window_start_utc),
           LEAST(fg.gap_end_utc, dw.window_end_utc)
       ))) / 60.0 AS outage_minutes_06_22
FROM all_feed_gaps fg
JOIN day_window dw ON dw.service_date = fg.service_date
GROUP BY fg.service_date;

CREATE OR REPLACE TABLE marker_agg AS
SELECT service_date,
       COUNT(*) FILTER (WHERE held_departure_utc IS NOT NULL) AS with_held_value,
       COUNT(*) FILTER (WHERE held_departure_utc IS NOT NULL AND departure_marker) AS with_marker
FROM all_stop_events
WHERE stop_position != 'final'
GROUP BY service_date;

CREATE OR REPLACE TABLE trip_agg_dq AS
SELECT service_date,
       COUNT(*) FILTER (WHERE in_scope) AS in_scope_trips,
       COUNT(*) FILTER (WHERE trip_status = 'no_realtime_data') AS trips_no_realtime_data,
       COUNT(*) FILTER (WHERE no_data_in_outage) AS trips_no_realtime_data_in_outage,
       COUNT(*) FILTER (WHERE trip_status = 'cancelled') AS cancelled_trips
FROM all_trips
GROUP BY service_date;

CREATE OR REPLACE TABLE coverage_agg AS
SELECT service_date,
       COUNT(*) FILTER (WHERE status IN ('observed', 'unobserved')) AS eligible_departures,
       COUNT(*) FILTER (WHERE status = 'observed') AS observed_departures
FROM all_stop_events
WHERE stop_position != 'final' AND in_scope
GROUP BY service_date;

CREATE OR REPLACE TABLE data_quality AS
SELECT
    dt.service_date, dt.day_type,
    fq.distinct_snapshots, fq.duplicate_snapshots,
    strftime(fq.first_snapshot_utc AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm', '%Y-%m-%dT%H:%M:%S') AS first_snapshot_local,
    strftime(fq.last_snapshot_utc AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm', '%Y-%m-%dT%H:%M:%S') AS last_snapshot_local,
    ga.max_gap_s,
    strftime(lg.gap_start_utc AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm', '%Y-%m-%dT%H:%M:%S') AS largest_gap_start_local,
    strftime(lg.gap_end_utc AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm', '%Y-%m-%dT%H:%M:%S') AS largest_gap_end_local,
    ga.outages,
    ROUND(COALESCE(om.outage_minutes_06_22, 0), 2) AS outage_minutes_06_22,
    fq.local_hours_without_snapshots,
    ROUND(ma.with_marker::DOUBLE / NULLIF(ma.with_held_value, 0), 4) AS marker_share,
    fq.out_of_scope_trips_in_feed,
    ta.in_scope_trips, ta.trips_no_realtime_data, ta.trips_no_realtime_data_in_outage, ta.cancelled_trips,
    ROUND(ca.observed_departures::DOUBLE / NULLIF(ca.eligible_departures, 0), 4) AS coverage_share
FROM day_types dt
JOIN all_feed_quality fq ON fq.service_date = dt.service_date
JOIN gap_agg ga ON ga.service_date = dt.service_date
JOIN largest_gap lg ON lg.service_date = dt.service_date
LEFT JOIN outage_minutes_06_22_agg om ON om.service_date = dt.service_date
JOIN marker_agg ma ON ma.service_date = dt.service_date
JOIN trip_agg_dq ta ON ta.service_date = dt.service_date
JOIN coverage_agg ca ON ca.service_date = dt.service_date;
