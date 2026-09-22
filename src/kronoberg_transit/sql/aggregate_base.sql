-- Publishing layer base tables (D-017): loads every warehouse partition,
-- adds day_type/month, a canonical stop/station lookup, and one label per
-- route_id built from its most common stop pattern (ties broken by the
-- earliest scheduled first departure among trips using that pattern).
--
-- Params: $warehouse_dir

CREATE OR REPLACE TABLE all_trips AS
SELECT * FROM read_parquet('$warehouse_dir/trips/*/part-0.parquet');

CREATE OR REPLACE TABLE all_stop_events AS
SELECT * FROM read_parquet('$warehouse_dir/stop_events/*/part-0.parquet');

CREATE OR REPLACE TABLE all_routes AS
SELECT * FROM read_parquet('$warehouse_dir/routes/*/part-0.parquet');

CREATE OR REPLACE TABLE all_stops AS
SELECT * FROM read_parquet('$warehouse_dir/stops/*/part-0.parquet');

CREATE OR REPLACE TABLE all_feed_quality AS
SELECT * FROM read_parquet('$warehouse_dir/feed_quality/*/part-0.parquet');

CREATE OR REPLACE TABLE all_feed_gaps AS
SELECT * FROM read_parquet('$warehouse_dir/feed_gaps/*/part-0.parquet');

CREATE OR REPLACE TABLE service_dates AS
SELECT DISTINCT service_date FROM all_trips;

-- Day type (D-017): weekday | saturday | sunday. No weekday public holiday
-- falls in the processed range, so the OPEN Public holidays row is not
-- implemented here.
CREATE OR REPLACE TABLE day_types AS
SELECT service_date,
       strftime(service_date, '%Y-%m') AS month,
       CASE lower(strftime(service_date, '%A'))
           WHEN 'saturday' THEN 'saturday'
           WHEN 'sunday' THEN 'sunday'
           ELSE 'weekday'
       END AS day_type
FROM service_dates;

-- Expands each service date into an 'all' row plus its actual day_type row,
-- so every monthly tab can GROUP BY (month, day_type) uniformly and the
-- day_type='all' row is, by construction, the union of weekday/saturday/sunday.
CREATE OR REPLACE TABLE day_type_expanded AS
SELECT service_date, month, 'all' AS day_type FROM day_types
UNION ALL
SELECT service_date, month, day_type FROM day_types;

CREATE OR REPLACE TABLE month_completeness AS
SELECT month,
       COUNT(DISTINCT service_date) AS n_dates_processed,
       COUNT(DISTINCT service_date) = date_part('day', last_day(strptime(month || '-01', '%Y-%m-%d'))) AS month_complete
FROM day_types
GROUP BY month;

-- Canonical (stop_id -> name/parent_station), one row per stop_id, using the
-- most recent service date's value in case of any date-to-date drift.
CREATE OR REPLACE TABLE canonical_stops AS
SELECT stop_id,
       arg_max(stop_name, service_date) AS stop_name,
       arg_max(parent_station, service_date) AS parent_station
FROM all_stops
GROUP BY stop_id;

-- Station (D-017): a stop's parent_station, or the stop itself if it has none.
CREATE OR REPLACE TABLE stations AS
SELECT stop_id, COALESCE(parent_station, stop_id) AS station_id
FROM canonical_stops;

CREATE OR REPLACE TABLE station_names AS
SELECT DISTINCT st.station_id, cs.stop_name AS station_name
FROM stations st
LEFT JOIN canonical_stops cs ON cs.stop_id = st.station_id;

CREATE OR REPLACE TABLE canonical_routes AS
SELECT route_id, arg_max(route_short_name, service_date) AS route_short_name
FROM all_routes
GROUP BY route_id;

-- Every trip's stop pattern (ordered stop_id list, as a delimited string key
-- so it groups cleanly) plus its first/last stop.
CREATE OR REPLACE TABLE trip_patterns AS
SELECT
    t.service_date,
    t.route_id,
    t.trip_id,
    t.scheduled_first_departure_utc,
    array_to_string(list(se.stop_id ORDER BY se.stop_sequence), ',') AS pattern_key,
    first(se.stop_id ORDER BY se.stop_sequence) AS first_stop_id,
    last(se.stop_id ORDER BY se.stop_sequence) AS last_stop_id
FROM all_trips t
JOIN all_stop_events se ON se.trip_id = t.trip_id AND se.service_date = t.service_date
GROUP BY t.service_date, t.route_id, t.trip_id, t.scheduled_first_departure_utc;

CREATE OR REPLACE TABLE pattern_counts AS
SELECT route_id, pattern_key,
       any_value(first_stop_id) AS first_stop_id,
       any_value(last_stop_id) AS last_stop_id,
       COUNT(*) AS n_trips,
       MIN(scheduled_first_departure_utc) AS earliest_departure
FROM trip_patterns
GROUP BY route_id, pattern_key;

-- The most common pattern per route; ties broken by earliest first departure.
CREATE OR REPLACE TABLE route_pattern AS
SELECT route_id, first_stop_id, last_stop_id
FROM (
    SELECT route_id, first_stop_id, last_stop_id,
           ROW_NUMBER() OVER (PARTITION BY route_id ORDER BY n_trips DESC, earliest_departure ASC) AS rn
    FROM pattern_counts
) WHERE rn = 1;

CREATE OR REPLACE TABLE route_label_raw AS
SELECT r.route_id, r.route_short_name,
       r.route_short_name || ' · ' || fn.station_name || ' – ' || ln.station_name AS label
FROM canonical_routes r
JOIN route_pattern rp ON rp.route_id = r.route_id
JOIN stations sf ON sf.stop_id = rp.first_stop_id
JOIN station_names fn ON fn.station_id = sf.station_id
JOIN stations sl ON sl.stop_id = rp.last_stop_id
JOIN station_names ln ON ln.station_id = sl.station_id;

CREATE OR REPLACE TABLE label_collisions AS
SELECT label, list(DISTINCT route_id) AS route_ids, COUNT(DISTINCT route_id) AS n
FROM route_label_raw
GROUP BY label
HAVING COUNT(DISTINCT route_id) > 1;

-- Only routes with at least one trip anywhere get a label (route_pattern has
-- no row otherwise); routes involved in a label collision get their
-- route_id appended.
CREATE OR REPLACE TABLE route_labels AS
SELECT rlr.route_id, rlr.route_short_name,
       CASE WHEN lc.n > 1 THEN rlr.label || ' (' || rlr.route_id || ')' ELSE rlr.label END AS route_label
FROM route_label_raw rlr
LEFT JOIN label_collisions lc ON lc.label = rlr.label;
