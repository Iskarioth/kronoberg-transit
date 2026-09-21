-- routes and stops: the full route/stop set from D's same-date static
-- schedule, tagged with service_date. Not filtered to trips active on D.
-- Params: $svc_date (ISO date, e.g. 2026-09-07)

CREATE OR REPLACE TABLE routes_out AS
SELECT
    DATE '$svc_date' AS service_date,
    route_id,
    route_short_name,
    route_long_name,
    route_type
FROM static_routes;

CREATE OR REPLACE TABLE stops_out AS
SELECT
    DATE '$svc_date' AS service_date,
    stop_id,
    stop_name,
    stop_lat,
    stop_lon,
    location_type,
    NULLIF(parent_station, '') AS parent_station
FROM static_stops;
