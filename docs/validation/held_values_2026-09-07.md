# Held-value validation scan: 2026-09-07

Evidence for D-005/D-006 (see `docs/decisions.md`). This report never changes a definition. It tests whether D-005's held value is a recorded actual time or a stale prediction, using TripUpdates alone and, where run, an independent VehiclePositions passage check.

## Header

- Service date: 2026-09-07
- Feeds: TripUpdates, VehiclePositions
- Hours present (TripUpdates): 24/24 (missing: none)
- Snapshot count (deduplicated): 4143
- First snapshot: 2026-09-07T02:39:03+00:00 UTC / 2026-09-07T04:39:03+02:00 Europe/Stockholm
- Last snapshot: 2026-09-07T21:59:39+00:00 UTC / 2026-09-07T23:59:39+02:00 Europe/Stockholm
- Git commit: bac4a7c3ad893a66d5a32c349ce026a99cb7a626
- Run timestamp (UTC): 2026-09-21T19:19:53Z

## Exclusions

- CANCELED trip (snapshot, trip) observations excluded: 82
- SKIPPED stop_time_updates excluded: 2490

## Check 1: frozen or drifting

Among 46443 (trip, stop) pairs with a held value:
- Reach t_cross: 45968 / 46443 (99.0%)
- Zero changes after t_cross: 13559 / 45968 (29.5%)
- Drift (held value minus value at t_cross): n=45968
  - percentiles (s): p1=-3.0, p5=0.0, p25=0.0, p50=13.0, p75=30.0, p95=69.0, p99=158.0
  - <=±15s: 54.4%, <=±30s: 76.1%, <=±60s: 93.4%
- t_last_change minus t_cross (s), n=45968: p5=0.0, p25=0.0, p50=16.0, p75=35.0, p95=80.0
- held value minus t_last_change (s), n=46443: p5=-29.0, p25=-20.0, p50=-15.0, p75=-10.0, p95=13.0

By how the stop left:
- Mid-route drop: n=30890, reach t_cross=30845 / 30890 (99.9%), drift median=13.0s
- Trip removal: n=15553, reach t_cross=15123 / 15553 (97.2%), drift median=12.0s

By whether uncertainty is present on the held value:
- Present: n=44790, drift median=13.0s, drift p95=69.0s
- Absent: n=1653, drift median=0.0s, drift p95=49.29999999999973s

## Check 2: uncertainty

**Arrival** (n=5067528):
- Cross-tab (uncertainty bucket, time-relative-to-now bucket, count): [('absent', '0-60s_future', 180510), ('absent', '0-60s_past', 62360), ('absent', '>60s_future', 3416939), ('absent', '>60s_past', 6589), ('present_zero', '0-60s_past', 136904), ('present_zero', '>60s_past', 1264226)]
- Non-zero uncertainty values: none observed
- Stops where uncertainty ever appears: 44748; appears then later disappears: 146
- (now minus time) at first appearance of uncertainty (s), n=44895: p5=4.0, p25=9.0, p50=15.0, p75=22.0, p95=39.0

**Departure** (n=5067524):
- Cross-tab (uncertainty bucket, time-relative-to-now bucket, count): [('absent', '0-60s_future', 183406), ('absent', '0-60s_past', 73393), ('absent', '>60s_future', 3435229), ('absent', '>60s_past', 6723), ('present_zero', '0-60s_past', 139927), ('present_zero', '>60s_past', 1228846)]
- Non-zero uncertainty values: none observed
- Stops where uncertainty ever appears: 44363; appears then later disappears: 146
- (now minus time) at first appearance of uncertainty (s), n=44510: p5=6.0, p25=11.0, p50=15.0, p75=20.0, p95=30.0

## Check 3: trip removal

Among 2034 trip removals:
- Completed (all remaining stops in the past): 1585 / 2034 (77.9%)
- Left early (at least one remaining stop in the future): 449 / 2034 (22.1%)
- Future-stops-remaining percentiles for left-early trips (n=449): p25=1.0, p50=1.0, p75=1.0, p95=1.0
- Left-early trips where the final stop is among the future stops: 448 / 449 (99.8%)
- Removal time minus final stop's held arrival.time, completed trips with final stop present (n=1584 of 1585 completed): p5=1.0, p25=5.0, p50=10.0, p75=20.0, p95=44.0

## Check 4: time vs delay

