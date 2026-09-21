# Held-value validation scan: 2026-09-06

Evidence for D-005/D-006 (see `docs/decisions.md`). This report never changes a definition. It tests whether D-005's held value is a recorded actual time or a stale prediction, using TripUpdates alone and, where run, an independent VehiclePositions passage check.

## Header

- Service date: 2026-09-06
- Feeds: TripUpdates
- Hours present (TripUpdates): 24/24 (missing: none)
- Snapshot count (deduplicated): 4541
- First snapshot: 2026-09-05T21:59:43+00:00 UTC / 2026-09-05T23:59:43+02:00 Europe/Stockholm
- Last snapshot: 2026-09-06T21:35:21+00:00 UTC / 2026-09-06T23:35:21+02:00 Europe/Stockholm
- Git commit: 7c426c8ded019b7bb7ed08845042423fa2b6d348
- Run timestamp (UTC): 2026-09-21T12:31:07Z

## Exclusions

- CANCELED trip (snapshot, trip) observations excluded: 0
- SKIPPED stop_time_updates excluded: 0

## Check 1: frozen or drifting

Among 17183 (trip, stop) pairs with a held value:
- Reach t_cross: 17059 / 17183 (99.3%)
- Zero changes after t_cross: 5113 / 17059 (30.0%)
- Drift (held value minus value at t_cross): n=17059
  - percentiles (s): p1=-3.0, p5=0.0, p25=0.0, p50=11.0, p75=26.0, p95=57.0, p99=130.4
  - <=±15s: 57.7%, <=±30s: 80.0%, <=±60s: 95.5%
- t_last_change minus t_cross (s), n=17059: p5=0.0, p25=0.0, p50=16.0, p75=32.0, p95=67.0
- held value minus t_last_change (s), n=17183: p5=-28.0, p25=-19.0, p50=-14.0, p75=-10.0, p95=3.0

By how the stop left:
- Mid-route drop: n=10969, reach t_cross=10969 / 10969 (100.0%), drift median=12.0s
- Trip removal: n=6214, reach t_cross=6090 / 6214 (98.0%), drift median=10.0s

By whether uncertainty is present on the held value:
- Present: n=16689, drift median=12.0s, drift p95=58.0s
- Absent: n=494, drift median=0.0s, drift p95=41.0s

## Check 2: uncertainty

**Arrival** (n=1847445):
- Cross-tab (uncertainty bucket, time-relative-to-now bucket, count): [('present_zero', '>60s_past', 471220), ('absent', '>60s_past', 1050), ('absent', '0-60s_past', 21519), ('absent', '0-60s_future', 65917), ('absent', '>60s_future', 1235622), ('present_zero', '0-60s_past', 52117)]
- Non-zero uncertainty values: none observed
- Stops where uncertainty ever appears: 16673; appears then later disappears: 59
- (now minus time) at first appearance of uncertainty (s), n=16732: p5=4.0, p25=9.0, p50=15.0, p75=21.0, p95=37.0

**Departure** (n=1847445):
- Cross-tab (uncertainty bucket, time-relative-to-now bucket, count): [('present_zero', '>60s_past', 457728), ('absent', '>60s_future', 1243472), ('present_zero', '0-60s_past', 53013), ('absent', '0-60s_past', 24921), ('absent', '0-60s_future', 67217), ('absent', '>60s_past', 1094)]
- Non-zero uncertainty values: none observed
- Stops where uncertainty ever appears: 16539; appears then later disappears: 59
- (now minus time) at first appearance of uncertainty (s), n=16598: p5=6.0, p25=10.0, p50=15.0, p75=19.0, p95=28.0

## Check 3: trip removal

Among 700 trip removals:
- Completed (all remaining stops in the past): 557 / 700 (79.6%)
- Left early (at least one remaining stop in the future): 143 / 700 (20.4%)
- Future-stops-remaining percentiles for left-early trips (n=143): p25=1.0, p50=1.0, p75=1.0, p95=1.0
- Left-early trips where the final stop is among the future stops: 143 / 143 (100.0%)
- Removal time minus final stop's held arrival.time, completed trips with final stop present (n=557 of 557 completed): p5=1.0, p25=5.0, p50=9.0, p75=17.0, p95=36.0

## Check 4: time vs delay

**Arrival** ((time - delay) minus scheduled time in UTC, n=1847445):
- Exactly 0: 1847445 / 1847445 (100.0%)
- Within ±60s: 1847445 / 1847445 (100.0%)
- Percentiles (s): p1=0.0, p5=0.0, p25=0.0, p50=0.0, p75=0.0, p95=0.0, p99=0.0

**Departure** ((time - delay) minus scheduled time in UTC, n=1847445):
- Exactly 0: 1847445 / 1847445 (100.0%)
- Within ±60s: 1847445 / 1847445 (100.0%)
- Percentiles (s): p1=0.0, p5=0.0, p25=0.0, p50=0.0, p75=0.0, p95=0.0, p99=0.0

