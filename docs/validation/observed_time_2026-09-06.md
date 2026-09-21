# Observed-time validation scan: 2026-09-06

Evidence for D-005/D-006 (see `docs/decisions.md`). This report never changes a definition; it only measures how the `krono` TripUpdates feed actually behaves.

## Header

- Service date: 2026-09-06
- Operator/feed: krono/TripUpdates
- Hours present: 24/24 (missing: none)
- Snapshot count (deduplicated): 4541
- First snapshot: 2026-09-05T21:59:43+00:00 UTC / 2026-09-05T23:59:43+02:00 Europe/Stockholm
- Last snapshot: 2026-09-06T21:35:21+00:00 UTC / 2026-09-06T23:35:21+02:00 Europe/Stockholm
- Git commit: e3c5f50c19ff17e69f09d6c5a1175daa1c8a2ab5
- Run timestamp (UTC): 2026-09-21T09:46:44Z

## 1. Snapshot cadence

- Gaps measured: 4540 (between 4541 deduplicated snapshots)
- p50=16.0s, p95=24.0s, p99=32.0s, max=6876s
- Gaps > 30s: 148 / 4540 (3.3%)
- Gaps > 60s: 1 / 4540 (0.0%)
- Gaps > 300s: 1 / 4540 (0.0%)
- Duplicate snapshot files (same header_timestamp as another file): 913
- 20 largest gaps (start -> end, gap_s):
  - start 2026-09-06T01:34:47+00:00 UTC / 2026-09-06T03:34:47+02:00 Europe/Stockholm
    end   2026-09-06T03:29:23+00:00 UTC / 2026-09-06T05:29:23+02:00 Europe/Stockholm: 6876s
  - start 2026-09-06T16:00:37+00:00 UTC / 2026-09-06T18:00:37+02:00 Europe/Stockholm
    end   2026-09-06T16:01:27+00:00 UTC / 2026-09-06T18:01:27+02:00 Europe/Stockholm: 50s
  - start 2026-09-06T21:30:24+00:00 UTC / 2026-09-06T23:30:24+02:00 Europe/Stockholm
    end   2026-09-06T21:31:11+00:00 UTC / 2026-09-06T23:31:11+02:00 Europe/Stockholm: 47s
  - start 2026-09-05T22:38:25+00:00 UTC / 2026-09-06T00:38:25+02:00 Europe/Stockholm
    end   2026-09-05T22:39:11+00:00 UTC / 2026-09-06T00:39:11+02:00 Europe/Stockholm: 46s
  - start 2026-09-05T22:56:25+00:00 UTC / 2026-09-06T00:56:25+02:00 Europe/Stockholm
    end   2026-09-05T22:57:10+00:00 UTC / 2026-09-06T00:57:10+02:00 Europe/Stockholm: 45s
  - start 2026-09-05T22:17:26+00:00 UTC / 2026-09-06T00:17:26+02:00 Europe/Stockholm
    end   2026-09-05T22:18:09+00:00 UTC / 2026-09-06T00:18:09+02:00 Europe/Stockholm: 43s
  - start 2026-09-06T00:20:34+00:00 UTC / 2026-09-06T02:20:34+02:00 Europe/Stockholm
    end   2026-09-06T00:21:12+00:00 UTC / 2026-09-06T02:21:12+02:00 Europe/Stockholm: 38s
  - start 2026-09-05T22:45:31+00:00 UTC / 2026-09-06T00:45:31+02:00 Europe/Stockholm
    end   2026-09-05T22:46:08+00:00 UTC / 2026-09-06T00:46:08+02:00 Europe/Stockholm: 37s
  - start 2026-09-06T20:03:32+00:00 UTC / 2026-09-06T22:03:32+02:00 Europe/Stockholm
    end   2026-09-06T20:04:08+00:00 UTC / 2026-09-06T22:04:08+02:00 Europe/Stockholm: 36s
  - start 2026-09-06T20:31:36+00:00 UTC / 2026-09-06T22:31:36+02:00 Europe/Stockholm
    end   2026-09-06T20:32:12+00:00 UTC / 2026-09-06T22:32:12+02:00 Europe/Stockholm: 36s
  - start 2026-09-06T17:04:27+00:00 UTC / 2026-09-06T19:04:27+02:00 Europe/Stockholm
    end   2026-09-06T17:05:02+00:00 UTC / 2026-09-06T19:05:02+02:00 Europe/Stockholm: 35s
  - start 2026-09-06T15:44:25+00:00 UTC / 2026-09-06T17:44:25+02:00 Europe/Stockholm
    end   2026-09-06T15:44:59+00:00 UTC / 2026-09-06T17:44:59+02:00 Europe/Stockholm: 34s
  - start 2026-09-06T18:08:30+00:00 UTC / 2026-09-06T20:08:30+02:00 Europe/Stockholm
    end   2026-09-06T18:09:04+00:00 UTC / 2026-09-06T20:09:04+02:00 Europe/Stockholm: 34s
  - start 2026-09-06T19:37:43+00:00 UTC / 2026-09-06T21:37:43+02:00 Europe/Stockholm
    end   2026-09-06T19:38:17+00:00 UTC / 2026-09-06T21:38:17+02:00 Europe/Stockholm: 34s
  - start 2026-09-06T20:47:44+00:00 UTC / 2026-09-06T22:47:44+02:00 Europe/Stockholm
    end   2026-09-06T20:48:18+00:00 UTC / 2026-09-06T22:48:18+02:00 Europe/Stockholm: 34s
  - start 2026-09-05T22:21:29+00:00 UTC / 2026-09-06T00:21:29+02:00 Europe/Stockholm
    end   2026-09-05T22:22:02+00:00 UTC / 2026-09-06T00:22:02+02:00 Europe/Stockholm: 33s
  - start 2026-09-06T01:24:34+00:00 UTC / 2026-09-06T03:24:34+02:00 Europe/Stockholm
    end   2026-09-06T01:25:07+00:00 UTC / 2026-09-06T03:25:07+02:00 Europe/Stockholm: 33s
  - start 2026-09-06T04:07:34+00:00 UTC / 2026-09-06T06:07:34+02:00 Europe/Stockholm
    end   2026-09-06T04:08:07+00:00 UTC / 2026-09-06T06:08:07+02:00 Europe/Stockholm: 33s
  - start 2026-09-06T09:50:33+00:00 UTC / 2026-09-06T11:50:33+02:00 Europe/Stockholm
    end   2026-09-06T09:51:06+00:00 UTC / 2026-09-06T11:51:06+02:00 Europe/Stockholm: 33s
  - start 2026-09-06T11:10:55+00:00 UTC / 2026-09-06T13:10:55+02:00 Europe/Stockholm
    end   2026-09-06T11:11:28+00:00 UTC / 2026-09-06T13:11:28+02:00 Europe/Stockholm: 33s

