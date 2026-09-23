-- data_quality (D-017): one row per service date. Snapshot figures
-- (distinct/duplicate snapshots, first/last snapshot, local_hours_without_
-- snapshots, out_of_scope_trips_in_feed) come from feed_quality (D's own
-- archives only, unchanged by D-016). The outage columns (longest_outage_s,
-- longest_outage_start/end_local, outages, outage_minutes_06_22) come from
-- feed_gaps (D-016) *clipped to D's own local day* (00:00-24:00 local): a
-- feed_gaps window can extend into the D+1 hours read for D-011, which would
-- otherwise attribute part of tomorrow's gap to today. feed_gaps itself and
-- trips.no_data_in_outage are untouched - the D+1 window is right for judging
-- D's trips. Requires aggregate_base.sql to have run first.

CREATE OR REPLACE TABLE day_bounds AS
SELECT service_date,
       ((service_date + INTERVAL 0 HOUR) AT TIME ZONE 'Europe/Stockholm') AT TIME ZONE 'UTC' AS day_start_utc,
       ((service_date + INTERVAL 24 HOUR) AT TIME ZONE 'Europe/Stockholm') AT TIME ZONE 'UTC' AS day_end_utc
FROM service_dates;

-- feed_gaps windows clipped to [day_start_utc, day_end_utc); a window that
-- falls entirely outside D's own day (e.g. an after_last gap that starts
-- after D+1's midnight) clips to zero-or-negative length and is dropped.
CREATE OR REPLACE TABLE feed_gaps_clipped AS
SELECT fg.service_date,
       GREATEST(fg.gap_start_utc, db.day_start_utc) AS gap_start_utc,
       LEAST(fg.gap_end_utc, db.day_end_utc) AS gap_end_utc,
       date_diff('second', GREATEST(fg.gap_start_utc, db.day_start_utc), LEAST(fg.gap_end_utc, db.day_end_utc)) AS gap_s
FROM all_feed_gaps fg
JOIN day_bounds db ON db.service_date = fg.service_date
WHERE GREATEST(fg.gap_start_utc, db.day_start_utc) < LEAST(fg.gap_end_utc, db.day_end_utc);

CREATE OR REPLACE TABLE gap_agg AS
SELECT service_date,
       COUNT(*) AS outages,
       MAX(gap_s) AS longest_outage_s
FROM feed_gaps_clipped
GROUP BY service_date;

-- Ties on gap_s broken by gap_start_utc (unique per service_date - clipped
-- gap windows for a date never overlap), so this ORDER BY is a total order
-- (D-019: previously incomplete - see D-019 in docs/decisions.md).
CREATE OR REPLACE TABLE largest_gap AS
SELECT service_date, gap_start_utc, gap_end_utc
FROM (
    SELECT service_date, gap_start_utc, gap_end_utc,
           ROW_NUMBER() OVER (PARTITION BY service_date ORDER BY gap_s DESC, gap_start_utc ASC) AS rn
    FROM feed_gaps_clipped
) WHERE rn = 1;

-- Overlap, in minutes, between each date's clipped feed_gaps windows and
-- that same date's local 06:00-22:00 window.
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
FROM feed_gaps_clipped fg
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

-- D-021: dst_ambiguous departures per date.
CREATE OR REPLACE TABLE dst_ambiguous_agg AS
SELECT service_date,
       COUNT(*) FILTER (WHERE status = 'dst_ambiguous') AS dst_ambiguous_departures
FROM all_stop_events
WHERE stop_position != 'final'
GROUP BY service_date;

CREATE OR REPLACE TABLE data_quality AS
SELECT
    dt.service_date, dt.day_type,
    fq.distinct_snapshots, fq.duplicate_snapshots,
    strftime(fq.first_snapshot_utc AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm', '%Y-%m-%dT%H:%M:%S') AS first_snapshot_local,
    strftime(fq.last_snapshot_utc AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm', '%Y-%m-%dT%H:%M:%S') AS last_snapshot_local,
    ga.longest_outage_s,
    strftime(lg.gap_start_utc AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm', '%Y-%m-%dT%H:%M:%S') AS longest_outage_start_local,
    strftime(lg.gap_end_utc AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm', '%Y-%m-%dT%H:%M:%S') AS longest_outage_end_local,
    ga.outages,
    ROUND(COALESCE(om.outage_minutes_06_22, 0), 2) AS outage_minutes_06_22,
    fq.local_hours_without_snapshots,
    ROUND(ma.with_marker::DOUBLE / NULLIF(ma.with_held_value, 0), 4) AS marker_share,
    fq.out_of_scope_trips_in_feed,
    ta.in_scope_trips, ta.trips_no_realtime_data, ta.trips_no_realtime_data_in_outage, ta.cancelled_trips,
    ROUND(ca.observed_departures::DOUBLE / NULLIF(ca.eligible_departures, 0), 4) AS coverage_share,
    COALESCE(da.dst_ambiguous_departures, 0) AS dst_ambiguous_departures,
    fq.schedule_mismatch_stop_events,
    fq.unmatched_realtime_trips
FROM day_types dt
JOIN all_feed_quality fq ON fq.service_date = dt.service_date
JOIN gap_agg ga ON ga.service_date = dt.service_date
JOIN largest_gap lg ON lg.service_date = dt.service_date
LEFT JOIN outage_minutes_06_22_agg om ON om.service_date = dt.service_date
JOIN marker_agg ma ON ma.service_date = dt.service_date
JOIN trip_agg_dq ta ON ta.service_date = dt.service_date
JOIN coverage_agg ca ON ca.service_date = dt.service_date
LEFT JOIN dst_ambiguous_agg da ON da.service_date = dt.service_date;
