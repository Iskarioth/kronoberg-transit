-- station_monthly (D-017): one row per (month, day_type, stop_set (D-019),
-- station). Measure block M only, over in-scope non-final stop_events
-- grouped by the stop's station (D-017: parent_station, or the stop itself
-- if it has none). For stop_set='timing_stops' a station only gets a row if
-- it has at least one timing-stop eligible departure that month/day_type
-- (D-019); stop_set='all_stops' keeps the unrestricted pre-D-019 behaviour.
-- Requires aggregate_base.sql and aggregate_network_monthly.sql (for
-- month_completeness) to have run first.

CREATE OR REPLACE TABLE station_monthly_raw AS
WITH se_station AS (
    SELECT se.*, dte.month, dte.day_type, st.station_id
    FROM stop_set_stop_events se
    JOIN day_type_expanded dte ON dte.service_date = se.service_date
    JOIN stations st ON st.stop_id = se.stop_id
),
se_agg AS (
    SELECT month, day_type, stop_set, station_id,
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
    GROUP BY month, day_type, stop_set, station_id
    HAVING stop_set = 'all_stops'
        OR COUNT(*) FILTER (WHERE status IN ('observed', 'unobserved')) > 0
),
route_names AS (
    SELECT month, day_type, stop_set, station_id,
           array_to_string(list(DISTINCT route_short_name ORDER BY route_short_name), ',') AS route_short_names
    FROM (SELECT DISTINCT month, day_type, stop_set, station_id, route_id FROM se_station) s
    JOIN canonical_routes cr ON cr.route_id = s.route_id
    GROUP BY month, day_type, stop_set, station_id
),
-- Station coordinates, per month: the station's own row in stops
-- (station_id = stops.stop_id, D-017), from the latest service_date within
-- that month on which that stop_id appears. Per month, not globally, since
-- coordinates can drift date to date within the warehouse. arg_max mirrors
-- the canonical_stops "latest value" pattern in aggregate_base.sql, keyed
-- here by (month, stop_id) instead of stop_id alone.
station_coords_monthly AS (
    SELECT dt.month, s.stop_id AS station_id,
           arg_max(s.stop_lat, s.service_date) AS station_lat,
           arg_max(s.stop_lon, s.service_date) AS station_lon
    FROM all_stops s
    JOIN day_types dt ON dt.service_date = s.service_date
    GROUP BY dt.month, s.stop_id
)
SELECT
    sa.month, sa.day_type, sa.stop_set, sa.station_id, sn.station_name,
    scm.station_lat, scm.station_lon,
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
LEFT JOIN station_coords_monthly scm ON scm.month = sa.month AND scm.station_id = sa.station_id
LEFT JOIN route_names rn ON rn.month = sa.month AND rn.day_type = sa.day_type AND rn.stop_set = sa.stop_set AND rn.station_id = sa.station_id
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
