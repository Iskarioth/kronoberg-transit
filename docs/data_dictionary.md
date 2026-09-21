# Data dictionary

Schema reference for the Google Sheets serving layer and the Parquet warehouse. Keep this
file in sync with every schema change (see `CLAUDE.md`).

## Google Sheets tabs

### `route_daily`

| Column | Type | Description |
|---|---|---|
| service_date | date (ISO `YYYY-MM-DD`) | Service date the row summarizes |
| route_id | string | GTFS route ID |
| route_name | string | Human-readable route name |
| scheduled_departures | integer | Departures scheduled for this route on this date |
| observed_departures | integer | Departures with a matching realtime observation |
| coverage_pct | float | observed_departures / scheduled_departures |
| on_time_pct | float | Share of observed departures classified on-time |
| late_pct | float | Share of observed departures classified late |
| early_pct | float | Share of observed departures classified early |
| cancelled_trips | integer | Trips cancelled per the realtime feed |
| median_delay_s | integer | Median delay in seconds across observed departures |
| p90_delay_s | integer | 90th percentile delay in seconds |

### `stop_hotspots`

| Column | Type | Description |
|---|---|---|
| period_start | date (ISO `YYYY-MM-DD`) | Start of the aggregation window |
| period_end | date (ISO `YYYY-MM-DD`) | End of the aggregation window |
| stop_id | string | GTFS stop ID |
| stop_name | string | Human-readable stop name |
| observed_departures | integer | Departures with a matching realtime observation at this stop |
| median_delay_s | integer | Median delay in seconds |
| p90_delay_s | integer | 90th percentile delay in seconds |
| late_pct | float | Share of observed departures classified late |

### `hour_of_day`

| Column | Type | Description |
|---|---|---|
| period_start | date (ISO `YYYY-MM-DD`) | Start of the aggregation window |
| period_end | date (ISO `YYYY-MM-DD`) | End of the aggregation window |
| day_type | string | e.g. weekday / weekend classification |
| hour | integer | Hour of day, 0-23, in `Europe/Stockholm` |
| observed_departures | integer | Departures with a matching realtime observation |
| on_time_pct | float | Share of observed departures classified on-time |
| median_delay_s | integer | Median delay in seconds |

### `data_quality`

| Column | Type | Description |
|---|---|---|
| service_date | date (ISO `YYYY-MM-DD`) | Service date this row reports on |
| feed | string | Feed name (e.g. `TripUpdates`) |
| expected_snapshots | integer | Number of realtime snapshots expected for the date |
| received_snapshots | integer | Number of realtime snapshots actually received |
| missing_hours | string | Hours with no data, comma-separated |
| trips_scheduled | integer | Trips scheduled per the static schedule |
| trips_observed | integer | Trips with at least one realtime observation |
| notes | string | Free-text notes on data quality issues |

### `run_log`

| Column | Type | Description |
|---|---|---|
| run_ts_utc | timestamp (UTC, ISO 8601) | When the run occurred |
| run_type | string | e.g. `setup`, `smoke-ci`, `daily` |
| service_date | date (ISO `YYYY-MM-DD`) | Service date the run processed, if applicable |
| stage | string | Pipeline stage name |
| status | string | `ok`, `error`, etc. |
| rows_in | integer | Row count into the stage |
| rows_out | integer | Row count out of the stage |
| duration_s | float | Stage duration in seconds |
| message | string | Free-text status message |

These schemas are a starting point and will evolve as the pipeline is built.

## Parquet schema (Hugging Face warehouse)

Built by `kronoberg_transit.transform` (D-012). Local path today is `data/warehouse/`
(gitignored); the same layout is uploaded to Hugging Face unchanged. Every table is
partitioned by `service_date`, one file per date:
`data/warehouse/<table>/service_date=YYYY-MM-DD/part-0.parquet`. Re-running a date
overwrites that date's partition; it never duplicates rows. All timestamps are naive
`TIMESTAMP` columns holding UTC instants (no timezone-aware type), per CLAUDE.md's
"store timestamps in UTC" rule.

### `trips`

One row per trip scheduled on the service date (per `calendar.txt` + `calendar_dates.txt`,
same-date static schedule).