## 2. Field population

Over 1978256 stop_time_update rows (deduplicated snapshots):

- arrival_time_present: 1978256 / 1978256 (100.0%)
- arrival_delay_present: 1978256 / 1978256 (100.0%)
- arrival_uncertainty_present: 561839 / 1978256 (28.4%)
- departure_time_present: 1978256 / 1978256 (100.0%)
- departure_delay_present: 1978256 / 1978256 (100.0%)
- departure_uncertainty_present: 548388 / 1978256 (27.7%)
- stop_id_present: 1978256 / 1978256 (100.0%)
- stop_sequence_present: 1978256 / 1978256 (100.0%)

Stop-level schedule_relationship distribution:
- SCHEDULED: 1978256 / 1978256 (100.0%)

## 3. Trip level

Trip-level schedule_relationship, counted per (snapshot, trip) observation:
- SCHEDULED: 92162 / 92162 (100.0%)
- CANCELED: 0 / 92162 (0.0%)
- ADDED: 0 / 92162 (0.0%)

- Static trips scheduled for 2026-09-06: 729
- Realtime trips matched to static: 701 / 760 (92.2%)
- Realtime trips NOT matched to static: 59 / 760 (7.8%)
- Static trips never seen in the feed: 28 / 729 (3.8%)

## 4. How trips leave the feed

Among 700 matched, uncensored trips:
- Left with only the final static stop remaining: 3 / 700 (0.4%)
- Left with 2+ stops remaining: 697 / 700 (99.6%)
  - Stops-remaining distribution: min=2, p25=7.0, p50=9.0, p75=10.0, p95=14.0, max=18
- Other pattern (0 stops, or 1 stop that is not the final stop): 0 / 700 (0.0%)

## 5. Stop drops

