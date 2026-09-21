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

To be defined once the transform stage (DuckDB SQL in `src/kronoberg_transit/sql/`) is
built. Will document partition layout, column types and any derived fields per the
definitions in `docs/definitions.md`.
