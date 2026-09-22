-- network_monthly (D-017): one row per (month, day_type in all/weekday/
-- saturday/sunday). Trip block T over all_trips (any scope); measure block M
-- over in-scope, non-final stop_events. Requires aggregate_base.sql to have
-- run first.

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
    SELECT dte.month, dte.day_type,
        COUNT(*) FILTER (WHERE se.status IN ('observed', 'unobserved')) AS eligible_departures,
        COUNT(*) FILTER (WHERE se.status = 'observed') AS observed_departures,
        COUNT(*) FILTER (WHERE se.status = 'unobserved') AS unobserved_departures,
        COUNT(*) FILTER (WHERE se.status = 'skipped') AS skipped_departures,
        COUNT(DISTINCT (se.service_date, se.trip_id)) FILTER (WHERE se.status = 'observed') AS observed_trips,
        COUNT(*) FILTER (WHERE se.status = 'observed' AND se.delay_s < -60) AS early_departures,
        COUNT(*) FILTER (WHERE se.status = 'observed' AND se.delay_s BETWEEN -60 AND 180) AS on_time_departures,
        COUNT(*) FILTER (WHERE se.status = 'observed' AND se.delay_s > 180) AS late_departures,
        COUNT(*) FILTER (WHERE se.status = 'observed' AND se.delay_s BETWEEN -60 AND 60) AS on_time_60_departures,
        COUNT(*) FILTER (WHERE se.status = 'observed' AND se.delay_s BETWEEN -60 AND 300) AS on_time_300_departures,
        quantile_cont(se.delay_s, 0.5) FILTER (WHERE se.status = 'observed') AS median_delay_s,
        quantile_cont(se.delay_s, 0.9) FILTER (WHERE se.status = 'observed') AS p90_delay_s
    FROM all_stop_events se
    JOIN day_type_expanded dte ON dte.service_date = se.service_date
    WHERE se.stop_position != 'final' AND se.in_scope
    GROUP BY dte.month, dte.day_type
),
service_dates_agg AS (
    SELECT month, day_type, COUNT(DISTINCT service_date) AS service_dates
    FROM day_type_expanded
    GROUP BY month, day_type
)
SELECT
    sda.month, sda.day_type, sda.service_dates, mc.month_complete,
    ta.scheduled_trips, ta.in_scope_trips, ta.out_of_scope_trips,
    ta.cancelled_trips, ta.trips_no_realtime_data, ta.trips_no_realtime_data_in_outage,
    ROUND(ta.cancelled_trips::DOUBLE / NULLIF(ta.in_scope_trips, 0), 4) AS cancellation_share,
    ROUND(ta.trips_no_realtime_data::DOUBLE / NULLIF(ta.in_scope_trips, 0), 4) AS no_realtime_data_share,
    sa.skipped_departures,
    ROUND(sa.skipped_departures::DOUBLE / NULLIF(sa.eligible_departures + sa.skipped_departures, 0), 4) AS skipped_share,
    sa.eligible_departures, sa.observed_departures, sa.unobserved_departures,
    ROUND(sa.observed_departures::DOUBLE / NULLIF(sa.eligible_departures, 0), 4) AS coverage_share,
    sa.observed_trips,
    sa.early_departures, sa.on_time_departures, sa.late_departures,
    sa.on_time_60_departures, sa.on_time_300_departures,
    ROUND(sa.early_departures::DOUBLE / NULLIF(sa.observed_departures, 0), 4) AS early_share,
    ROUND(sa.on_time_departures::DOUBLE / NULLIF(sa.observed_departures, 0), 4) AS on_time_share,
    ROUND(sa.late_departures::DOUBLE / NULLIF(sa.observed_departures, 0), 4) AS late_share,
    ROUND(sa.on_time_60_departures::DOUBLE / NULLIF(sa.observed_departures, 0), 4) AS on_time_60_share,
    ROUND(sa.on_time_300_departures::DOUBLE / NULLIF(sa.observed_departures, 0), 4) AS on_time_300_share,
    sa.median_delay_s, sa.p90_delay_s
FROM service_dates_agg sda
JOIN trip_agg ta ON ta.month = sda.month AND ta.day_type = sda.day_type
JOIN se_agg sa ON sa.month = sda.month AND sa.day_type = sda.day_type
JOIN month_completeness mc ON mc.month = sda.month;

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
