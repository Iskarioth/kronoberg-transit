# Definitions

Source of truth for every metric in this project. Changes require Marcus's approval and a
matching entry in `decisions.md`.

Status labels:

- **FIXED**: settled, do not change without approval.
- **PROVISIONAL**: working default, expected to be confirmed once real data is inspected.
- **OPEN**: not decided yet. Do not build logic that depends on it.

## Scope

| Item | Value | Status |
|---|---|---|
| Operator | Länstrafiken Kronoberg, Trafiklab code `krono` | FIXED |
| Trips in scope | Trips in the `krono` static schedule for the service date, except those whose operator in `attributions.txt` is another public transport authority listed in the feed's `agency.txt` (any agency other than Länstrafiken Kronoberg). Trips without an attribution row are in scope. Punctuality, coverage, cancelled trips, skipped stops, trips with no realtime data and unobserved stop events all apply to trips in scope only. Out-of-scope trips are counted separately per route and day (D-013) | FIXED |
| Realtime source | KoDa historical GTFS-RT, feed `TripUpdates` | FIXED |
| Schedule source | KoDa historical GTFS static for the **same service date** | FIXED |
| Analysis period | Every service date from 2026-09-01 onward, processed daily. Published figures are by calendar month of service date; a month is published once all its service dates are processed. Daily figures are kept for trends (D-015) | FIXED |

## Time

| Item | Definition | Status |
|---|---|---|
| Storage time zone | UTC everywhere in stored data | FIXED |
| Reporting time zone | `Europe/Stockholm` (handles DST) | FIXED |
| Service day | The GTFS service date. Trips with scheduled times past 24:00 belong to the previous service day, as in the GTFS spec | FIXED |
| Realtime-to-schedule matching | A realtime trip is matched to the static schedule on `trip_id` and `start_date`, using the KoDa static schedule for that start date. Processing service date D reads D's TripUpdates archives plus D+1's archives up to and including the hour containing the time two hours after D's last scheduled arrival. A trip found in D's archives with `start_date` D−1 belongs to D−1 (D-011) | FIXED |
| Day type | `weekday` (Monday–Friday), `saturday` or `sunday`, from the calendar day of the service date (D-017) | FIXED |
| Public holidays | How a public holiday that falls on a weekday is classified. None falls on a weekday before December 2026 | OPEN |

## Observed times

| Item | Definition | Status |
|---|---|---|
| Observed time at a stop | The recorded time at the measurement point from the last TripUpdates snapshot in which that stop (identified by `stop_sequence`) still appears in the trip's `stop_time_update` list, counted as observed only when that value carries `uncertainty = 0`. Values without the marker are last predictions, and the stop event is labelled `unobserved`. Basis (D-007): the `krono` feed keeps each passed stop for about 600 s after its departure time, so removal from the feed is not the passage event. About one snapshot after the event (median 15 s), the feed replaces the prediction with the recorded time and marks it `uncertainty = 0`; the marker never appears on future times. Snapshots arrive about every 16 s | FIXED |

## Punctuality

| Item | Definition | Status |
|---|---|---|
| Delay | Observed time minus scheduled time, in seconds. Positive means late | FIXED |
| Measurement point | Departure time at every stop except the final stop of a trip. Final-stop arrivals are excluded from punctuality and from coverage (D-008) | FIXED |
| Timing stop | A stop event whose `timepoint` in the same-date static schedule is 1, or empty (which GTFS treats as an exact time). Scheduled times at timing stops are whole minutes; at other stops they almost always carry seconds, consistent with times computed between timing stops (D-019) | FIXED |
| Headline stop set | Headline, route and hour-of-day figures are computed on departures at timing stops, final stops excluded (D-008). Every published level also carries the same figures on all non-final stops, labelled by stop set. Station figures are published on all stops, and also on timing stops for stations that have one (D-019) | FIXED |
| On time | −60 s ≤ delay ≤ +180 s (D-018) | FIXED |
| Early | Delay < −60 s. Early departures count as **not on time**: a bus leaving early strands passengers, which is worse than a late bus (D-018) | FIXED |
| Late | Delay > +180 s (D-018) | FIXED |
| Sensitivity | Every headline punctuality figure is also reported at +60 s and +300 s late thresholds, so results do not depend on one arbitrary cut-off. Early stays delay_s < −60 in both versions; on time is −60 ≤ delay_s ≤ +60 (or +300), and late is delay_s > +60 (or +300) | FIXED |
| Hour of day | The local hour, in the reporting time zone, of a stop event's scheduled departure (D-017) | FIXED |
| Station | Stop events are grouped by their stop's `parent_station`; a stop without one is its own station (D-017) | FIXED |

