---
license: cc0-1.0
pretty_name: Kronoberg transit punctuality
language:
- en
tags:
- gtfs
- gtfs-realtime
- public-transport
- punctuality
- sweden
size_categories:
- 1M<n<10M
configs:
- config_name: stop_events
  data_files: "data/stop_events/*/*.parquet"
  default: true
- config_name: trips
  data_files: "data/trips/*/*.parquet"
- config_name: routes
  data_files: "data/routes/*/*.parquet"
- config_name: stops
  data_files: "data/stops/*/*.parquet"
- config_name: feed_quality
  data_files: "data/feed_quality/*/*.parquet"
- config_name: feed_gaps
  data_files: "data/feed_gaps/*/*.parquet"
---

# Kronoberg transit punctuality

Stop-level departure data for public transport in Kronoberg county, Sweden, run by Länstrafiken Kronoberg. Each row in the main table is one scheduled stop event: when the bus was due to leave, when the realtime feed recorded it leaving, and the difference in seconds. The data starts on 1 September 2026 and grows by one service date a day.

I built it as part of a portfolio project on how punctual public transport in Kronoberg is, where and when it falls behind, and how far the realtime data can be trusted to say so. The pipeline code, the metric definitions and the decision log behind every rule are on GitHub: [Iskarioth/kronoberg-transit](https://github.com/Iskarioth/kronoberg-transit).

## Tables

Each table is a separate config, partitioned by service date as `data/<table>/service_date=YYYY-MM-DD/part-0.parquet`. Every file also carries `service_date` as a column. The row counts are for Monday 21 September 2026.

| Config | One row per | Rows on 21 Sep 2026 |
|---|---|---:|
| `stop_events` (default) | Scheduled stop event: one stop on one trip | 49,314 |
| `trips` | Trip scheduled on the service date | 2,161 |
| `routes` | Route in that day's static schedule | 136 |
| `stops` | Stop in that day's static schedule | 4,623 |
| `feed_quality` | Service date: snapshot counts and data-quality checks | 1 |
| `feed_gaps` | Stretch of more than 300 seconds without a realtime snapshot | 2 |

Every column of every table is documented in [data_dictionary.md](https://github.com/Iskarioth/kronoberg-transit/blob/main/docs/data_dictionary.md). The ones most analyses need are in `stop_events`:

| Column | Meaning |
|---|---|
| `service_date`, `trip_id`, `stop_sequence` | The key. Join on `stop_sequence`, since a looping trip can visit the same `stop_id` twice |
| `scheduled_departure_utc` | Timetabled departure |
| `held_departure_utc` | Departure time from the last realtime snapshot the stop appeared in |
| `delay_s` | Recorded minus scheduled departure, in seconds. Set only when `status = 'observed'` |
| `status` | `out_of_scope`, `cancelled`, `skipped`, `dst_ambiguous`, `observed` or `unobserved`. Null at a trip's final stop |
| `is_timing_stop` | True at stops the timetable marks as timing stops (GTFS `timepoint`) |

All timestamps are UTC, stored without a time zone. All IDs are strings.

## How it is measured

These are summaries. The exact definitions are in [definitions.md](https://github.com/Iskarioth/kronoberg-transit/blob/main/docs/definitions.md), and the reasoning behind each rule is in [decisions.md](https://github.com/Iskarioth/kronoberg-transit/blob/main/docs/decisions.md) under the decision number given.

- A departure counts as observed only when its time comes from the last realtime snapshot the stop appeared in and carries the feed's recorded-time marker, `uncertainty = 0` (D-007). The feed keeps a passed stop for about ten minutes, so a stop dropping out of the feed says nothing about when the bus left. Compared with GPS positions on 7 September 2026, the recorded times were within 15 seconds for 91.8% and within 60 seconds for 98.8% of 44,608 departures.
- Only departures are measured. A trip's final stop has no status, because trips leave the feed before their final stop gets a recorded time (D-008).
- On time means between 60 seconds early and 180 seconds late, both inclusive. Anything earlier is early and anything later is late. I also publish +60 s and +300 s versions with the same early limit (D-018).
- Coverage is observed departures divided by observed plus unobserved departures. An unobserved departure is never counted as on time. Cancelled and skipped stops are left out of coverage and counted separately (D-009).
- My headline figures use timing stops, where the timetable gives whole-minute times. Times at the other stops look computed between timing stops, and 98% of them carry seconds (D-019).
- Some trips in the Kronoberg timetable are run by neighbouring counties' transport authorities and don't appear in the Kronoberg realtime feed. They stay in the tables with status `out_of_scope` (D-013).
- A scheduled trip that never appears in the feed gets `trip_status = 'no_realtime_data'` in `trips`. It is not assumed cancelled (D-010).
- On the two nights a year when the clocks change, stop events timetabled between 02:00 and 03:59 local time are marked `dst_ambiguous` and excluded (D-021).

## Example

On-time share at timing stops in September 2026, with coverage and counts, in DuckDB:

```sql
SELECT
    count(*) FILTER (WHERE status IN ('observed', 'unobserved')) AS eligible_departures,
    count(*) FILTER (WHERE status = 'observed') AS observed_departures,
    round(observed_departures / eligible_departures, 4) AS coverage_share,
    round(count(*) FILTER (WHERE status = 'observed' AND delay_s BETWEEN -60 AND 180)
          / observed_departures, 4) AS on_time_share
FROM read_parquet(
    'hf://datasets/Traumenteize/kronoberg-transit-punctuality/data/stop_events/*/*.parquet',
    hive_partitioning = false)
WHERE is_timing_stop
  AND service_date BETWEEN DATE '2026-09-01' AND DATE '2026-09-30';
```

Filtering on `status = 'observed'` already leaves out out-of-scope trips, final stops and the daylight-saving window, so the query needs no other filters.

With the `datasets` library:

```python
from datasets import load_dataset

stop_events = load_dataset(
    "Traumenteize/kronoberg-transit-punctuality", "stop_events", split="train"
)
```

## Known limitations

- Recorded times at a trip's first stop run a median 16 seconds earlier than GPS (7 September 2026). About 40% of timing-stop departures are first stops, so on-time shares at timing stops lean optimistic, by roughly 1 percentage point at +180 s and 4 points at +60 s on that day's data.
- The GPS comparison covers one weekday. I haven't repeated it on a weekend or in another month.
- The realtime feed goes dark most nights. In September 2026 that was roughly 01:00 to 04:40 on weekday nights, 03:30 to 05:30 at weekends, and from about 23:35 on Sunday to 04:40 on Monday. Every gap is in `feed_gaps`, and a missing trip whose whole schedule falls inside one is flagged `no_data_in_outage` in `trips`.
- The figures can't be compared directly with an operator's official punctuality statistics, which use their own thresholds and stop selection. Västtrafik, for example, counts −30 s to +3 min at timing stops as on time.
- `routes` and `stops` hold the full lists from each day's static schedule, including routes that didn't run that day.

## Updates

A GitHub Actions workflow runs once a day. It adds each service date two days after it ends: the archive publishes a day only after it is over, and each date also needs the next day's early hours. Each date is its own commit, `data: add service date YYYY-MM-DD`.

When a definition changes, I reprocess the earlier dates and record the change in decisions.md. Reprocessing replaces a date's files, so no date ever holds duplicate rows, but the same date can hold different values at different commits. For a reproducible analysis, load a fixed commit with the `revision` argument.

## Source and licence

Built from Trafiklab's KoDa archive, which keeps past versions of the GTFS Regional data for Länstrafiken Kronoberg (operator code `krono`): the realtime TripUpdates feed and each day's static timetable. KoDa was created by RISE and Vinnova in cooperation with Trafiklab. KoDa and GTFS Regional data are published under CC0, and so is this dataset.

This is an independent project and is not affiliated with Länstrafiken Kronoberg or Trafiklab.
