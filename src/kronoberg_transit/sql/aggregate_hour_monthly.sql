-- hour_monthly (D-017): one row per (month, day_type, stop_set (D-019),
-- hour_local). Measure block M only, over in-scope non-final stop_events
-- grouped by the local hour of the stop event's scheduled departure.
-- Requires aggregate_base.sql and aggregate_network_monthly.sql (for
-- month_completeness) to have run first.

CREATE OR REPLACE TABLE hour_monthly_raw AS
WITH se_hour AS (
    SELECT se.*, dte.month, dte.day_type,
        date_part('hour', se.scheduled_departure_utc AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Stockholm') AS hour_local
    FROM stop_set_stop_events se
    JOIN day_type_expanded dte ON dte.service_date = se.service_date
),
se_agg AS (
    SELECT month, day_type, stop_set, hour_local,
        COUNT(*) FILTER (WHERE status IN ('observed', 'unobserved')) AS eligible_departures,
        COUNT(*) FILTER (WHERE status = 'observed') AS observed_departures,
        COUNT(*) FILTER (WHERE status = 'unobserved') AS unobserved_departures,
        COUNT(DISTINCT (service_date, trip_id)) FILTER (WHERE status = 'observed') AS observed_trips,
        COUNT(*) FILTER (WHERE status = 'observed' AND delay_s < -60) AS early_departures,
        COUNT(*) FILTER (WHERE status = 'observed' AND delay_s BETWEEN -60 AND 180) AS on_time_departures,
        COUNT(*) FILTER (WHERE status = 'observed' AND delay_s > 180) AS late_departures,
        COUNT(*) FILTER (WHERE status = 'observed' AND delay_s BETWEEN -60 AND 60) AS on_time_60_departures,
        COUNT(*) FILTER (WHERE status = 'observed' AND delay_s BETWEEN -60 AND 300) AS on_time_300_departures,
        quantile_cont(delay_s, 0.5) FILTER (WHERE status = 'observed') AS median_delay_s,
        quantile_cont(delay_s, 0.9) FILTER (WHERE status = 'observed') AS p90_delay_s
    FROM se_hour
    GROUP BY month, day_type, stop_set, hour_local
)
SELECT
    sa.month, sa.day_type, sa.stop_set, sa.hour_local, mc.month_complete,
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
FROM se_agg sa
JOIN month_completeness mc ON mc.month = sa.month;

CREATE OR REPLACE TABLE hour_monthly AS
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
FROM hour_monthly_raw;