## Check 5: loose ends

**5a. Per-hour table** (hour, archive_files, distinct_header_timestamps, first_ts, last_ts, trip_entities):
- 00: files=248, distinct_ts=166, first=2026-09-05T21:59:43+00:00 UTC / 2026-09-05T23:59:43+02:00 Europe/Stockholm, last=2026-09-05T22:59:25+00:00 UTC / 2026-09-06T00:59:25+02:00 Europe/Stockholm, trip_entities=23
- 01: files=254, distinct_ts=172, first=2026-09-05T22:59:45+00:00 UTC / 2026-09-06T00:59:45+02:00 Europe/Stockholm, last=2026-09-05T23:59:37+00:00 UTC / 2026-09-06T01:59:37+02:00 Europe/Stockholm, trip_entities=30
- 02: files=254, distinct_ts=171, first=2026-09-05T23:59:56+00:00 UTC / 2026-09-06T01:59:56+02:00 Europe/Stockholm, last=2026-09-06T00:59:32+00:00 UTC / 2026-09-06T02:59:32+02:00 Europe/Stockholm, trip_entities=29
- 03: files=148, distinct_ts=123, first=2026-09-06T00:59:54+00:00 UTC / 2026-09-06T02:59:54+02:00 Europe/Stockholm, last=2026-09-06T01:34:47+00:00 UTC / 2026-09-06T03:34:47+02:00 Europe/Stockholm, trip_entities=12
- 05: files=129, distinct_ts=116, first=2026-09-06T03:29:23+00:00 UTC / 2026-09-06T05:29:23+02:00 Europe/Stockholm, last=2026-09-06T03:59:33+00:00 UTC / 2026-09-06T05:59:33+02:00 Europe/Stockholm, trip_entities=5
- 06: files=252, distinct_ts=225, first=2026-09-06T03:59:48+00:00 UTC / 2026-09-06T05:59:48+02:00 Europe/Stockholm, last=2026-09-06T04:59:55+00:00 UTC / 2026-09-06T06:59:55+02:00 Europe/Stockholm, trip_entities=21
- 07: files=254, distinct_ts=224, first=2026-09-06T05:00:11+00:00 UTC / 2026-09-06T07:00:11+02:00 Europe/Stockholm, last=2026-09-06T05:59:42+00:00 UTC / 2026-09-06T07:59:42+02:00 Europe/Stockholm, trip_entities=34
- 08: files=252, distinct_ts=221, first=2026-09-06T05:59:57+00:00 UTC / 2026-09-06T07:59:57+02:00 Europe/Stockholm, last=2026-09-06T06:59:44+00:00 UTC / 2026-09-06T08:59:44+02:00 Europe/Stockholm, trip_entities=51
- 09: files=250, distinct_ts=220, first=2026-09-06T07:00:00+00:00 UTC / 2026-09-06T09:00:00+02:00 Europe/Stockholm, last=2026-09-06T07:59:45+00:00 UTC / 2026-09-06T09:59:45+02:00 Europe/Stockholm, trip_entities=70
- 10: files=252, distinct_ts=224, first=2026-09-06T08:00:01+00:00 UTC / 2026-09-06T10:00:01+02:00 Europe/Stockholm, last=2026-09-06T08:59:42+00:00 UTC / 2026-09-06T10:59:42+02:00 Europe/Stockholm, trip_entities=73
- 11: files=249, distinct_ts=218, first=2026-09-06T08:59:58+00:00 UTC / 2026-09-06T10:59:58+02:00 Europe/Stockholm, last=2026-09-06T09:59:48+00:00 UTC / 2026-09-06T11:59:48+02:00 Europe/Stockholm, trip_entities=77
- 12: files=249, distinct_ts=217, first=2026-09-06T10:00:03+00:00 UTC / 2026-09-06T12:00:03+02:00 Europe/Stockholm, last=2026-09-06T10:59:47+00:00 UTC / 2026-09-06T12:59:47+02:00 Europe/Stockholm, trip_entities=81
- 13: files=251, distinct_ts=220, first=2026-09-06T10:59:47+00:00 UTC / 2026-09-06T12:59:47+02:00 Europe/Stockholm, last=2026-09-06T11:59:37+00:00 UTC / 2026-09-06T13:59:37+02:00 Europe/Stockholm, trip_entities=83
- 14: files=250, distinct_ts=218, first=2026-09-06T11:59:53+00:00 UTC / 2026-09-06T13:59:53+02:00 Europe/Stockholm, last=2026-09-06T12:59:26+00:00 UTC / 2026-09-06T14:59:26+02:00 Europe/Stockholm, trip_entities=77
- 15: files=253, distinct_ts=222, first=2026-09-06T12:59:57+00:00 UTC / 2026-09-06T14:59:57+02:00 Europe/Stockholm, last=2026-09-06T13:59:55+00:00 UTC / 2026-09-06T15:59:55+02:00 Europe/Stockholm, trip_entities=81
- 16: files=253, distinct_ts=221, first=2026-09-06T14:00:11+00:00 UTC / 2026-09-06T16:00:11+02:00 Europe/Stockholm, last=2026-09-06T14:59:49+00:00 UTC / 2026-09-06T16:59:49+02:00 Europe/Stockholm, trip_entities=86
- 17: files=251, distinct_ts=217, first=2026-09-06T15:00:05+00:00 UTC / 2026-09-06T17:00:05+02:00 Europe/Stockholm, last=2026-09-06T15:59:47+00:00 UTC / 2026-09-06T17:59:47+02:00 Europe/Stockholm, trip_entities=82
- 18: files=251, distinct_ts=214, first=2026-09-06T16:00:03+00:00 UTC / 2026-09-06T18:00:03+02:00 Europe/Stockholm, last=2026-09-06T16:59:41+00:00 UTC / 2026-09-06T18:59:41+02:00 Europe/Stockholm, trip_entities=78
- 19: files=248, distinct_ts=215, first=2026-09-06T16:59:57+00:00 UTC / 2026-09-06T18:59:57+02:00 Europe/Stockholm, last=2026-09-06T17:59:45+00:00 UTC / 2026-09-06T19:59:45+02:00 Europe/Stockholm, trip_entities=57
- 20: files=253, distinct_ts=220, first=2026-09-06T18:00:01+00:00 UTC / 2026-09-06T20:00:01+02:00 Europe/Stockholm, last=2026-09-06T18:59:36+00:00 UTC / 2026-09-06T20:59:36+02:00 Europe/Stockholm, trip_entities=64
- 21: files=252, distinct_ts=208, first=2026-09-06T18:59:53+00:00 UTC / 2026-09-06T20:59:53+02:00 Europe/Stockholm, last=2026-09-06T19:59:41+00:00 UTC / 2026-09-06T21:59:41+02:00 Europe/Stockholm, trip_entities=57
- 22: files=253, distinct_ts=185, first=2026-09-06T19:59:41+00:00 UTC / 2026-09-06T21:59:41+02:00 Europe/Stockholm, last=2026-09-06T20:59:38+00:00 UTC / 2026-09-06T22:59:38+02:00 Europe/Stockholm, trip_entities=34
- 23: files=148, distinct_ts=106, first=2026-09-06T20:59:58+00:00 UTC / 2026-09-06T22:59:58+02:00 Europe/Stockholm, last=2026-09-06T21:35:21+00:00 UTC / 2026-09-06T23:35:21+02:00 Europe/Stockholm, trip_entities=6

