# Data dictionary

Schema reference for the Google Sheets serving layer and the Parquet warehouse. Keep this
file in sync with every schema change (see `CLAUDE.md`).

## Google Sheets tabs

Built by `kronoberg_transit.aggregate` (D-017) from every warehouse partition. Every run
rebuilds every tab from scratch (`--dry-run` writes the same rows as CSV to
`data/publish/` instead). All shares are fractions between 0 and 1, rounded to 4
decimals, null when their denominator is 0. All times are local (`Europe/Stockholm`,
D-017's Reporting time zone) as ISO 8601 strings. `month` is `YYYY-MM`. `day_type` is
`weekday` | `saturday` | `sunday` on `route_daily` and `data_quality`, and additionally
`all` (the union of the other three) on every monthly tab (D-017).

Two column blocks are shared across tabs:

**Measure block M** (every tab except `data_quality` and `run_log`), over in-scope,
non-final stop events (D-008, D-013):

| Column | Type | Description |
|---|---|---|
| eligible_departures | integer | Non-final in-scope stop events with status `observed` or `unobserved` |
| observed_departures | integer | Of those, status `observed` |
| unobserved_departures | integer | Of those, status `unobserved` |
| coverage_share | float, nullable | observed_departures ÷ eligible_departures (D-009) |
| observed_trips | integer | Distinct (service_date, trip_id) with at least one observed departure in the group (D-014) |
| early_departures | integer | Observed departures with `delay_s < -60` |
| on_time_departures | integer | Observed departures with `-60 <= delay_s <= 180` |
| late_departures | integer | Observed departures with `delay_s > 180` |
| on_time_60_departures | integer | Observed departures with `-60 <= delay_s <= 60` (+60s sensitivity) |
| on_time_300_departures | integer | Observed departures with `-60 <= delay_s <= 300` (+300s sensitivity) |
| early_share, on_time_share, late_share, on_time_60_share, on_time_300_share | float, nullable | Each departures column ÷ observed_departures |
| median_delay_s, p90_delay_s | float, nullable | `quantile_cont(delay_s, 0.5 \| 0.9)` over observed departures |
| reportable | bool | False when eligible_departures = 0, observed_trips < 20, or coverage_share < 0.90 (D-014) |
| not_reportable_reason | string | Empty when reportable; else `observed_trips<20`, `coverage<90%`, both joined by `;`, or `no_eligible_departures` |

**Trip block T** (`network_monthly`, `route_monthly`, `route_daily`), over all trips
regardless of scope:

| Column | Type | Description |
|---|---|---|
| scheduled_trips | integer | All trips scheduled in the group, any scope |
| in_scope_trips | integer | Of those, `in_scope` (D-013) |
| out_of_scope_trips | integer | Of those, not `in_scope` |
| cancelled_trips | integer | `trip_status = cancelled` |
| trips_no_realtime_data | integer | `trip_status = no_realtime_data` (D-010) |
| trips_no_realtime_data_in_outage | integer | Of those, `no_data_in_outage` (D-016) |
| cancellation_share | float, nullable | cancelled_trips ÷ in_scope_trips |
| no_realtime_data_share | float, nullable | trips_no_realtime_data ÷ in_scope_trips |
| skipped_departures | integer | Non-final in-scope stop events with status `skipped` (D-009) |
| skipped_share | float, nullable | skipped_departures ÷ (eligible_departures + skipped_departures) |

### `network_monthly`

One row per (month, day_type). T and M as above.

| Column | Type | Description |
|---|---|---|
| month | string | `YYYY-MM` |
| day_type | string | `all` \| `weekday` \| `saturday` \| `sunday` |
| service_dates | integer | Distinct service dates contributing to this row |
| month_complete | bool | True once every calendar date in the month has a warehouse partition (D-015) |
| *(T block)* | | |
| *(M block)* | | |

### `route_monthly`

One row per (month, day_type, route_id). Includes every route with at least one
scheduled trip that month/day_type, even entirely out-of-scope routes (M block is then
all zero/null). Every route number is a route_id; `route_label` disambiguates route
numbers that map to more than one route_id (D-017).

| Column | Type | Description |
|---|---|---|
| month | string | `YYYY-MM` |
| day_type | string | `all` \| `weekday` \| `saturday` \| `sunday` |
| route_id | string | GTFS route ID |
| route_short_name | string | Short route name |
| route_label | string | `<route_short_name> · <first station> – <last station>` of the route's most common stop pattern (ties broken by earliest scheduled first departure); `route_id` appended in parentheses to both sides of a label collision |
| service_dates | integer | Distinct service dates this route ran on, this month/day_type |
| month_complete | bool | See `network_monthly` |
| *(T block)* | | |
| in_scope_share | float, nullable | in_scope_trips ÷ scheduled_trips |
| *(M block)* | | |

### `route_daily`

One row per (service_date, route_id). Same shape as `route_monthly` at daily grain, no
`service_dates`/`month_complete`; `day_type` is that date's own actual type.

| Column | Type | Description |
|---|---|---|
| service_date | date (ISO `YYYY-MM-DD`) | Service date the row summarizes |
| day_type | string | `weekday` \| `saturday` \| `sunday` |
| route_id | string | GTFS route ID |
| route_short_name | string | Short route name |
| route_label | string | See `route_monthly` |
| *(T block)* | | |
| in_scope_share | float, nullable | in_scope_trips ÷ scheduled_trips |
| *(M block)* | | |

### `station_monthly`

One row per (month, day_type, station). A station is a stop's `parent_station`, or the
stop itself if it has none (D-017). M block only.

| Column | Type | Description |
|---|---|---|
| month | string | `YYYY-MM` |
| day_type | string | `all` \| `weekday` \| `saturday` \| `sunday` |
| station_id | string | The station's `stop_id` (its own, or its members' `parent_station`) |
| station_name | string | The station's `stop_name` |
| route_short_names | string | Distinct route short names serving this station, sorted, comma-separated |
| month_complete | bool | See `network_monthly` |
| *(M block)* | | |

