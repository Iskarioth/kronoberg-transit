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

**Correction (2026-09-21):** The validation scans excluded `SKIPPED` stop updates before
picking each stop's last value. As a result, on 2026-09-07 they counted 41 stops that
D-009 classes as skipped as held values without the marker. Measured in the warehouse
(D-012), excluding cancelled trips and skipped stops, non-final stops with a held value
but no marker are 206 on 2026-09-07 and 18 on 2026-09-06: about 0.5% and 0.1% of
non-final stops with a held value. The D-007 figures for values without the marker
(3.6% on Monday, 85% of them final stops) include the same 41 stops. The conclusions of
D-007 and D-008 do not change.

---

## D-009 · 2026-09-21 · Skipped stops, and what coverage counts

**Decision:** A stop event whose last realtime value is marked `SKIPPED` is excluded
from punctuality and reported as a skipped-stop rate. It is never counted as on time
and never labelled unobserved. A trip is cancelled when its last appearance in
TripUpdates is marked `CANCELED`. Stop events on cancelled trips and skipped stop
events are excluded from both counts in coverage. Every scheduled stop event at the
measurement point gets exactly one status, checked in this order: cancelled, skipped,
observed, unobserved.

**Reason:**

- The feed does mark stops `SKIPPED`: 2,490 stop updates on 2026-09-07, none on
  2026-09-06. D-005 had assumed it never did.
- A skipped stop is a positive statement that the stop was not served, not a gap in the
  data. Labelling it unobserved would misreport it, and counting it as on time would
  hide a service failure.
- Coverage did not say whether cancelled trips and skipped stops were in its
  denominator. Leaving them in would count one cancellation twice: once in the
  cancellation rate and again as lost coverage. Coverage should measure how much of the
  service that ran is observed.
- Trip-level `CANCELED` is rare (82 trip observations across 2 trips on 2026-09-07, none
  on 2026-09-06). Reading the status from the trip's last appearance matches how D-007
  reads stop values, and handles a trip whose status changes.
- Without a fixed order, a skipped stop's last value, which carries no recorded-time
  marker, would also qualify as unobserved under D-007.

**Consequences:**

- The transform assigns one status per stop event, in the order above.
- A trip cancelled after it started loses its already-observed departures from
  punctuality. With 2 cancelled trips on the validated weekday, this is accepted for
  simplicity.
- The route-day output needs a skipped-stop count and rate. `docs/data_dictionary.md` is
  updated when the aggregation is built.

---

## D-010 · 2026-09-21 · Trips with no realtime data are reported, never assumed cancelled

**Decision:** Scheduled trips that never appear in TripUpdates for their service date
are reported per route and day as trips with no realtime data, as a count and as a
share of scheduled trips, next to the cancellation rate. Their stop events are
unobserved. They are never labelled cancelled.

**Reason:**

- Far more trips are missing than flagged. On 2026-09-07, 122 of 2,162 scheduled trips
  (5.6%) never appeared, while 2 were marked `CANCELED`. On 2026-09-06, 28 of 729 (3.8%)
  never appeared and none were marked.
- The feed alone cannot say why a trip is missing: it may not have run, or it may have
  run without its data reaching the feed. Calling missing trips cancelled would
  overstate cancellations. Leaving them out would make a cancellation rate built only
  on `CANCELED` look cleaner than the service was.
- Some missing trips may run late in the evening or after midnight, where their data
  would sit in the next date's archives, which the validation scans did not read (see
  D-011).

**Consequences:**

- Coverage already falls when trips are missing, since their stop events are
  unobserved. This count shows how much of that gap is whole trips.
- The count is read alongside data_quality. During a gap in the snapshots, trips can be
  missing because the feed was, not because the service was.
- The route-day output needs this count and share. `docs/data_dictionary.md` is updated
  when the aggregation is built.

**Addendum (2026-09-21):** The missing trips are not explained by where the archives
end. Of the 122 missing trips on 2026-09-07, 121 were scheduled to start between that
day's first and last snapshot; on 2026-09-06, 26 of 28 were. The remaining 1 and 2 were
scheduled to start after that day's last snapshot.

