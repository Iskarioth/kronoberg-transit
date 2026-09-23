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

---

## D-014 · 2026-09-22 · Reporting floor: 20 observed trips and 90% coverage

**Decision:** A punctuality figure is reportable only when it rests on at least 20
observed trips and at least 90% coverage, at the level it is shown: for example a route
in a month, a route on a day, or a station in a month. Observed trips are distinct
in-scope trips with at least one observed departure among the stop events the figure
covers. Figures below either floor stay in the data, flagged with the reason, and are
not presented as results.

**Reason:**

- Departures on the same trip are not independent: a bus running late at one stop is
  usually late at the next. Trips are the more honest sample size. Treating each trip as
  one observation, an on-time share near 60% from 10 trips is uncertain by roughly
  ±30 pp, and from 20 trips by roughly ±22 pp.
- Within scope (D-013), coverage is rarely the binding limit. Every route-day on
  2026-09-06 and 88 of 100 on 2026-09-07 are at 95% or above. A 90% floor still excludes
  the real partial-data cases, such as route 775 (48%) and route 31 (route_id
  9011007003100000, 62%) on 2026-09-07. It keeps route 106 (92%, 488 observed
  departures), which a 95% floor would drop.
- On 2026-09-07, 32 of 100 in-scope route-days pass, holding 85% of the day's observed
  departures. On 2026-09-06, 10 of 29 pass, holding 74%. Daily figures work for the
  larger routes; smaller routes need a longer period (D-015).
- Flagging instead of dropping keeps thin routes visible, consistent with never
  silently dropping data.

**Consequences:**

- Every aggregate carries its observed trips, observed departures, coverage, and a
  reportable flag with the reason.
- The floor applies to headline figures and their +60 s and +300 s sensitivity versions
  alike.

---

## D-015 · 2026-09-22 · Analysis period: daily from 2026-09-01, published by calendar month

**Decision:** The pipeline processes every service date from 2026-09-01 onward.
Published figures are by calendar month of service date. A month is published once
every service date in it has been processed, including the next-day archives D-011
needs for its last day. Daily figures are kept for trends, and for the larger routes
that pass the reporting floor on a single day (D-014).

**Reason:**

