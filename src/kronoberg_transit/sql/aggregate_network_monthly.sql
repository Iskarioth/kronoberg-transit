-- network_monthly (D-017): one row per (month, day_type in all/weekday/
-- saturday/sunday, stop_set in all_stops/timing_stops (D-019)). Trip block T
-- over all_trips (any scope) is identical across stop sets; measure block M
-- over in-scope, non-final stop_events is restricted per stop_set. Requires
-- aggregate_base.sql to have run first.

CREATE OR REPLACE TABLE network_monthly_raw AS
WITH trip_agg AS (
    SELECT dte.month, dte.day_type,
        COUNT(*) AS scheduled_trips,
        COUNT(*) FILTER (WHERE t.in_scope) AS in_scope_trips,
        COUNT(*) FILTER (WHERE NOT t.in_scope) AS out_of_scope_trips,
        COUNT(*) FILTER (WHERE t.trip_status = 'cancelled') AS cancelled_trips,
        COUNT(*) FILTER (WHERE t.trip_status = 'no_realtime_data') AS trips_no_realtime_data,
        COUNT(*) FILTER (WHERE t.no_data_in_outage) AS trips_no_realtime_data_in_outage
    FROM all_trips t
    JOIN day_type_expanded dte ON dte.service_date = t.service_date
    GROUP BY dte.month, dte.day_type
),
se_agg AS (
    SELECT dte.month, dte.day_type, se.stop_set,
        COUNT(*) FILTER (WHERE se.status IN ('observed', 'unobserved')) AS eligible_departures,
        COUNT(*) FILTER (WHERE se.status = 'observed') AS observed_departures,
        COUNT(*) FILTER (WHERE se.status = 'unobserved') AS unobserved_departures,
        COUNT(*) FILTER (WHERE se.status = 'skipped') AS skipped_departures,
        COUNT(*) FILTER (WHERE se.status = 'dst_ambiguous') AS dst_ambiguous_departures,
        COUNT(DISTINCT (se.service_date, se.trip_id)) FILTER (WHERE se.status = 'observed') AS observed_trips,
        COUNT(*) FILTER (WHERE se.status = 'observed' AND se.delay_s < -60) AS early_departures,
        COUNT(*) FILTER (WHERE se.status = 'observed' AND se.delay_s BETWEEN -60 AND 180) AS on_time_departures,
        COUNT(*) FILTER (WHERE se.status = 'observed' AND se.delay_s > 180) AS late_departures,
        COUNT(*) FILTER (WHERE se.status = 'observed' AND se.delay_s BETWEEN -60 AND 60) AS on_time_60_departures,
        COUNT(*) FILTER (WHERE se.status = 'observed' AND se.delay_s BETWEEN -60 AND 300) AS on_time_300_departures,
        quantile_cont(se.delay_s, 0.5) FILTER (WHERE se.status = 'observed') AS median_delay_s,
        quantile_cont(se.delay_s, 0.9) FILTER (WHERE se.status = 'observed') AS p90_delay_s
    FROM stop_set_stop_events se
    JOIN day_type_expanded dte ON dte.service_date = se.service_date
    GROUP BY dte.month, dte.day_type, se.stop_set
),
service_dates_agg AS (
    SELECT month, day_type, COUNT(DISTINCT service_date) AS service_dates
    FROM day_type_expanded
    GROUP BY month, day_type
),
keys AS (
    SELECT sda.month, sda.day_type, ss.stop_set, sda.service_dates
    FROM service_dates_agg sda
    CROSS JOIN stop_sets ss
)
SELECT
    k.month, k.day_type, k.stop_set, k.service_dates, mc.month_complete,
    ta.scheduled_trips, ta.in_scope_trips, ta.out_of_scope_trips,
    ta.cancelled_trips, ta.trips_no_realtime_data, ta.trips_no_realtime_data_in_outage,
    ROUND(ta.cancelled_trips::DOUBLE / NULLIF(ta.in_scope_trips, 0), 4) AS cancellation_share,
    ROUND(ta.trips_no_realtime_data::DOUBLE / NULLIF(ta.in_scope_trips, 0), 4) AS no_realtime_data_share,
    COALESCE(sa.skipped_departures, 0) AS skipped_departures,
    ROUND(COALESCE(sa.skipped_departures, 0)::DOUBLE / NULLIF(COALESCE(sa.eligible_departures, 0) + COALESCE(sa.skipped_departures, 0), 0), 4) AS skipped_share,
    COALESCE(sa.dst_ambiguous_departures, 0) AS dst_ambiguous_departures,
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
JOIN trip_agg ta ON ta.month = k.month AND ta.day_type = k.day_type
LEFT JOIN se_agg sa ON sa.month = k.month AND sa.day_type = k.day_type AND sa.stop_set = k.stop_set
JOIN month_completeness mc ON mc.month = k.month;

CREATE OR REPLACE TABLE network_monthly AS
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
FROM network_monthly_raw;