**Arrival** ((time - delay) minus scheduled time in UTC, n=5067528):
- Exactly 0: 5067528 / 5067528 (100.0%)
- Within ±60s: 5067528 / 5067528 (100.0%)
- Percentiles (s): p1=0.0, p5=0.0, p25=0.0, p50=0.0, p75=0.0, p95=0.0, p99=0.0

**Departure** ((time - delay) minus scheduled time in UTC, n=5067524):
- Exactly 0: 5067524 / 5067524 (100.0%)
- Within ±60s: 5067524 / 5067524 (100.0%)
- Percentiles (s): p1=0.0, p5=0.0, p25=0.0, p50=0.0, p75=0.0, p95=0.0, p99=0.0

## Check 5: loose ends

**5a. Per-hour table** (hour, archive_files, distinct_header_timestamps, first_ts, last_ts, trip_entities):
- 04: files=89, distinct_ts=79, first=2026-09-07T02:39:03+00:00 UTC / 2026-09-07T04:39:03+02:00 Europe/Stockholm, last=2026-09-07T02:59:54+00:00 UTC / 2026-09-07T04:59:54+02:00 Europe/Stockholm, trip_entities=7
- 05: files=250, distinct_ts=216, first=2026-09-07T02:59:54+00:00 UTC / 2026-09-07T04:59:54+02:00 Europe/Stockholm, last=2026-09-07T03:59:43+00:00 UTC / 2026-09-07T05:59:43+02:00 Europe/Stockholm, trip_entities=84
- 06: files=250, distinct_ts=223, first=2026-09-07T03:59:58+00:00 UTC / 2026-09-07T05:59:58+02:00 Europe/Stockholm, last=2026-09-07T04:59:47+00:00 UTC / 2026-09-07T06:59:47+02:00 Europe/Stockholm, trip_entities=252
- 07: files=252, distinct_ts=219, first=2026-09-07T05:00:03+00:00 UTC / 2026-09-07T07:00:03+02:00 Europe/Stockholm, last=2026-09-07T05:59:40+00:00 UTC / 2026-09-07T07:59:40+02:00 Europe/Stockholm, trip_entities=300
- 08: files=250, distinct_ts=217, first=2026-09-07T05:59:56+00:00 UTC / 2026-09-07T07:59:56+02:00 Europe/Stockholm, last=2026-09-07T06:59:42+00:00 UTC / 2026-09-07T08:59:42+02:00 Europe/Stockholm, trip_entities=248
- 09: files=249, distinct_ts=217, first=2026-09-07T06:59:59+00:00 UTC / 2026-09-07T08:59:59+02:00 Europe/Stockholm, last=2026-09-07T07:59:34+00:00 UTC / 2026-09-07T09:59:34+02:00 Europe/Stockholm, trip_entities=170
- 10: files=250, distinct_ts=219, first=2026-09-07T07:59:49+00:00 UTC / 2026-09-07T09:59:49+02:00 Europe/Stockholm, last=2026-09-07T08:59:45+00:00 UTC / 2026-09-07T10:59:45+02:00 Europe/Stockholm, trip_entities=144
- 11: files=253, distinct_ts=223, first=2026-09-07T08:59:45+00:00 UTC / 2026-09-07T10:59:45+02:00 Europe/Stockholm, last=2026-09-07T09:59:48+00:00 UTC / 2026-09-07T11:59:48+02:00 Europe/Stockholm, trip_entities=138
- 12: files=250, distinct_ts=217, first=2026-09-07T10:00:04+00:00 UTC / 2026-09-07T12:00:04+02:00 Europe/Stockholm, last=2026-09-07T10:59:39+00:00 UTC / 2026-09-07T12:59:39+02:00 Europe/Stockholm, trip_entities=153
- 13: files=251, distinct_ts=216, first=2026-09-07T11:00:10+00:00 UTC / 2026-09-07T13:00:10+02:00 Europe/Stockholm, last=2026-09-07T11:59:44+00:00 UTC / 2026-09-07T13:59:44+02:00 Europe/Stockholm, trip_entities=153
- 14: files=255, distinct_ts=219, first=2026-09-07T12:00:00+00:00 UTC / 2026-09-07T14:00:00+02:00 Europe/Stockholm, last=2026-09-07T12:59:42+00:00 UTC / 2026-09-07T14:59:42+02:00 Europe/Stockholm, trip_entities=201
- 15: files=251, distinct_ts=214, first=2026-09-07T12:59:57+00:00 UTC / 2026-09-07T14:59:57+02:00 Europe/Stockholm, last=2026-09-07T13:59:33+00:00 UTC / 2026-09-07T15:59:33+02:00 Europe/Stockholm, trip_entities=271
- 16: files=255, distinct_ts=216, first=2026-09-07T14:00:04+00:00 UTC / 2026-09-07T16:00:04+02:00 Europe/Stockholm, last=2026-09-07T14:59:39+00:00 UTC / 2026-09-07T16:59:39+02:00 Europe/Stockholm, trip_entities=317
- 17: files=250, distinct_ts=215, first=2026-09-07T14:59:55+00:00 UTC / 2026-09-07T16:59:55+02:00 Europe/Stockholm, last=2026-09-07T15:59:35+00:00 UTC / 2026-09-07T17:59:35+02:00 Europe/Stockholm, trip_entities=274
- 18: files=250, distinct_ts=210, first=2026-09-07T15:59:52+00:00 UTC / 2026-09-07T17:59:52+02:00 Europe/Stockholm, last=2026-09-07T16:59:29+00:00 UTC / 2026-09-07T18:59:29+02:00 Europe/Stockholm, trip_entities=174
- 19: files=251, distinct_ts=209, first=2026-09-07T16:59:46+00:00 UTC / 2026-09-07T18:59:46+02:00 Europe/Stockholm, last=2026-09-07T17:59:41+00:00 UTC / 2026-09-07T19:59:41+02:00 Europe/Stockholm, trip_entities=110
- 20: files=249, distinct_ts=213, first=2026-09-07T17:59:57+00:00 UTC / 2026-09-07T19:59:57+02:00 Europe/Stockholm, last=2026-09-07T18:59:41+00:00 UTC / 2026-09-07T20:59:41+02:00 Europe/Stockholm, trip_entities=100
- 21: files=253, distinct_ts=207, first=2026-09-07T18:59:59+00:00 UTC / 2026-09-07T20:59:59+02:00 Europe/Stockholm, last=2026-09-07T19:59:44+00:00 UTC / 2026-09-07T21:59:44+02:00 Europe/Stockholm, trip_entities=71
- 22: files=250, distinct_ts=197, first=2026-09-07T20:00:00+00:00 UTC / 2026-09-07T22:00:00+02:00 Europe/Stockholm, last=2026-09-07T20:59:37+00:00 UTC / 2026-09-07T22:59:37+02:00 Europe/Stockholm, trip_entities=55
- 23: files=251, distinct_ts=199, first=2026-09-07T20:59:54+00:00 UTC / 2026-09-07T22:59:54+02:00 Europe/Stockholm, last=2026-09-07T21:59:39+00:00 UTC / 2026-09-07T23:59:39+02:00 Europe/Stockholm, trip_entities=24