| Column | Type | Description |
|---|---|---|
| service_date | date | Service date this row belongs to |
| trip_id | string | GTFS trip ID |
| route_id | string | GTFS route ID |
| direction_id | int, nullable | GTFS `direction_id`, or null if blank in the static feed |
| scheduled_first_departure_utc | timestamp | Static schedule's first-stop departure, converted to UTC |
| scheduled_last_arrival_utc | timestamp | Static schedule's final-stop arrival, converted to UTC |
| scheduled_stops | int | Count of this trip's `stop_times` rows |
| trip_status | string | `in_feed` \| `cancelled` \| `no_realtime_data` (D-009, D-010) |
| first_seen_utc | timestamp, nullable | Earliest TripUpdates snapshot in which the trip appeared, matched on `trip_id` + `start_date` = this service date (D-011). Null if `no_realtime_data` |
| last_seen_utc | timestamp, nullable | Latest such snapshot. Null if `no_realtime_data` |

### `stop_events`

One row per `stop_times` row of a trip active on the service date. Key: `(trip_id,
stop_sequence)` - never `stop_id` alone, which can repeat on a looping trip.

| Column | Type | Description |
|---|---|---|
| service_date | date | Service date this row belongs to |
| trip_id | string | GTFS trip ID |
| route_id | string | GTFS route ID |
| stop_sequence | int | GTFS `stop_sequence` |
| stop_id | string | GTFS stop ID |
| stop_position | string | `first` \| `intermediate` \| `final`, by this trip's min/max scheduled `stop_sequence` |
| scheduled_arrival_utc | timestamp | Static schedule's arrival time, converted to UTC |
| scheduled_departure_utc | timestamp | Static schedule's departure time, converted to UTC |
| held_arrival_utc | timestamp, nullable | Arrival `time` from the last TripUpdates snapshot in which this stop appeared (D-007). Null if never observed |
| held_departure_utc | timestamp, nullable | Departure `time` from that same last snapshot. Null if never observed |
| arrival_marker | bool, nullable | Whether that arrival value carried `uncertainty = 0`. Null if `held_arrival_utc` is null |
| departure_marker | bool, nullable | Whether that departure value carried `uncertainty = 0`. Null if `held_departure_utc` is null |
| last_stop_relationship | string, nullable | This stop's `schedule_relationship` (e.g. `SCHEDULED`, `SKIPPED`) at that same last snapshot. Null if never observed |
| last_seen_utc | timestamp, nullable | Header timestamp of that last snapshot. Null if never observed |
| status | string, nullable | `cancelled` \| `skipped` \| `observed` \| `unobserved`, checked in that order (D-009). Null for `stop_position = final` (D-008) |
| delay_s | int, nullable | `held_departure_utc - scheduled_departure_utc` in seconds. Set only when `status = observed`; null otherwise, including for final stops |

### `routes`

The full route list from the service date's same-date static schedule (not filtered to
routes actually running that day).

| Column | Type | Description |
|---|---|---|
| service_date | date | Service date this row's static schedule is from |
| route_id | string | GTFS route ID |
| route_short_name | string | Short route name |
| route_long_name | string | Long route name |
| route_type | int | GTFS route type (e.g. 700 = bus) |

### `stops`

The full stop list from the service date's same-date static schedule.

| Column | Type | Description |
|---|---|---|
| service_date | date | Service date this row's static schedule is from |
| stop_id | string | GTFS stop ID |
| stop_name | string | Stop name |
| stop_lat | double | Latitude |
| stop_lon | double | Longitude |
| location_type | int | GTFS `location_type` |
| parent_station | string, nullable | GTFS `parent_station`, null if blank |

### `feed_quality`

One row per feed per service date, statistics over that date's own local-hour archives
only (not the extra D+1 hours read per D-011).

| Column | Type | Description |
|---|---|---|
| service_date | date | Service date this row reports on |
| feed | string | Feed name (`TripUpdates`) |
| archive_files | int | Total snapshot files across the date's own 24 hourly archives, including duplicates |
| distinct_snapshots | int | Distinct feed header timestamps after deduplication |
| duplicate_snapshots | int | `archive_files - distinct_snapshots` |
| first_snapshot_utc | timestamp | Earliest snapshot's header timestamp |
| last_snapshot_utc | timestamp | Latest snapshot's header timestamp |
| max_gap_s | int | Largest gap between consecutive snapshots, in seconds |
| gaps_over_300s | int | Count of gaps over 300 seconds |
| local_hours_without_snapshots | string | Comma-separated local hours (00-23) with no snapshots that day |
| next_day_hours_read | string | Comma-separated D+1 local hours read for this run, per D-011 |