### `hour_monthly`

One row per (month, day_type, hour_local). M block only, grouped by the local hour of
each stop event's scheduled departure (D-017's Hour of day).

| Column | Type | Description |
|---|---|---|
| month | string | `YYYY-MM` |
| day_type | string | `all` \| `weekday` \| `saturday` \| `sunday` |
| hour_local | integer | Local hour, 0-23, of the scheduled departure |
| month_complete | bool | See `network_monthly` |
| *(M block)* | | |

### `data_quality`

One row per service date. Snapshot-count columns come from `feed_quality` (D's own
archives only, D-012). The outage columns describe outages (D-016) within D's own local
day only (00:00-24:00 local): each `feed_gaps` window is clipped to that range before
these columns are computed, since a `feed_gaps` window can extend into the D+1 hours
read for D-011, which would otherwise attribute part of tomorrow's gap to today.
`feed_gaps` itself and `trips.no_data_in_outage` are unaffected by this clipping - the
D+1 window is the right one for judging D's trips.

| Column | Type | Description |
|---|---|---|
| service_date | date (ISO `YYYY-MM-DD`) | Service date this row reports on |
| day_type | string | `weekday` \| `saturday` \| `sunday` |
| distinct_snapshots | integer | `feed_quality.distinct_snapshots` |
| duplicate_snapshots | integer | `feed_quality.duplicate_snapshots` |
| first_snapshot_local | string | `feed_quality.first_snapshot_utc`, converted |
| last_snapshot_local | string | `feed_quality.last_snapshot_utc`, converted |
| longest_outage_s | integer | Largest `feed_gaps.gap_s` within D's own local day, after clipping |
| longest_outage_start_local, longest_outage_end_local | string | That clipped gap's window, converted |
| outages | integer | Count of `feed_gaps` windows that overlap D's own local day, after clipping |
| outage_minutes_06_22 | float | Minutes of clipped outage overlapping 06:00-22:00 local, summed across that day's clipped gaps |
| local_hours_without_snapshots | string | `feed_quality.local_hours_without_snapshots` |
| marker_share | float, nullable | Recorded-time marker share, all non-final stop events with a held value (definitions.md) |
| out_of_scope_trips_in_feed | integer | `feed_quality.out_of_scope_trips_in_feed` (D-013) |
| in_scope_trips | integer | In-scope trips scheduled that day |
| trips_no_realtime_data | integer | `trip_status = no_realtime_data` that day |
| trips_no_realtime_data_in_outage | integer | Of those, `no_data_in_outage` (D-016) |
| cancelled_trips | integer | `trip_status = cancelled` that day |
| coverage_share | float, nullable | Network-wide coverage that day |

### `run_log`

Unchanged. `run_type = aggregate` for a publishing-layer run.