**5b. Unmatched trips' start_date distribution:** not applicable (only run for 2026-09-06)

**5c. Snapshot gaps over 300s** (0 found):

**5d. Trips that leave the feed and come back:**
- Trips with at least one return: 38
- Total leave-and-return events: 45
- Absence duration percentiles (s), n=45: p5=31.0, p25=32.0, p50=33.0, p75=48.0, p95=80.6
- Stops dropped while absent, percentiles, n=45: p5=0.0, p25=0.0, p50=0.0, p75=0.0, p95=1.0

## Check 6: held values without the marker

Held values without `uncertainty = 0`: 1653 / 46443 (3.6%)

By stop position and how the stop left the feed:
- first, clean drop: 19 / 1653 (1.1%)
- first, trip removal: 1 / 1653 (0.1%)
- intermediate, clean drop: 151 / 1653 (9.1%)
- intermediate, trip removal: 76 / 1653 (4.6%)
- final, clean drop: 1 / 1653 (0.1%)
- final, trip removal: 1405 / 1653 (85.0%)

## Stops that reappeared after dropping

- Count (resolvable against both the pre-drop snapshot and the final appearance): 9 (of 9 reappeared stops found)
- Share where the time differs (last snapshot before the first drop vs. final appearance): 9 / 9 (100.0%)
- Difference (final minus before), seconds, n=9: p5=530.8, p25=1108.0, p50=1206.0, p75=1226.0, p95=1236.8
- Marker status, before the first drop -> final appearance (count):
  - absent -> present: 9

**Per-stop V3 offset against VP (R = 50m)**, before the first drop vs. the final appearance:

| trip_id | stop_sequence | offset before (s) | offset final (s) |
|---|---|---|---|
| 76110000043895645 | 2 | -153.0 | -3.0 |
| 76110000043920067 | 11 | -1106.0 | -4.0 |
| 76110000043920067 | 12 | -1111.5 | -3.5 |
| 76110000043920067 | 13 | -1113.0 | -3.0 |
| 76110000043920067 | 14 | -1230.0 | -9.0 |
| 76110000043920067 | 15 | -1238.0 | -9.0 |
| 76110000043920067 | 16 | -1249.0 | -7.0 |
| 76110000043920067 | 17 | -1230.0 | -4.0 |
| 76110000043920067 | 18 | -1208.0 | -2.0 |

## VehiclePositions reconciliation (R = 50m)

Funnel from held values to V4 events; no stage exceeds the one before it, and V3 and V4 counts must match:
- Held values: 46443
- Eligible stop events: 46443
- Detected in V2: 45950
- V3 events: 45940
- V4 events: 45940

## V1: field population

Matching deviates from a literal (trip_id, start_date) match: VehiclePositions never carries start_date, and most entities lack a trip descriptor (see field counts and the per-hour breakdown below), so matching is trip_id-only plus a scheduled-time window (first departure - 30 min to final arrival + 90 min, from the static schedule, not TripUpdates). Confirmed with Marcus; see Observations.

Among 14677481 raw VehiclePosition entities:
- trip_id: 4128137 / 14677481 (28.1%)
- start_date: 0 / 14677481 (0.0%)
- vehicle_timestamp: 14677481 / 14677481 (100.0%)
- position: 14677481 / 14677481 (100.0%)
- current_status: 0 / 14677481 (0.0%)
- current_stop_sequence: 0 / 14677481 (0.0%)
- stop_id: 0 / 14677481 (0.0%)
- Ping time source used: vehicle.timestamp 14677481 / 14677481 (100.0%), header_timestamp fallback 0 / 14677481 (0.0%)
- vehicle.timestamp minus header_timestamp (s), n=14677481: p5=-57.0, p25=-37.0, p50=-13.0, p75=-1.0, p95=-1.0
- Distinct trip_ids seen in VP: 2040
- ...matched to static schedule: 2040 / 2040 (100.0%)
- ...matched to TripUpdates matched trips: 2036 / 2040 (99.8%)

**Per-hour trip_id population:**
- 00: 0 / 610211 (0.0%)
- 01: 0 / 596003 (0.0%)
- 02: 0 / 606810 (0.0%)
- 03: 0 / 607857 (0.0%)
- 04: 2892 / 601555 (0.5%)
- 05: 93771 / 601380 (15.6%)
- 06: 326554 / 608940 (53.6%)
- 07: 441914 / 612210 (72.2%)
- 08: 290312 / 612180 (47.4%)
- 09: 212536 / 614670 (34.6%)
- 10: 155358 / 612548 (25.4%)
- 11: 166005 / 613424 (27.1%)
- 12: 185267 / 610020 (30.4%)
- 13: 198586 / 616718 (32.2%)
- 14: 258095 / 616368 (41.9%)
- 15: 352498 / 610767 (57.7%)
- 16: 434337 / 621294 (69.9%)
- 17: 356433 / 614543 (58.0%)
- 18: 212846 / 619268 (34.4%)
- 19: 131639 / 613260 (21.5%)
- 20: 121505 / 612900 (19.8%)
- 21: 89238 / 612000 (14.6%)
- 22: 73507 / 615060 (12.0%)
- 23: 24844 / 617495 (4.0%)

**Per-trip summary** (n=2036 trips with pings):
- Raw entities per trip: p25=1312.8, p50=1852.0, p75=2488.0, p95=4185.5
- Distinct pings per trip (after dedup by vehicle_id+vehicle_timestamp): p25=663.0, p50=941.0, p75=1254.2, p95=2082.2
- Ping time span / scheduled trip duration ratio, n=2036: p5=0.8, p25=0.9, p50=1.0, p75=1.1, p95=1.2