**5b. Unmatched trips' start_date distribution (2026-09-06 only):**
- [('20260905', 59)]
- Of 59 realtime trips unmatched to 2026-09-06's static schedule, 59 match 2026-09-05's static schedule

**5c. Snapshot gaps over 300s** (1 found):
  - start 2026-09-06T01:34:47+00:00 UTC / 2026-09-06T03:34:47+02:00 Europe/Stockholm
    end   2026-09-06T03:29:23+00:00 UTC / 2026-09-06T05:29:23+02:00 Europe/Stockholm: 6876s

**5d. Trips that leave the feed and come back:**
- Trips with at least one return: 4
- Total leave-and-return events: 6
- Absence duration percentiles (s), n=6: p5=31.0, p25=31.0, p50=31.5, p75=32.8, p95=41.2
- Stops dropped while absent, percentiles, n=6: p5=0.0, p25=0.0, p50=0.0, p75=0.8, p95=1.0

## Check 6: held values without the marker

Held values without `uncertainty = 0`: 494 / 17183 (2.9%)

By stop position and how the stop left the feed:
- first, clean drop: 1 / 494 (0.2%)
- first, trip removal: 0 / 494 (0.0%)
- intermediate, clean drop: 12 / 494 (2.4%)
- intermediate, trip removal: 5 / 494 (1.0%)
- final, clean drop: 0 / 494 (0.0%)
- final, trip removal: 476 / 494 (96.4%)

## V1-V4: VehiclePositions

Not run for 2026-09-06. Run with --vehicle-positions (2026-09-07 only) to include V1-V4.

## Observations

- Drift (held value minus value at t_cross) median is 11.0s overall, tight relative to the ~600s gap between predicted time and drop time found in observed_time's report - the value itself settles quickly even though the feed is slow to remove the entry.
- Arrival uncertainty, when present, is always exactly 0 in this sample - never non-zero.
- Departure uncertainty, when present, is always exactly 0 in this sample - never non-zero.
- 59 of 59 unmatched trips match the previous day's (2026-09-05) static schedule instead.