| Column | Type | Description |
|---|---|---|
| run_ts_utc | timestamp (UTC, ISO 8601) | When the run occurred |
| run_type | string | e.g. `setup`, `smoke-ci`, `aggregate` |
| service_date | date (ISO `YYYY-MM-DD`) | Service date the run processed, if applicable |
| stage | string | Pipeline stage name |
| status | string | `ok`, `error`, etc. |
| rows_in | integer | Row count into the stage |
| rows_out | integer | Row count out of the stage |
| duration_s | float | Stage duration in seconds |
| message | string | Free-text status message |

## Parquet schema (Hugging Face warehouse)

Built by `kronoberg_transit.transform` (D-012). Local path today is `data/warehouse/`
(gitignored); the same layout is uploaded to Hugging Face unchanged. Every table is
partitioned by `service_date`, one file per date:
`data/warehouse/<table>/service_date=YYYY-MM-DD/part-0.parquet`. Re-running a date
overwrites that date's partition; it never duplicates rows. All timestamps are naive
`TIMESTAMP` columns holding UTC instants (no timezone-aware type), per CLAUDE.md's
"store timestamps in UTC" rule. `agency.txt` and `attributions.txt` are read from the
same-date static schedule alongside the other GTFS files, to determine trip scope
(D-013).

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
| operator | string, nullable | `organization_name` of this trip's `is_operator = 1` row in `attributions.txt`. Null if the trip has no attribution row (D-013) |
| in_scope | bool | False when `operator` matches another agency in `agency.txt` (any agency other than Länstrafiken Kronoberg). True otherwise, including when `operator` is null (D-013) |
| trip_status | string | `out_of_scope` \| `in_feed` \| `cancelled` \| `no_realtime_data`, checked in that order (D-009, D-010, D-013) |
| first_seen_utc | timestamp, nullable | Earliest TripUpdates snapshot in which the trip appeared, matched on `trip_id` + `start_date` = this service date (D-011). Null if the trip never appeared in TripUpdates, regardless of scope |
| last_seen_utc | timestamp, nullable | Latest such snapshot. Null if the trip never appeared in TripUpdates, regardless of scope |
| no_data_in_outage | bool, nullable | True when `trip_status = no_realtime_data` and the trip's scheduled span (`scheduled_first_departure_utc` to `scheduled_last_arrival_utc`) falls entirely inside one `feed_gaps` window. False for other `no_realtime_data` trips. Null for every other `trip_status` (D-016) |

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
| in_scope | bool | Copied from this row's trip (`trips.in_scope`, D-013) |
| status | string, nullable | `out_of_scope` \| `cancelled` \| `skipped` \| `observed` \| `unobserved`, checked in that order (D-013, D-009). Null for `stop_position = final` (D-008) |
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
| out_of_scope_trips_in_feed | int | Out-of-scope trips that appear in TripUpdates with `start_date` = this service date (D-013). Expected to be zero |
| first_snapshot_utc | timestamp | Earliest snapshot's header timestamp |
| last_snapshot_utc | timestamp | Latest snapshot's header timestamp |
| max_gap_s | int | Largest gap between consecutive snapshots, in seconds |
| gaps_over_300s | int | Count of gaps over 300 seconds |
| local_hours_without_snapshots | string | Comma-separated local hours (00-23) with no snapshots that day |
| next_day_hours_read | string | Comma-separated D+1 local hours read for this run, per D-011 |

### `feed_gaps`

One row per feed outage (D-016): a stretch of more than 300 s with no TripUpdates
snapshot, computed over the full snapshot timeline the transform reads for the service
date (D's own hours plus any D+1 hours read, deduplicated by header timestamp, per
D-011). Unlike `feed_quality`, this spans both D's own archives and the D+1 hours.

| Column | Type | Description |
|---|---|---|
| service_date | date | Service date this row belongs to |
| feed | string | Feed name (`TripUpdates`) |
| gap_start_utc | timestamp | Start of the gap: the snapshot before it (or the window start, D's local midnight, for a `before_first` gap) |
| gap_end_utc | timestamp | End of the gap: the snapshot after it (or the window end, the end of the last hour read, for an `after_last` gap) |
| gap_s | int | `gap_end_utc - gap_start_utc` in seconds. Always > 300 |
| kind | string | `between` (two consecutive snapshots) \| `before_first` (window start to the first snapshot) \| `after_last` (the last snapshot to the window end) |