Among 10929 stop-drop events (matched, uncensored trips):
- Clean drops (front of list, trip stays in feed): 10625 / 10929 (97.2%)
- Drops not from the front: 6 / 10929 (0.1%)
- Intervals with 2+ stops dropped at once: 298 / 10929 (2.7%)
- Window width (first-absent minus last-present, seconds): p50=16.0, p95=31.0, p99=32.0, max=50

## 6. Retained stale values

Among 1978256 stop_time_updates with a populated arrival.time or departure.time, still present with a predicted time before the snapshot's own header_timestamp by more than:
- 60s: 507626 / 1978256 (25.7%)
- 120s: 444988 / 1978256 (22.5%)
- 300s: 271172 / 1978256 (13.7%)

## 7. Prediction vs drop time

**Predicted departure (departure-first fallback)** (10625 / 10625 (100.0%) resolved)

- Prediction source used: {'departure.time': 10625}
- Offset percentiles (seconds, predicted minus window midpoint): p1=-801.3, p5=-714.0, p25=-607.5, p50=-602.0, p75=-597.0, p95=-592.5, p99=-590.0
- Share with |offset| <= 15s: 0.0% (of 10625 resolved)
- Share with |offset| <= 30s: 0.0% (of 10625 resolved)
- Share with |offset| <= 60s: 0.0% (of 10625 resolved)
- Prediction falls before/inside/after the drop window: 10625 / 0 / 0 (of 10625)

**Predicted arrival (arrival-first fallback)** (10625 / 10625 (100.0%) resolved)

- Prediction source used: {'arrival.time': 10625}
- Offset percentiles (seconds, predicted minus window midpoint): p1=-872.8, p5=-756.0, p25=-637.0, p50=-607.0, p75=-599.5, p95=-593.0, p99=-591.5
- Share with |offset| <= 15s: 0.0% (of 10625 resolved)
- Share with |offset| <= 30s: 0.0% (of 10625 resolved)
- Share with |offset| <= 60s: 0.0% (of 10625 resolved)
- Prediction falls before/inside/after the drop window: 10625 / 0 / 0 (of 10625)

**Final-stop arrival, trips that left with only the final stop (3 events)** (3 / 3 (100.0%) resolved)

- Prediction source used: {'arrival.time': 3}
- Offset percentiles (seconds, predicted minus window midpoint): p1=-927.0, p5=-902.9, p25=-782.2, p50=-631.5, p75=-610.0, p95=-592.8, p99=-589.4
- Share with |offset| <= 15s: 0.0% (of 3 resolved)
- Share with |offset| <= 30s: 0.0% (of 3 resolved)
- Share with |offset| <= 60s: 0.0% (of 3 resolved)
- Prediction falls before/inside/after the drop window: 3 / 0 / 0 (of 3)

## 8. First stops

Among 701 matched trips (excluding censored_start):
- Static first stop ever appears in the feed: 701 / 701 (100.0%)
- First stop_sequence position seen (n=701): p25=1.0, p50=1.0, p75=1.0, p95=1.0
- First appearance vs scheduled first departure, minutes (n=701): p5=-1.0, p25=-0.9, p50=-0.8, p75=-0.8, p95=0.5

## 9. Route-type split

Not applicable: only one route_type (700) observed among matched trips.

## 10. Observations

- 913 snapshot files share a header_timestamp with another file in the same day (deduplicated to one canonical file per timestamp).
- 3 / 700 (0.4%) of matched, uncensored trips left the feed with only their final static stop remaining; 697 / 700 (99.6%) left with 2+ stops still listed.
- 6 drops were not from the front of the list and 298 intervals dropped 2+ stops at once, out of 10929 total.
- 507626 / 1978256 (25.7%) of stop_time_updates with a populated time field are more than 60s stale relative to the snapshot's own header_timestamp.
- Across 10625 resolved clean drops, predicted departure.time falls before the drop window in 10625 of 10625 cases (offset percentiles: p1=-801.3, p5=-714.0, p25=-607.5, p50=-602.0, p75=-597.0, p95=-592.5, p99=-590.0), and this holds even for trips running close to on-time - the predicted departure/arrival time is not close to when the stop actually leaves the feed.
- For the 3 trips that left with only their final stop remaining, the predicted-arrival-vs-drop offset is far more spread out and not consistently one-sided (offset percentiles: p1=-927.0, p5=-902.9, p25=-782.2, p50=-631.5, p75=-610.0, p95=-592.8, p99=-589.4; before/inside/after: 3/0/0), unlike the tight, consistently-before pattern for mid-route clean drops.
