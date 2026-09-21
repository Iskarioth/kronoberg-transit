# Decision log

One entry per decision that affects data, results or architecture. Newest at the bottom.
Format: date, decision, reason, consequences.

---

## D-001 · 2026-09-21 · Google Sheets is the serving layer, not storage

**Decision:** Cleaned stop-level data lives in Parquet. Google Sheets holds only daily
aggregates.

**Reason:** A Google spreadsheet is capped at 10 million cells. Measured from KoDa's
static schedule for service date 2026-09-07: 2,162 trips scheduled, 49,204 `stop_times`
rows. Annualized, that is roughly 18 million stop-level rows a year, which is several
times over the cap even at six columns. Route-level daily aggregates for a year are
around 18,000 rows.

**Consequences:** Any analysis that needs stop-level detail runs in DuckDB against the
Parquet files, not in Sheets.

---

## D-002 · 2026-09-21 · KoDa is the raw layer

**Decision:** Raw GTFS-RT archives are not stored by this project. They are re-downloaded
from KoDa when needed.

**Reason:** KoDa already archives every GTFS Regional feed from February 2020 onward, and
data is available the day after publication. Storing raw protobuf ourselves would
duplicate an existing public archive.

**Consequences:** The pipeline must be able to rebuild any date from KoDa. If KoDa becomes
unavailable, historical reprocessing stops until it returns.

---

## D-003 · 2026-09-21 · Looker Studio for the public dashboard

**Decision:** The public dashboard is built in Looker Studio on top of Google Sheets.

**Reason:** Free, reads Sheets natively, updates automatically when the pipeline writes,
and produces a public link. Power BI's service requires a work or school account for
sign-up and gates Publish to web behind an admin setting.

**Consequences:** Power BI is reserved for other portfolio projects.

---

## D-004 · 2026-09-21 · Git hook enforces no AI attribution in commits

**Decision:** `.githooks/commit-msg` strips AI attribution lines from every commit
message, in addition to Claude Code's `attribution` setting and the rule in `CLAUDE.md`.

**Reason:** Claude Code's attribution setting has open bug reports where it is ignored,
and a newer `Claude-Session:` trailer is added regardless of the setting. A git hook is
deterministic.

**Consequences:** The hook only covers commits made locally. It cannot clean PR
descriptions or commits created by cloud sessions, so the `CLAUDE.md` rule still matters.

---

## D-005 · 2026-09-21 · Observed time = last prediction before the stop drops from the feed

**Decision:** "Observed time at a stop" is defined as the arrival/departure delay from the
last TripUpdates snapshot in which that stop_id still appears in the trip's
`stop_time_update` list.