- Calendar months are the natural unit for the write-up and for comparisons ("in
  September, route X…").
- Many routes run only a few trips a day: on 2026-09-07, 53 of 100 in-scope route-days
  had fewer than 10 observed trips. Over a month, a route with one trip each weekday
  reaches about 20 observed trips, enough to meet the floor.
- September 2026 is the first full calendar month the project can process, and it
  contains both validated dates (2026-09-06 and 2026-09-07).

**Consequences:**

- Months differ in their mix of weekdays, Saturdays and Sundays, and in holidays and
  school breaks, so month-to-month comparisons can shift for calendar reasons alone.
  Monthly figures are also reported by day type. The day-type definition, including
  public holidays, is set with the aggregation step.
- Until a month is complete, its figures are shown as month to date and marked
  incomplete.
- The daylight-saving handling flagged in D-011 must be in place before the pipeline
  processes 2026-10-25.
- September 2026 is complete once the early archives of 2026-10-01 have been processed.

---

## D-016 · 2026-09-22 · Feed outages are recorded, and missing trips inside them counted separately

**Decision:** A feed outage is a stretch of more than 300 s with no TripUpdates snapshot
within the archives read for a service date. That includes the stretch from the start of
the first hour read to the first snapshot, and from the last snapshot to the end of the
last hour read. Outages are stored per service date with their start and end times. An
in-scope trip with no realtime data whose whole scheduled span falls inside one outage
is counted separately, as no realtime data during a feed outage.

**Reason:**

- On every backfilled date from 2026-09-01 to 2026-09-20, the feed has at least one fully
  dark local hour at night. On 2026-09-08 it was dark from 01:09 to 04:39 local, and on
  2026-09-19 from 03:22 to 05:29. A trip scheduled inside such a window cannot appear in
  the feed, so its absence says nothing about the service.
- `local_hours_without_snapshots` lists only clock hours with no snapshots at all, and
  `max_gap_s` gives only the length of the largest gap. Neither says when the feed was
  dark, which is what matters when judging a missing trip.
- 300 s is far above the normal cadence of about 16 s, so ordinary jitter does not count
  as an outage.

**Consequences:**

- The warehouse gains a `feed_gaps` table and a column on `trips` marking missing trips
  that fall inside an outage.
- data_quality reports outage windows, and the count of missing trips inside outages
  next to the count from D-010.

---

## D-017 · 2026-09-22 · Publishing layer: one tab per level, with counts and a floor flag on every row

**Decision:** The Google Sheet holds one tab per level at which figures are published:
network_monthly, route_monthly, station_monthly and hour_monthly by calendar month and
day type; route_daily for trends; and data_quality and run_log. Every row stores:

- the counts behind its shares: eligible, observed and unobserved departures, observed
  trips, and departures in each punctuality class at the base and sensitivity thresholds
- the shares themselves
- a reportable flag with its reason, evaluated at that row's own level (D-014)

Routes are keyed on route_id and labelled with their number plus the first and last
stations of their most common trip pattern. Each run rebuilds every tab from the full
warehouse.

**Reason:**

- The reporting floor applies at the level shown, and shares cannot be averaged across
  rows, so each published level needs its own rows and its own flag.
- Storing counts lets anyone recompute and check a figure, and lets the dashboard show
  the numbers behind it.
- Six route numbers map to more than one route_id (31, 12, 1, 2, 3 and 14), and
  route_long_name is empty in this feed, so the number alone does not identify a route.
- The publishing layer is small (roughly 3,000 route_daily rows and 450 route_monthly
  rows per month), so rebuilding it on every run is simpler and safer than updating rows
  in place.
- Hotspots are ranked at station level. Every stop served on the validated days has a
  parent station, and a station is what passengers recognise.

**Consequences:**

- The earlier stop_hotspots and hour_of_day tabs are replaced by station_monthly and
  hour_monthly.
- On time, Early and Late use the thresholds in definitions.md, which are still
  PROVISIONAL. Figures are not presented as results until those thresholds are FIXED.
- Monthly rows carry a month-complete flag. A month is complete when every service date
  in it has a warehouse partition (D-015).
- The Sensitivity row now states its inequalities explicitly. Its meaning is unchanged:
  only the late-side threshold moves.

---

## D-018 · 2026-09-22 · On-time thresholds fixed at −60 s to +180 s

**Decision:** The On time, Early and Late definitions move from PROVISIONAL to FIXED,
with their thresholds unchanged:

- early is a delay below −60 s
- on time is from −60 s to +180 s, both limits inclusive
- late is above +180 s

The +60 s and +300 s sensitivity versions stay as defined.

**Reason:**

- A late limit of three minutes is the common convention for Swedish bus
  punctuality. Västtrafik counts a bus departure as on time when it leaves a timing
  stop no more than 30 s early and no more than 3 minutes late (as reported by Partille
  Tidning, March 2026). Roslagsbanan's departure punctuality uses the same window as this
  project: a departure more than 1 minute early or more than 3 minutes late is off time
  (Transdev Sverige quality report for 2024). Danish rail sets its punctuality limit just
  under 3 minutes, because customer surveys show that is where passengers start to
  experience a delay (Riksdagen report 2020/21:RFR5).
- The early limit is 60 s rather than Västtrafik's 30 s, to leave room for the measured
  lean of recorded times. Against GPS, recorded departures run a median 6 s earlier at
  intermediate stops and 16 s earlier at first stops (D-007). A 30 s limit would count
  part of that measurement lean as early running.
- "Three minutes" can mean up to 3:00, or up to 3:59 if whole minutes are counted, as
  Swedish rail statistics do: rail's five-minute limit is 5 minutes 59 seconds
  (Trafikverket). The bus sources do not say which. This project uses the stricter
  reading, 180 s.
- These are the values set provisionally at the project's start. They are fixed on the
  grounds above, not on the figures they produce.

**Consequences:**

- The hold in D-017 is lifted: figures computed with these thresholds can be presented
  as results, subject to the reporting floor (D-014).
- This project measures departures at every non-final stop (D-008). Operators such as
  Västtrafik measure at timing stops only, so these figures are not directly comparable
  with an operator's published punctuality. The write-up states this.
- Whether the `krono` schedule marks timing stops (the GTFS `timepoint` field) is being
  checked. A timing-stop view would be a separate decision.

**Sources:**

- Partille Tidning, "Här är de mest försenade busslinjerna i Partille", March 2026:
  https://www.partilletidning.se/nyheter/har-ar-partilles-mest-forsenade-busslinjer.091caf51-c28e-4a75-a872-63a54ed28d68
- Transdev Sverige AB, kvalitetsrapport järnvägstrafik 2024 (via ERA):
  https://www.era.europa.eu/sites/default/files/2025-05/kvalitetsrapport%20%28era%29%20transdev%20sverige%20ab%20j%C3%A4rnv%C3%A4gstrafik%20f%C3%B6r%202024.pdf
- Sveriges riksdag, Punktlighet för persontrafik på järnväg – en uppföljning (2020/21:RFR5):
  https://www.riksdagen.se/sv/dokument-och-lagar/dokument/rapport-fran-riksdagen/punktlighet-for-persontrafik-pa-jarnvag-en_h80wrfr5/html/
- Trafikverket, Järnkoll på persontågens punktlighet:
  https://www.trafikverket.se/resa-och-trafik/jarnvag/jarnkoll--fakta-om-svensk-jarnvag/jarnkoll-pa-persontagens-punktlighet/

---

## D-019 · 2026-09-22 · Headline punctuality is measured at timing stops

**Decision:** Headline, route and hour-of-day punctuality is measured on departures at
timing stops (`stop_times.timepoint` = 1), with final stops excluded as before
(D-008). Every published level also carries the same figures computed on all non-final
stops, labelled by stop set. Station figures are published on all stops, and also on
timing stops for stations that have one. The measurement point itself (D-008) is
unchanged: this decision selects which of its stop events the headline uses.

**Reason:**

- The `krono` schedule marks timing stops. On 2026-09-06 and 2026-09-07, every stop time
  has `timepoint` 0 or 1, about 15% of in-scope stop events are timing stops, and every
  in-scope trip starts and ends at one, with a median of 3 per trip.
- Scheduled times at timing stops are always whole minutes. At other stops, 98% carry
  non-zero seconds (2026-09-07). That is consistent with times computed between timing
  stops rather than times the operator publishes. Measuring early and late against those
  times at 85% of stops would mix punctuality with the way the times were produced.
- Swedish operators such as Västtrafik measure at timing stops (D-018), so a timing-stop
  headline is the more comparable one.
- The choice was made before any punctuality figure at timing stops was computed.

**Consequences:**

- First stops make up about 40% of timing-stop departures, because every trip starts at
  one. D-007 found that recorded departures at first stops run a median 16 s earlier
  than GPS, which understates lateness there: by 2.6 pp at +180 s and 9.6 pp at +60 s on
  2026-09-07. As a rough estimate from that one day of GPS data, the timing-stop
  headline therefore leans optimistic by about 1 pp at +180 s and about 4 pp at +60 s.
  The write-up states this.
- The all-stops version stays published. It reflects the times a journey planner built
  on this feed shows at ordinary stops, and it covers every place passengers board.
- Timing-stop figures are closer to how operators measure, but thresholds and stop
  selection still differ from any single operator's published punctuality.
- The warehouse marks each stop event as a timing stop or not, and the Sheet tabs gain a
  stop set column.

---

## D-020 · 2026-09-22 · The pipeline runs daily in GitHub Actions, with the Hugging Face dataset as the warehouse

**Decision:** A scheduled GitHub Actions workflow runs the pipeline once a day. Each run:

- processes every service date from the day after the latest date in the Hugging Face
  dataset up to two days before the run date (Europe/Stockholm), at most seven dates
  per run
- uploads each date's warehouse partitions to the dataset
- then rebuilds the Google Sheet from the full dataset

The Hugging Face dataset is the warehouse of record. Archives are requested only for
the local hours that exist on each service date.

**Reason:**

- The local machine ran out of memory twice during backfills, and a scheduled task
  there only runs while the machine is on. A fresh CI runner each day avoids both, and
  nothing builds up on disk.
- A service date needs the next day's early hours (D-011), and KoDa serves a day's data
  only after that day has ended, so the latest date that can be processed is two days
  back. Processing every missing date up to that point means a failed or skipped run is
  caught up by the next one.
- KoDa labels archives by Europe/Stockholm local hour. On 2025-10-26 (autumn change) the
  02 archive holds both occurrences of the repeated hour. On 2026-03-29 (spring change)
  there is no 02 archive, and the snapshot timeline is continuous across it. Requesting
  only the local hours that exist reads every snapshot once; requesting 00–23
  unconditionally fails on spring-change days.
- Scheduled times around daylight-saving changes are not yet verified. Comparing the
  feed's own scheduled instants (time minus delay) with the pipeline's on 2025-10-25,
  2025-10-26 and 2026-03-28 found systematic differences on after-midnight stop times,
  which are still being investigated. Until that is settled, the pipeline does not
  process a service date on, or the day before, a daylight-saving change.
- Archive file names carry a local time marked with a Z suffix, so only the feed header
  timestamp is used for time.

**Consequences:**

- The 20 dates already built locally (2026-09-01 to 2026-09-20) are uploaded once from
  the local warehouse, and CI continues from 2026-09-21.
- A date that fails a hard check or the out-of-scope tripwire (D-013) stops the run
  before that date is uploaded; the next run retries it.
- Intermediate build files are deleted after each successful date. Raw archives and
  static schedules can be fetched again from KoDa when needed.
- The daylight-saving question raised in D-011 is not resolved by this decision; it is
  deferred to a later decision, and the pipeline guards against processing a date it
  affects until then.
- Processing stops at 2026-10-24, the first service date affected, until the
  daylight-saving question is settled in a later decision. That date becomes
  processable on 2026-10-26.

Amended 2026-09-23: the schedule moved from 06:00 to 05:23 UTC. GitHub delays and
sometimes drops scheduled runs at the start of the hour, and the first scheduled run
(2026-09-23 06:00 UTC) never fired. Date selection is unchanged: it uses Stockholm time
in pipeline.py.

---

## D-021 · 2026-09-22 · Stop events in the daylight-saving window are excluded, and schedule mismatches are counted

**Decision:** On a date when Europe/Stockholm changes its UTC offset, stop events whose
timetable clock time falls between 02:00 and 03:59 local on that date are marked
`dst_ambiguous`. That covers times written 02:00–03:59 on that date's service, and
26:00–27:59 on the previous date's service. They are excluded from punctuality and
coverage and counted separately. Every service date also records how many stop events
on matched trips have a feed scheduled time that differs from the pipeline's outside
that window, and how many realtime trips match no scheduled trip. Both counts are
expected to be zero. The date guard added in D-020 is removed.

**Reason:**

- On the night before the spring change (2026-03-28 service), departures written 26:xx
  fall in the hour that does not exist. The GTFS noon-minus-12-hours rule places them one
  hour earlier than the operator's own scheduled time, and the recorded departures match
  the operator's time. As a result, 118 departures between 03:00 and 04:20 local on
  2026-03-29 got delays inflated by about an hour. Two stops written 27:00 were off by
  about 57 minutes.
- After the rule, one departure on the 2026-03-28 service still shows a large delay:
  route 7, trip 76110000042348084, stop 7, scheduled 25:55:22 (01:55 local, five minutes
  before the clock change). Its recorded departure is 04:00:01 local, 65 minutes later
  in elapsed time. The feed's own scheduled time agrees with the pipeline's, and the
  recorded time comes from the feed, so it is kept as an observed departure. The data
  cannot show whether the delay was real or a clock fault at the operator.
- Around the autumn change (2025-10-25 and 2025-10-26 service), published delays were
  clean: no observed departure was 30 minutes or more off. The rule covers autumn too,
  because the evidence is one night of each kind, and one symmetric rule is easier to
  state and check than a spring-only exception.
- The window is defined on the timetable's clock time, not on converted times, because
  that is where the ambiguity sits.
- On 2025-10-25 and 2025-10-26 the feed also tagged some after-midnight trips with a
  start date on which their service does not run (about 50 trips each night). They
  match no scheduled trip on that date, so they cannot produce a wrong delay. Instead,
  their real service date loses those observations and counts the trips as having no
  realtime data. A per-date count of unmatched realtime trips catches this; a per-date
  count of schedule mismatches on matched trips catches differences like the spring
  one. From 2026-09-01 to 2026-09-20 both counts are zero on every date.

**Consequences:**

- The Stop event status order gains `dst_ambiguous` after `skipped`. Coverage and
  Unobserved stop events exclude it.
- data_quality reports `dst_ambiguous` departures, schedule-mismatch stop events and
  unmatched realtime trips per date. A non-zero count of either of the last two marks
  that date's pipeline run as a warning rather than stopping it, because neither known
  cause produces wrong delays. Mislabelled trips show up instead as lower coverage on
  their real service date, which the count explains.
- The pipeline no longer refuses dates next to a daylight-saving change (D-020).
- Everywhere else, scheduled times still follow the GTFS noon-minus-12-hours rule.
