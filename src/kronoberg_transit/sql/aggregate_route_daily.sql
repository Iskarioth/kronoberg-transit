-- route_daily (D-017): one row per (service_date, route_id, stop_set
-- (D-019)). Same shape as route_monthly but at daily grain, with a single
-- day_type per row (that date's actual type, not the 'all' expansion). Trip
-- block T is identical across stop sets; measure block M is restricted per
-- stop_set. Requires aggregate_base.sql to have run first.

CREATE OR REPLACE TABLE route_daily_raw AS
WITH trip_agg AS (
    SELECT dt.service_date, dt.day_type, t.route_id,
        COUNT(*) AS scheduled_trips,
        COUNT(*) FILTER (WHERE t.in_scope) AS in_scope_trips,
        COUNT(*) FILTER (WHERE NOT t.in_scope) AS out_of_scope_trips,
        COUNT(*) FILTER (WHERE t.trip_status = 'cancelled') AS cancelled_trips,
        COUNT(*) FILTER (WHERE t.trip_status = 'no_realtime_data') AS trips_no_realtime_data,
        COUNT(*) FILTER (WHERE t.no_data_in_outage) AS trips_no_realtime_data_in_outage
    FROM all_trips t
    JOIN day_types dt ON dt.service_date = t.service_date
    GROUP BY dt.service_date, dt.day_type, t.route_id
),
se_agg AS (
    SELECT se.service_date, se.stop_set, se.route_id,
        COUNT(*) FILTER (WHERE se.status IN ('observed', 'unobserved')) AS eligible_departures,
        COUNT(*) FILTER (WHERE se.status = 'observed') AS observed_departures,
        COUNT(*) FILTER (WHERE se.status = 'unobserved') AS unobserved_departures,
        COUNT(*) FILTER (WHERE se.status = 'skipped') AS skipped_departures,
        COUNT(*) FILTER (WHERE se.status = 'dst_ambiguous') AS dst_ambiguous_departures,
        COUNT(DISTINCT se.trip_id) FILTER (WHERE se.status = 'observed') AS observed_trips,
        COUNT(*) FILTER (WHERE se.status = 'observed' AND se.delay_s < -60) AS early_departures,
        COUNT(*) FILTER (WHERE se.status = 'observed' AND se.delay_s BETWEEN -60 AND 180) AS on_time_departures,
        COUNT(*) FILTER (WHERE se.status = 'observed' AND se.delay_s > 180) AS late_departures,
        COUNT(*) FILTER (WHERE se.status = 'observed' AND se.delay_s BETWEEN -60 AND 60) AS on_time_60_departures,
        COUNT(*) FILTER (WHERE se.status = 'observed' AND se.delay_s BETWEEN -60 AND 300) AS on_time_300_departures,
        quantile_cont(se.delay_s, 0.5) FILTER (WHERE se.status = 'observed') AS median_delay_s,
        quantile_cont(se.delay_s, 0.9) FILTER (WHERE se.status = 'observed') AS p90_delay_s
    FROM stop_set_stop_events se
    GROUP BY se.service_date, se.stop_set, se.route_id
),
keys AS (
    SELECT ta.service_date, ta.day_type, ta.route_id, ss.stop_set
    FROM trip_agg ta
    CROSS JOIN stop_sets ss
)
SELECT
    k.service_date, k.day_type, k.stop_set, k.route_id, rl.route_short_name, rl.route_label,
    ta.scheduled_trips, ta.in_scope_trips, ta.out_of_scope_trips,
    ta.cancelled_trips, ta.trips_no_realtime_data, ta.trips_no_realtime_data_in_outage,
    ROUND(ta.cancelled_trips::DOUBLE / NULLIF(ta.in_scope_trips, 0), 4) AS cancellation_share,
    ROUND(ta.trips_no_realtime_data::DOUBLE / NULLIF(ta.in_scope_trips, 0), 4) AS no_realtime_data_share,
    COALESCE(sa.skipped_departures, 0) AS skipped_departures,
    ROUND(COALESCE(sa.skipped_departures, 0)::DOUBLE / NULLIF(COALESCE(sa.eligible_departures, 0) + COALESCE(sa.skipped_departures, 0), 0), 4) AS skipped_share,
    COALESCE(sa.dst_ambiguous_departures, 0) AS dst_ambiguous_departures,
    ROUND(ta.in_scope_trips::DOUBLE / NULLIF(ta.scheduled_trips, 0), 4) AS in_scope_share,
    COALESCE(sa.eligible_departures, 0) AS eligible_departures,
    COALESCE(sa.observed_departures, 0) AS observed_departures,
    COALESCE(sa.unobserved_departures, 0) AS unobserved_departures,
    ROUND(COALESCE(sa.observed_departures, 0)::DOUBLE / NULLIF(sa.eligible_departures, 0), 4) AS coverage_share,
    COALESCE(sa.observed_trips, 0) AS observed_trips,
    COALESCE(sa.early_departures, 0) AS early_departures,
    COALESCE(sa.on_time_departures, 0) AS on_time_departures,
    COALESCE(sa.late_departures, 0) AS late_departures,
    COALESCE(sa.on_time_60_departures, 0) AS on_time_60_departures,
    COALESCE(sa.on_time_300_departures, 0) AS on_time_300_departures,
    ROUND(COALESCE(sa.early_departures, 0)::DOUBLE / NULLIF(sa.observed_departures, 0), 4) AS early_share,
    ROUND(COALESCE(sa.on_time_departures, 0)::DOUBLE / NULLIF(sa.observed_departures, 0), 4) AS on_time_share,
    ROUND(COALESCE(sa.late_departures, 0)::DOUBLE / NULLIF(sa.observed_departures, 0), 4) AS late_share,
    ROUND(COALESCE(sa.on_time_60_departures, 0)::DOUBLE / NULLIF(sa.observed_departures, 0), 4) AS on_time_60_share,
    ROUND(COALESCE(sa.on_time_300_departures, 0)::DOUBLE / NULLIF(sa.observed_departures, 0), 4) AS on_time_300_share,
    sa.median_delay_s, sa.p90_delay_s
FROM keys k
JOIN trip_agg ta ON ta.service_date = k.service_date AND ta.route_id = k.route_id
JOIN route_labels rl ON rl.route_id = k.route_id
LEFT JOIN se_agg sa ON sa.service_date = k.service_date AND sa.stop_set = k.stop_set AND sa.route_id = k.route_id;

CREATE OR REPLACE TABLE route_daily AS
SELECT *,
    CASE
        WHEN eligible_departures = 0 THEN 'no_eligible_departures'
        WHEN observed_trips < 20 AND coverage_share < 0.90 THEN 'observed_trips<20;coverage<90%'
        WHEN observed_trips < 20 THEN 'observed_trips<20'
        WHEN coverage_share < 0.90 THEN 'coverage<90%'
        ELSE ''
    END AS not_reportable_reason,
    CASE
        WHEN eligible_departures = 0 THEN false
        WHEN observed_trips < 20 THEN false
        WHEN coverage_share < 0.90 THEN false
        ELSE true
    END AS reportable
FROM route_daily_raw;