**Addendum (2026-09-22):** Almost all the missing trips in this decision's evidence were
run by neighbouring authorities, which D-013 puts out of scope: 116 of the 121 trips
with no realtime data on 2026-09-07 (after reading the next day's archives, D-011) and
all 28 on 2026-09-06. Within scope, 5 of 2,046 trips had no realtime data on 2026-09-07
and 0 of 701 on 2026-09-06. The rule stands: trips with no realtime data are reported
and never assumed cancelled.

---

## D-011 · 2026-09-21 · Matching realtime trips to the schedule by service date

**Decision:** Realtime trips are matched to the static schedule on `trip_id` and
`start_date`, using the KoDa static schedule for that start date. Processing service
date D reads D's TripUpdates archives plus D+1's archives up to and including the hour
containing the time two hours after D's last scheduled arrival. A trip found in D's
archives with `start_date` D−1 belongs to D−1.

**Reason:**

- A date's archives contain trips from the previous service date. On 2026-09-06, 59 of
  760 realtime trips did not match that date's schedule. All 59 carried start date
  2026-09-05 and all matched the 2026-09-05 schedule. Matching against the date's own
  schedule alone would have left them unmatched, to be counted as added trips.
- The reverse also holds: a service date's trips that run after midnight appear in the
  next date's archives. KoDa's hourly archives follow Europe/Stockholm local hours; the
  2026-09-06 hour 00 archive starts at 23:59:43 local time on the 5th.
- `start_date` is populated on every realtime trip on both validated days.
- The two-hour margin covers late running and the roughly 600 s the feed keeps passed
  stops (D-007), at the cost of a few extra hourly archives.

**Consequences:**

- The Service day rule is unchanged. This decision makes it operational.
- A realtime trip that still matches no scheduled trip under this rule is an added trip
  (PROVISIONAL). None occurred on the validated days.
- The fetcher requests local hours 00–23. On the two days a year when Sweden changes
  clocks, the local day has 23 or 25 hours, and how KoDa names those archives is
  unverified. This must be resolved before the analysis period, still OPEN, includes
  such a day.

---

## D-012 · 2026-09-21 · Warehouse grain: one row per scheduled stop event

**Decision:** The Parquet warehouse holds one row per scheduled stop event per service
date (`stop_events`) and one row per scheduled trip (`trips`). Alongside them are the
day's `routes` and `stops` from the same-date static schedule, and one row per feed per
day of snapshot statistics (`feed_quality`). All tables are partitioned by service date.
Punctuality classes are not stored; aggregations derive them from `delay_s`. Final stops
are kept with their arrival values and marker, but have no status, since they are
outside the measurement point (D-008).

**Reason:**

- Every Sheet tab, coverage figure and sensitivity version can be computed from one
  table, and anyone using the public dataset can reproduce them.
- Keeping unobserved, skipped and cancelled stop events as rows means nothing is
  silently dropped.
- The On time, Early and Late thresholds are still PROVISIONAL. Storing the delay rather
  than the class means a threshold change needs no reprocessing.
- Raw archives are never stored by this project (CLAUDE.md), so snapshot statistics have
  to be captured when a day is processed, or they are lost.

**Consequences:**

- The Sheet tabs become aggregates of these tables. Their schemas are revised when the
  aggregation step is built.
- Trips are labelled `in_feed`, `cancelled` or `no_realtime_data`. `in_feed` means the
  trip appeared in TripUpdates, not that it ran as scheduled.

---

## D-013 · 2026-09-22 · Trips run by neighbouring authorities are out of scope

**Decision:** A trip in the `krono` static schedule is out of scope when its operator in
`attributions.txt` is another public transport authority listed in the feed's
`agency.txt`, meaning any agency other than Länstrafiken Kronoberg. Out-of-scope trips
and their stop events are excluded from punctuality, coverage, cancellations and trips
with no realtime data, and are counted separately per route and day. All other trips
are in scope, including any trip without an attribution row.

**Reason:**

- On both validated days, every trip has exactly one operator attribution. Trips run by
  neighbouring authorities (Skånetrafiken, Kalmars Länsstrafik, Hallandstrafiken,
  Jönköpings Länstrafik, Blekinge Trafiken) never appeared in TripUpdates: 28 of 28 on
  2026-09-06 and 116 of 116 on 2026-09-07. Trips run by Connect bus appeared in 701 of
  701 and in 2,039 of 2,046 (2 cancelled, 5 with no realtime data).
- The split holds within mixed routes. On route 310 (2026-09-06) and route 320
  (2026-09-07), the Connect bus trips are in the feed and the other authorities' trips
  are not.
- Counting these trips would report a gap in the `krono` feed that is really a
  difference in who runs the trip. Coverage within scope is 99.9% (2026-09-06) and
  99.4% (2026-09-07). Across all trips it is 94.8% and 94.4%.
- The rule names a category (another authority in the same feed), not the current
  contractor, so it survives a change of operator.
- Whether these trips appear in the neighbouring authorities' own realtime feeds is not
  verified.

**Consequences:**

- Out-of-scope trips get trip status `out_of_scope`, and their non-final stop events get
  status `out_of_scope`, which takes precedence over every other status. Their
  first-seen and last-seen times are still recorded.
- data_quality counts out-of-scope trips that appear in TripUpdates. The expected count
  is zero; a non-zero count means the assumption behind this decision has changed.
- Routes with both in-scope and out-of-scope trips, such as 310 and 320, are reported on
  their in-scope trips only, with the in-scope share of the route's scheduled trips
  shown next to the figures.
- Measuring these trips from the neighbouring authorities' feeds is a possible later
  extension, not part of this project's scope.