## Exclusions and coverage

| Item | Definition | Status |
|---|---|---|
| Cancelled trips | Trips whose last appearance in TripUpdates has `schedule_relationship = CANCELED`. Their stop events are excluded from punctuality and coverage, and cancelled trips are reported separately as a cancellation rate: cancelled trips ÷ scheduled trips, per route and day (D-009) | FIXED |
| Skipped stops | Stop events at the measurement point whose last realtime value (from the last snapshot in which the stop appears, as in the Observed time rule) has `schedule_relationship = SKIPPED`. Excluded from punctuality and coverage, and reported separately as a skipped-stop rate: skipped stop events ÷ scheduled stop events at the measurement point on trips that were not cancelled, per route and day (D-009) | FIXED |
| Trips with no realtime data | Scheduled trips on the service day that never appear in TripUpdates, matched on `trip_id` and `start_date` (D-011). Reported per route and day as a count and as a share of scheduled trips, next to the cancellation rate. Their stop events are `unobserved`. They are never labelled cancelled (D-010) | FIXED |
| Out-of-scope trips in the feed | Out-of-scope trips that appear in TripUpdates, counted per service day in data_quality. Expected to be zero; a non-zero count means the assumption behind D-013 has changed (D-013) | FIXED |
| Feed outage | A stretch of more than 300 s with no TripUpdates snapshot within the archives read for a service date, including from the start of the first hour read to the first snapshot, and from the last snapshot to the end of the last hour read. An in-scope trip with no realtime data whose whole scheduled span falls inside one outage is counted separately, as no realtime data during a feed outage (D-016) | FIXED |
| Unobserved stop events | Scheduled stop events that are not on a cancelled trip, not skipped (D-009), and have no realtime observation, including those whose last realtime value lacks the recorded-time marker (D-007). They are labelled `unobserved`, **never** counted as on time and never silently dropped | FIXED |
| Stop event status | Every scheduled stop event at the measurement point gets exactly one status, checked in this order: `out_of_scope` (on an out-of-scope trip, D-013), `cancelled` (on a cancelled trip), `skipped`, `observed`, `unobserved` (D-009) | FIXED |
| Coverage | Observed stop events ÷ scheduled stop events at the measurement point (final stops excluded, D-008), per route and day. Stop events on cancelled trips and skipped stop events are excluded from both counts, since each is reported in its own rate (D-009). Reported alongside every punctuality figure | FIXED |
| Reporting floor | A punctuality figure is reportable only when it rests on at least 20 observed trips (distinct in-scope trips with at least one observed departure among the stop events it covers) and coverage of at least 90%, at the level it is shown. Figures below either floor stay in the data, flagged with the reason, and are not presented as results (D-014) | FIXED |
| Added trips | Trips in realtime with no matching scheduled trip: counted and logged, excluded from punctuality | PROVISIONAL |
| Recorded-time marker share | Stop events at the measurement point whose held value carries `uncertainty = 0`, divided by stop events at the measurement point with a held value, per service day. Reported in data_quality. A drop signals a change in the operator's system | FIXED |
| Marker share alert level | Below which a service day is flagged in data_quality | OPEN |