**Assignment dropouts** (trip-less pings from a vehicle, inside one of its trips' scheduled window):
- Dropout pings: 73111
- Trips with at least one dropout ping: 1091
- Dropout share per trip, n=2036: p25=0.0, p50=0.0, p75=0.1, p95=0.2

**Guardrail check (run before proceeding):** median inter-ping interval = 2.0s (stop threshold: >60s); share of trip-linked pings outside the scheduled window = 0.00% (stop threshold: >10%). Neither triggered.

## V2: passage detection

**R = 25m:**
- Events detected: 44916 / 46443 (96.7%)
- No ping within R: 1527 / 46443 (3.3%)
- Multiple visits: 1359 / 46443 (2.9%)
- Departure window width (s), n=43930: p25=2.0, p50=2.0, p75=2.0, p95=3.0
- Arrival window width (final stops only, s), n=1782: p25=2.0, p50=2.0, p75=2.0, p95=3.0

**R = 50m:**
- Events detected: 45950 / 46443 (98.9%)
- No ping within R: 493 / 46443 (1.1%)
- Multiple visits: 1146 / 46443 (2.5%)
- Departure window width (s), n=44415: p25=2.0, p50=2.0, p75=2.0, p95=3.0
- Arrival window width (final stops only, s), n=1902: p25=2.0, p50=2.0, p75=2.0, p95=4.0

**R = 100m:**
- Events detected: 46184 / 46443 (99.4%)
- No ping within R: 259 / 46443 (0.6%)
- Multiple visits: 1136 / 46443 (2.4%)
- Departure window width (s), n=44245: p25=2.0, p50=2.0, p75=2.0, p95=3.0
- Arrival window width (final stops only, s), n=1994: p25=2.0, p50=2.0, p75=2.0, p95=4.0

## V3: held value vs VP

**Compact table, R = 25m and R = 100m (overall offset only):**
- R=25m: n=44911
  - percentiles (s): p1=-40.0, p5=-11.0, p25=-6.0, p50=-4.0, p75=-2.0, p95=0.0, p99=18.5
  - <=±15s: 96.0%, <=±30s: 98.1%, <=±60s: 98.8%
- R=100m: n=46173
  - percentiles (s): p1=-84.6, p5=-35.0, p25=-17.0, p50=-12.0, p75=-6.5, p95=-3.0, p99=31.0
  - <=±15s: 67.0%, <=±30s: 91.9%, <=±60s: 97.8%

**R = 50m, full breakdown:**
- Overall: n=45940
  - percentiles (s): p1=-55.5, p5=-20.0, p25=-10.0, p50=-6.5, p75=-3.5, p95=-1.0, p99=18.0
  - <=±15s: 91.0%, <=±30s: 97.2%, <=±60s: 98.7%
By how the stop left:
- Mid-route drop: n=30606
  - percentiles (s): p1=-66.5, p5=-21.0, p25=-10.0, p50=-7.0, p75=-3.0, p95=-2.0, p99=-1.0
  - <=±15s: 90.8%, <=±30s: 97.1%, <=±60s: 98.6%
- Trip removal: n=15334
  - percentiles (s): p1=-39.0, p5=-17.0, p25=-9.0, p50=-6.0, p75=-4.0, p95=9.0, p99=32.8
  - <=±15s: 91.3%, <=±30s: 97.4%, <=±60s: 99.0%
By stop position:
- first: n=1981
  - percentiles (s): p1=-148.2, p5=-83.0, p25=-26.0, p50=-16.0, p75=-11.5, p95=-8.0, p99=-4.0
  - <=±15s: 46.1%, <=±30s: 83.1%, <=±60s: 92.7%
- intermediate: n=42057
  - percentiles (s): p1=-40.0, p5=-16.0, p25=-9.5, p50=-6.0, p75=-4.0, p95=-2.0, p99=-1.0
  - <=±15s: 94.2%, <=±30s: 98.2%, <=±60s: 99.1%
- final: n=1902
  - percentiles (s): p1=-40.0, p5=-23.0, p25=-7.0, p50=5.0, p75=13.0, p95=44.0, p99=92.0
  - <=±15s: 67.6%, <=±30s: 90.6%, <=±60s: 96.8%
By whether uncertainty is present on the held value:
- Present: n=44608
  - percentiles (s): p1=-55.0, p5=-19.0, p25=-10.0, p50=-6.5, p75=-4.0, p95=-2.0, p99=11.0
  - <=±15s: 91.8%, <=±30s: 97.5%, <=±60s: 98.8%
- Absent: n=1332
  - percentiles (s): p1=-57.7, p5=-28.0, p25=-12.0, p50=-1.5, p75=10.0, p95=53.2, p99=374.9
  - <=±15s: 62.6%, <=±30s: 86.8%, <=±60s: 95.0%
By Check 1 status:
- Zero changes after t_cross: n=13268
  - percentiles (s): p1=-105.0, p5=-21.0, p25=-7.0, p50=-5.0, p75=-3.0, p95=-1.0, p99=10.0
  - <=±15s: 92.7%, <=±30s: 96.6%, <=±60s: 97.8%
- Changed after t_cross: n=32335
  - percentiles (s): p1=-39.0, p5=-19.5, p25=-10.5, p50=-7.5, p75=-4.5, p95=-2.0, p99=13.8
  - <=±15s: 90.8%, <=±30s: 97.8%, <=±60s: 99.3%

## V4: classification agreement

Among 45940 events with both a held-value and a VP-based classification (R=50m):

**By stop position:**

**First** (n=1981):
- 3x3 matrix (held_class, vp_class): counts
  - (late, late): 192
  - (on_time, late): 51
  - (on_time, on_time): 1738
- On-time share by source and threshold:
  - +180s: held 1789 / 1981 (90.3%), vp 1738 / 1981 (87.7%)
  - +60s: held 1477 / 1981 (74.6%), vp 1286 / 1981 (64.9%)
  - +300s: held 1886 / 1981 (95.2%), vp 1863 / 1981 (94.0%)

**Intermediate** (n=42057):
- 3x3 matrix (held_class, vp_class): counts
  - (early, early): 2927
  - (early, late): 3
  - (early, on_time): 398
  - (late, early): 10
  - (late, late): 13236
  - (late, on_time): 39
  - (on_time, early): 7
  - (on_time, late): 605
  - (on_time, on_time): 24832
- On-time share by source and threshold:
  - +180s: held 25444 / 42057 (60.5%), vp 25269 / 42057 (60.1%)
  - +60s: held 12229 / 42057 (29.1%), vp 11625 / 42057 (27.6%)
  - +300s: held 32573 / 42057 (77.4%), vp 32680 / 42057 (77.7%)

**Final** (n=1902):
- 3x3 matrix (held_class, vp_class): counts
  - (early, early): 774
  - (early, late): 1
  - (early, on_time): 15
  - (late, early): 5
  - (late, late): 318
  - (late, on_time): 16
  - (on_time, early): 51
  - (on_time, late): 8
  - (on_time, on_time): 714
- On-time share by source and threshold:
  - +180s: held 773 / 1902 (40.6%), vp 745 / 1902 (39.2%)
  - +60s: held 479 / 1902 (25.2%), vp 478 / 1902 (25.1%)
  - +300s: held 929 / 1902 (48.8%), vp 899 / 1902 (47.3%)

**By marker presence on the held value:**

**All held values** (n=45940):
- 3x3 matrix (held_class, vp_class): counts
  - (early, early): 3701
  - (early, late): 4
  - (early, on_time): 413
  - (late, early): 15
  - (late, late): 13746
  - (late, on_time): 55
  - (on_time, early): 58
  - (on_time, late): 664
  - (on_time, on_time): 27284
- On-time share by source and threshold:
  - +180s: held 28006 / 45940 (61.0%), vp 27752 / 45940 (60.4%)
  - +60s: held 14185 / 45940 (30.9%), vp 13389 / 45940 (29.1%)
  - +300s: held 35388 / 45940 (77.0%), vp 35442 / 45940 (77.1%)

**Marker present (uncertainty = 0)** (n=44608):
- 3x3 matrix (held_class, vp_class): counts
  - (early, early): 3151
  - (early, late): 3
  - (early, on_time): 398
  - (late, early): 6
  - (late, late): 13513
  - (late, on_time): 45
  - (on_time, early): 32
  - (on_time, late): 651
  - (on_time, on_time): 26809
- On-time share by source and threshold:
  - +180s: held 27492 / 44608 (61.6%), vp 27252 / 44608 (61.1%)
  - +60s: held 13863 / 44608 (31.1%), vp 13064 / 44608 (29.3%)
  - +300s: held 34772 / 44608 (78.0%), vp 34841 / 44608 (78.1%)

## Observations

- Drift (held value minus value at t_cross) median is 13.0s overall, tight relative to the ~600s gap between predicted time and drop time found in observed_time's report - the value itself settles quickly even though the feed is slow to remove the entry.
- Arrival uncertainty, when present, is always exactly 0 in this sample - never non-zero.
- Departure uncertainty, when present, is always exactly 0 in this sample - never non-zero.
- Only 28.1% of raw VehiclePosition entities carry a trip_id (4128137 / 14677481 (28.1%)); start_date is never populated. Matching used trip_id plus a scheduled-time window instead of (trip_id, start_date), confirmed with Marcus before proceeding.
- Held-value-vs-VP offset is far tighter at intermediate stops (median -6.0s) than at first stops (median -16.0s) or final stops (median 5.0s, with a long tail).
- Held-value and VP-based punctuality classification agree on 44731 / 45940 (97.4%) of events (3-way early/on_time/late).