**Reason:** Traced two trips (6 and 49 scheduled stops) across roughly 750 real
`krono` TripUpdates snapshots for service date 2026-09-07, polled about every 14
seconds. Once a vehicle passes a stop, KoDa's feed drops that stop's entry entirely -
it is never retained with a frozen value and never marked `SKIPPED`. Exactly one stop
drops off the front of the list per real passage event, and the time between drops
tracks real inter-stop travel time, not the polling interval. This rules out relying on
retained passed-stop values (the feed doesn't keep them) and makes VehiclePositions
proximity unnecessary as a fallback.

**Consequences:** Transform logic must capture each stop's last-seen prediction before
it disappears from `stop_time_update`, rather than looking for a settled/actual value.
Accuracy is bounded by the polling interval (~14 s in the sample inspected), which is
small relative to the punctuality thresholds already in `docs/definitions.md`.

**Status:** PROVISIONAL since D-006; superseded by D-007.

---

## D-006 · 2026-09-21 · D-005 status corrected from FIXED to PROVISIONAL

**Decision:** The observed-time rule from D-005 remains the working default, but its
status in `definitions.md` is PROVISIONAL, not FIXED. It returns to FIXED only after a
full-day validation scan of a weekday and a weekend day has been reviewed. Any change
to the rule itself is logged as its own decision.

**Reason:** D-005 rests on two traced trips (6 and 49 scheduled stops) out of 2,162
scheduled on 2026-09-07, across about 750 snapshots, roughly three hours at ~14 s
polling. That sample shows the clean case: one stop dropping off the front of the list
while the trip continues. It does not cover final stops, where the stop disappears
because the whole trip leaves the feed. It also does not cover trips that leave the
feed mid-route, gaps between snapshots, which of `time` and `delay` the feed populates,
or whether the feed ever marks whole trips `CANCELED`. The Cancelled trips definition
depends on that last point.

**Consequences:** No transform logic that depends on observed time is built until the
scan results for both days have been reviewed. The scan is
`scripts/validate_observed_time.py`, and its reports go in `docs/validation/`. If the
feed never emits trip-level `CANCELED`, the Cancelled trips definition must be revisited
before any cancellation rate is published.

---

## D-007 · 2026-09-21 · Observed time requires the feed's recorded-time marker

**Decision:** The observed time at a stop is the recorded time from the last
TripUpdates snapshot in which that stop (by `stop_sequence`) still appears, counted as
observed only when that value carries `uncertainty = 0`. Held values without the
marker are labelled `unobserved`. Status FIXED. This supersedes D-005 and lifts
D-006's hold on transform work.

**Reason:** From full-day scans of 2026-09-07 (Monday) and 2026-09-06 (Sunday), in
`docs/validation/`:

- Removal from the feed is not the passage event. Each passed stop is kept for about
  600 s after its departure time. In every resolved clean drop (30,025 on Monday,
  10,625 on Sunday) the predicted departure fell before the drop. D-005's two-trip
  trace measured the spacing between drops, not this offset.
- The feed marks recorded times. Of arrival times more than 60 s in the past, 99.5%
  (Monday) and 99.8% (Sunday) carry `uncertainty = 0`. No future time carries it on
  either day, and no non-zero value appears. The marker first appears a median 15 s
  after the event, and the final value is dated a median 14-15 s before the snapshot
  in which it last changed. The system writes the actual time about one snapshot after
  it happens.
- VehiclePositions (Monday, pings every 2 s) agrees. With the marker present, the held
  value is within ±15 s of the GPS-based departure for 91.8% of 44,608 events and
  within ±60 s for 98.8%. Early/on-time/late classification agrees for 97.5%. The
  small negative offset grows with the detection radius (median −4 s at 25 m, −6.5 s
  at 50 m, −12 s at 100 m). That is consistent with the GPS method measuring when the
  bus leaves a circle around the stop, not with an error in the feed.
- Held values without the marker are less reliable: within ±60 s of GPS for 95.0% of
  1,332 events, with a p99 of about 375 s. They are 3.6% of held values on Monday and
  2.9% on Sunday. 85% and 96% of them are final stops of trips removed from the feed
  before the marker was written (see D-008).
- `time − delay` equals the scheduled time from the same-date static schedule on every
  row on both days, so reading `time` or `delay` gives the same delay.

**Consequences:**

- Transform work can proceed on this rule.
- First stops: the held value runs a median 16 s earlier than GPS, against 6 s at
  intermediate stops. Neither source shows any early departure at first stops (0 of
  1,981 on Monday). But the held value may understate first-stop lateness: its on-time
  share exceeds GPS by 2.6 pp at +180 s and 9.6 pp at +60 s. First-stop figures,
  especially at the +60 s sensitivity threshold, are reported with that caveat.
- Across all stops, the held value leans slightly earlier than GPS. With the marker
  present, on-time share differs by 0.5 pp at +180 s, 1.8 pp at +60 s and 0.2 pp at
  +300 s.
- data_quality reports the daily recorded-time marker share. A drop signals a change in
  the operator's system. The alert level is OPEN.
- The VehiclePositions check covers one weekday. VehiclePositions is a validation
  source, not part of the pipeline.

**Correction (2026-09-21):** Drop detection in `scripts/koda_scan_lib.py` compared
stop_ids rather than stop_sequences, which missed some drops on looping trips. After the
fix, the resolved clean drops are 30,126 (Monday) and 10,661 (Sunday), not 30,025 and
10,625. In every one of them the predicted departure still falls before the drop window.

**Addendum (2026-09-21):** Stops can leave the feed and come back. On Monday this
happened on two trips (9 stop events: 8 on one trip, which returned together in a
single snapshot, and 1 on another, absent for about one snapshot). On Sunday it
happened on one trip (2 stop events). Every one of those stops left without the marker
and returned with it, carrying a time a median of about 20 minutes (Monday) and 13
minutes (Sunday) later than its value before it left. For the trips whose stops
returned together, the feed did not resend an earlier state: the returned stop list
matches no earlier snapshot, and the trip-level timestamp moved forward. On Monday,
every returned value is within 9 s of the GPS-based departure, while the values from
before the stops left are 153 s to 1,249 s earlier than GPS. Taking the value from the
last appearance, as this decision does, picks the recorded time in every case.

---

## D-008 · 2026-09-21 · Punctuality measured at departures; final-stop arrivals excluded

**Decision:** Punctuality is measured at the departure time of every stop except a
trip's final stop. Final-stop arrivals are excluded from punctuality and from coverage.
The Measurement point moves from PROVISIONAL to FIXED.

**Reason:** From the same scans as D-007:

- The feed rarely records final arrivals. Completed trips are removed from the feed a
  median 10 s (Monday) and 9 s (Sunday) after their final arrival time, while the
  recorded-time marker takes a median 15 s to appear. About 70% of final-stop held
  values carry no marker on both days, and 22.1% (Monday) and 20.4% (Sunday) of trips
  are removed before their final arrival time is reached. Under D-007 most final stops
  would be unobserved.
- An early arrival at the end of a trip does not make anyone miss a bus. Of 1,902
  final-stop arrivals on Monday, 41.5% were early by the held value and 43.6% by GPS.
  Counting them as not on time would lower punctuality for something that is not a
  service failure for passengers. Early departures still count as not on time at
  every other stop.
- With final stops excluded, held values without the marker are rare: 247 on Monday
  and 18 on Sunday, about 0.6% and 0.1% of held values at non-final stops.

**Consequences:**

- Coverage uses the same set: scheduled departures at non-final stops.
- Arrival punctuality at the end of a trip is out of scope. It matters most for
  transfers at hubs, where a late arrival can cost a connection. The write-up lists
  this as a limitation.
- The transform still extracts final-stop events with their marker flag, so this
  choice can be revisited without refetching.
