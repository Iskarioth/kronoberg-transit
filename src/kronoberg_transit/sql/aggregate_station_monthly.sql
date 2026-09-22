-- station_monthly (D-017): one row per (month, day_type, station). Measure
-- block M only, over in-scope non-final stop_events grouped by the stop's
-- station (D-017: parent_station, or the stop itself if it has none).
-- Requires aggregate_base.sql and aggregate_network_monthly.sql (for
-- month_completeness) to have run first.

CREATE OR REPLACE TABLE station_monthly_raw AS
WITH se_station AS (
    SELECT se.*, dte.month, dte.day_type, st.station_id
    FROM all_stop_events se
    JOIN day_type_expanded dte ON dte.service_date = se.service_date
    JOIN stations st ON st.stop_id = se.stop_id
    WHERE se.stop_position != 'final' AND se.in_scope
),
se_agg AS (
    SELECT month, day_type, station_id,
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
    FROM se_station
    GROUP BY month, day_type, station_id
),
route_names AS (
    SELECT month, day_type, station_id,
           array_to_string(list(DISTINCT route_short_name ORDER BY route_short_name), ',') AS route_short_names
    FROM (SELECT DISTINCT month, day_type, station_id, route_id FROM se_station) s
    JOIN canonical_routes cr ON cr.route_id = s.route_id
    GROUP BY month, day_type, station_id
)
SELECT
    sa.month, sa.day_type, sa.station_id, sn.station_name,
    COALESCE(rn.route_short_names, '') AS route_short_names,
    mc.month_complete,
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
JOIN station_names sn ON sn.station_id = sa.station_id
LEFT JOIN route_names rn ON rn.month = sa.month AND rn.day_type = sa.day_type AND rn.station_id = sa.station_id
JOIN month_completeness mc ON mc.month = sa.month;

CREATE OR REPLACE TABLE station_monthly AS
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
FROM station_monthly_raw;
