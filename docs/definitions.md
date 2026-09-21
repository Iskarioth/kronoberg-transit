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
| Realtime source | KoDa historical GTFS-RT, feed `TripUpdates` | FIXED |
| Schedule source | KoDa historical GTFS static for the **same service date** | FIXED |
| Analysis period | To be set after the first backfill test | OPEN |

## Time

| Item | Definition | Status |
|---|---|---|
| Storage time zone | UTC everywhere in stored data | FIXED |
| Reporting time zone | `Europe/Stockholm` (handles DST) | FIXED |
| Service day | The GTFS service date. Trips with scheduled times past 24:00 belong to the previous service day, as in the GTFS spec | FIXED |

## Observed times

| Item | Definition | Status |
|---|---|---|
| Observed time at a stop | How the observed departure is inferred from TripUpdates predictions. Candidates: (a) last prediction before the vehicle passed the stop, (b) passed-stop values if the feed retains them, (c) VehiclePositions proximity. Decide after inspecting one real day of `krono` data | OPEN |

## Punctuality

| Item | Definition | Status |
|---|---|---|
| Delay | Observed time minus scheduled time, in seconds. Positive means late | FIXED |
| Measurement point | Departure time at every stop, except the final stop of a trip, which uses arrival | PROVISIONAL |
| On time | −60 s ≤ delay ≤ +180 s | PROVISIONAL |
| Early | Delay < −60 s. Early departures count as **not on time**: a bus leaving early strands passengers, which is worse than a late bus | PROVISIONAL |
| Late | Delay > +180 s | PROVISIONAL |
| Sensitivity | Every headline punctuality figure is also reported at +60 s and +300 s late thresholds, so results do not depend on one arbitrary cut-off | FIXED |

## Exclusions and coverage

| Item | Definition | Status |
|---|---|---|
| Cancelled trips | Trips with `schedule_relationship = CANCELED` are excluded from punctuality and reported separately as a cancellation rate | FIXED |
| Unobserved stop events | Scheduled stop events with no realtime observation are labelled `unobserved`. They are **never** counted as on time and never silently dropped | FIXED |
| Coverage | Observed stop events ÷ scheduled stop events, per route and day. Reported alongside every punctuality figure | FIXED |
| Minimum coverage | Below which a route-day is flagged as unreliable in reporting | OPEN |
| Added trips | Trips in realtime with no matching scheduled trip: counted and logged, excluded from punctuality | PROVISIONAL |
