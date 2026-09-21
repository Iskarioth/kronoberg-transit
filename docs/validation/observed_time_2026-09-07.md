# Observed-time validation scan: 2026-09-07

Evidence for D-005/D-006 (see `docs/decisions.md`). This report never changes a definition; it only measures how the `krono` TripUpdates feed actually behaves.

## Header

- Service date: 2026-09-07
- Operator/feed: krono/TripUpdates
- Hours present: 24/24 (missing: none)
- Snapshot count (deduplicated): 4143
- First snapshot: 2026-09-07T02:39:03+00:00 UTC / 2026-09-07T04:39:03+02:00 Europe/Stockholm
- Last snapshot: 2026-09-07T21:59:39+00:00 UTC / 2026-09-07T23:59:39+02:00 Europe/Stockholm
- Git commit: a883f1b58853236833a8a10fd8e0b5cca6dd8015
- Run timestamp (UTC): 2026-09-21T18:12:11Z

## 1. Snapshot cadence

- Gaps measured: 4142 (between 4143 deduplicated snapshots)
- p50=16.0s, p95=21.0s, p99=32.0s, max=39s
- Gaps > 30s: 161 / 4142 (3.9%)
- Gaps > 60s: 0 / 4142 (0.0%)
- Gaps > 300s: 0 / 4142 (0.0%)
- Duplicate snapshot files (same header_timestamp as another file): 716
- 20 largest gaps (start -> end, gap_s):
  - start 2026-09-07T20:58:27+00:00 UTC / 2026-09-07T22:58:27+02:00 Europe/Stockholm
    end   2026-09-07T20:59:06+00:00 UTC / 2026-09-07T22:59:06+02:00 Europe/Stockholm: 39s
  - start 2026-09-07T16:14:34+00:00 UTC / 2026-09-07T18:14:34+02:00 Europe/Stockholm
    end   2026-09-07T16:15:10+00:00 UTC / 2026-09-07T18:15:10+02:00 Europe/Stockholm: 36s
  - start 2026-09-07T16:46:26+00:00 UTC / 2026-09-07T18:46:26+02:00 Europe/Stockholm
    end   2026-09-07T16:47:01+00:00 UTC / 2026-09-07T18:47:01+02:00 Europe/Stockholm: 35s
  - start 2026-09-07T18:48:29+00:00 UTC / 2026-09-07T20:48:29+02:00 Europe/Stockholm
    end   2026-09-07T18:49:04+00:00 UTC / 2026-09-07T20:49:04+02:00 Europe/Stockholm: 35s
  - start 2026-09-07T15:53:31+00:00 UTC / 2026-09-07T17:53:31+02:00 Europe/Stockholm
    end   2026-09-07T15:54:05+00:00 UTC / 2026-09-07T17:54:05+02:00 Europe/Stockholm: 34s
  - start 2026-09-07T16:05:03+00:00 UTC / 2026-09-07T18:05:03+02:00 Europe/Stockholm
    end   2026-09-07T16:05:37+00:00 UTC / 2026-09-07T18:05:37+02:00 Europe/Stockholm: 34s
  - start 2026-09-07T16:49:29+00:00 UTC / 2026-09-07T18:49:29+02:00 Europe/Stockholm
    end   2026-09-07T16:50:03+00:00 UTC / 2026-09-07T18:50:03+02:00 Europe/Stockholm: 34s
  - start 2026-09-07T20:23:29+00:00 UTC / 2026-09-07T22:23:29+02:00 Europe/Stockholm
    end   2026-09-07T20:24:03+00:00 UTC / 2026-09-07T22:24:03+02:00 Europe/Stockholm: 34s
  - start 2026-09-07T21:15:33+00:00 UTC / 2026-09-07T23:15:33+02:00 Europe/Stockholm
    end   2026-09-07T21:16:07+00:00 UTC / 2026-09-07T23:16:07+02:00 Europe/Stockholm: 34s
  - start 2026-09-07T21:25:55+00:00 UTC / 2026-09-07T23:25:55+02:00 Europe/Stockholm
    end   2026-09-07T21:26:29+00:00 UTC / 2026-09-07T23:26:29+02:00 Europe/Stockholm: 34s
  - start 2026-09-07T21:47:35+00:00 UTC / 2026-09-07T23:47:35+02:00 Europe/Stockholm
    end   2026-09-07T21:48:09+00:00 UTC / 2026-09-07T23:48:09+02:00 Europe/Stockholm: 34s
  - start 2026-09-07T07:38:00+00:00 UTC / 2026-09-07T09:38:00+02:00 Europe/Stockholm
    end   2026-09-07T07:38:33+00:00 UTC / 2026-09-07T09:38:33+02:00 Europe/Stockholm: 33s
  - start 2026-09-07T07:46:30+00:00 UTC / 2026-09-07T09:46:30+02:00 Europe/Stockholm
    end   2026-09-07T07:47:03+00:00 UTC / 2026-09-07T09:47:03+02:00 Europe/Stockholm: 33s
  - start 2026-09-07T08:01:22+00:00 UTC / 2026-09-07T10:01:22+02:00 Europe/Stockholm
    end   2026-09-07T08:01:55+00:00 UTC / 2026-09-07T10:01:55+02:00 Europe/Stockholm: 33s
  - start 2026-09-07T09:10:30+00:00 UTC / 2026-09-07T11:10:30+02:00 Europe/Stockholm
    end   2026-09-07T09:11:03+00:00 UTC / 2026-09-07T11:11:03+02:00 Europe/Stockholm: 33s
  - start 2026-09-07T11:11:17+00:00 UTC / 2026-09-07T13:11:17+02:00 Europe/Stockholm
    end   2026-09-07T11:11:50+00:00 UTC / 2026-09-07T13:11:50+02:00 Europe/Stockholm: 33s
  - start 2026-09-07T13:01:34+00:00 UTC / 2026-09-07T15:01:34+02:00 Europe/Stockholm
    end   2026-09-07T13:02:07+00:00 UTC / 2026-09-07T15:02:07+02:00 Europe/Stockholm: 33s
  - start 2026-09-07T16:11:37+00:00 UTC / 2026-09-07T18:11:37+02:00 Europe/Stockholm
    end   2026-09-07T16:12:10+00:00 UTC / 2026-09-07T18:12:10+02:00 Europe/Stockholm: 33s
  - start 2026-09-07T16:40:33+00:00 UTC / 2026-09-07T18:40:33+02:00 Europe/Stockholm
    end   2026-09-07T16:41:06+00:00 UTC / 2026-09-07T18:41:06+02:00 Europe/Stockholm: 33s
  - start 2026-09-07T17:38:38+00:00 UTC / 2026-09-07T19:38:38+02:00 Europe/Stockholm
    end   2026-09-07T17:39:11+00:00 UTC / 2026-09-07T19:39:11+02:00 Europe/Stockholm: 33s

## 2. Field population

Over 5082642 stop_time_update rows (deduplicated snapshots):

- arrival_time_present: 5080152 / 5082642 (100.0%)
- arrival_delay_present: 5080152 / 5082642 (100.0%)
- arrival_uncertainty_present: 1403863 / 5082642 (27.6%)
- departure_time_present: 5080148 / 5082642 (100.0%)
- departure_delay_present: 5080148 / 5082642 (100.0%)
- departure_uncertainty_present: 1371366 / 5082642 (27.0%)
- stop_id_present: 5082642 / 5082642 (100.0%)
- stop_sequence_present: 5082642 / 5082642 (100.0%)

Stop-level schedule_relationship distribution:
- SCHEDULED: 5080152 / 5082642 (100.0%)
- SKIPPED: 2490 / 5082642 (0.0%)

## 3. Trip level

Trip-level schedule_relationship, counted per (snapshot, trip) observation:
- SCHEDULED: 260878 / 260960 (100.0%)
- CANCELED: 82 / 260960 (0.0%)
- ADDED: 0 / 260960 (0.0%)

- Static trips scheduled for 2026-09-07: 2162
- Realtime trips matched to static: 2040 / 2040 (100.0%)
- Realtime trips NOT matched to static: 0 / 2040 (0.0%)
- Static trips never seen in the feed: 122 / 2162 (5.6%)

## 4. How trips leave the feed

Among 2036 matched, uncensored trips:
- Left with only the final static stop remaining: 21 / 2036 (1.0%)
- Left with 2+ stops remaining: 2015 / 2036 (99.0%)
  - Stops-remaining distribution: min=2, p25=6.0, p50=8.0, p75=9.0, p95=12.0, max=28
- Other pattern (0 stops, or 1 stop that is not the final stop): 0 / 2036 (0.0%)

## 5. Stop drops

Among 30904 stop-drop events (matched, uncensored trips):
- Clean drops (front of list, trip stays in feed): 30164 / 30904 (97.6%)
- Drops not from the front: 11 / 30904 (0.0%)
- Intervals with 2+ stops dropped at once: 729 / 30904 (2.4%)
- Window width (first-absent minus last-present, seconds): p50=16.0, p95=31.0, p99=32.0, max=1196

**Stops that dropped more than once:**

- Trips affected: 1
- Distinct stop events affected: 8
- Repeat drops (occurrences beyond the first): 8
- Reappearance events (one snapshot, one or more stops): 1
- Stops reappearing per event (n=1): p25=8.0, p50=8.0, p75=8.0, p95=8.0

**Reappearance characterisation:**

- Reappearance snapshot identical to an earlier snapshot of the same trip (same stop_sequences, same times, same uncertainty): 0 / 1 (0.0%)
- Trip-level TripUpdate.timestamp populated at the reappearance snapshot: 1 / 1 (100.0%)
- Of those with the timestamp also populated at the trip's prior snapshot (n=1), it goes backwards: 0 / 1 (0.0%)

## 6. Retained stale values

Among 5080152 stop_time_updates with a populated arrival.time or departure.time, still present with a predicted time before the snapshot's own header_timestamp by more than:
- 60s: 1273263 / 5080152 (25.1%)
- 120s: 1116397 / 5080152 (22.0%)
- 300s: 678771 / 5080152 (13.4%)

## 7. Prediction vs drop time

**Predicted departure (departure-first fallback)** (30126 / 30164 (99.9%) resolved)

- Prediction source used: {'departure.time': 30126, 'unavailable': 38}
- Offset percentiles (seconds, predicted minus window midpoint): p1=-772.4, p5=-669.0, p25=-606.0, p50=-601.0, p75=-596.0, p95=-592.0, p99=-589.5
- Share with |offset| <= 15s: 0.0% (of 30126 resolved)
- Share with |offset| <= 30s: 0.0% (of 30126 resolved)
- Share with |offset| <= 60s: 0.0% (of 30126 resolved)
- Prediction falls before/inside/after the drop window: 30126 / 0 / 0 (of 30126)

**Predicted arrival (arrival-first fallback)** (30126 / 30164 (99.9%) resolved)

- Prediction source used: {'arrival.time': 30126, 'unavailable': 38}
- Offset percentiles (seconds, predicted minus window midpoint): p1=-840.4, p5=-731.5, p25=-624.5, p50=-606.0, p75=-599.0, p95=-593.0, p99=-591.5
- Share with |offset| <= 15s: 0.0% (of 30126 resolved)
- Share with |offset| <= 30s: 0.0% (of 30126 resolved)
- Share with |offset| <= 60s: 0.0% (of 30126 resolved)
- Prediction falls before/inside/after the drop window: 30126 / 0 / 0 (of 30126)

Excluding 16 clean drops of stops that dropped more than once, same statistics:

**Predicted departure, excluding repeat-dropped stops** (30110 / 30148 (99.9%) resolved)

- Prediction source used: {'departure.time': 30110, 'unavailable': 38}
- Offset percentiles (seconds, predicted minus window midpoint): p1=-772.5, p5=-669.0, p25=-606.0, p50=-601.0, p75=-596.0, p95=-592.0, p99=-589.5
- Share with |offset| <= 15s: 0.0% (of 30110 resolved)
- Share with |offset| <= 30s: 0.0% (of 30110 resolved)
- Share with |offset| <= 60s: 0.0% (of 30110 resolved)
- Prediction falls before/inside/after the drop window: 30110 / 0 / 0 (of 30110)

**Predicted arrival, excluding repeat-dropped stops** (30110 / 30148 (99.9%) resolved)

- Prediction source used: {'arrival.time': 30110, 'unavailable': 38}
- Offset percentiles (seconds, predicted minus window midpoint): p1=-840.5, p5=-731.5, p25=-624.5, p50=-606.0, p75=-599.0, p95=-593.0, p99=-591.5
- Share with |offset| <= 15s: 0.0% (of 30110 resolved)
- Share with |offset| <= 30s: 0.0% (of 30110 resolved)
- Share with |offset| <= 60s: 0.0% (of 30110 resolved)
- Prediction falls before/inside/after the drop window: 30110 / 0 / 0 (of 30110)

**Final-stop arrival, trips that left with only the final stop (21 events)** (21 / 21 (100.0%) resolved)

- Prediction source used: {'arrival.time': 21}
- Offset percentiles (seconds, predicted minus window midpoint): p1=-1659.3, p5=-1380.5, p25=-883.0, p50=-597.0, p75=-55.5, p95=611.0, p99=819.4
- Share with |offset| <= 15s: 0.0% (of 21 resolved)
- Share with |offset| <= 30s: 14.3% (of 21 resolved)
- Share with |offset| <= 60s: 19.0% (of 21 resolved)
- Prediction falls before/inside/after the drop window: 19 / 0 / 2 (of 21)

## 8. First stops

Among 2039 matched trips (excluding censored_start):
- Static first stop ever appears in the feed: 2034 / 2039 (99.8%)
- First stop_sequence position seen (n=2039): p25=1.0, p50=1.0, p75=1.0, p95=1.0
- First appearance vs scheduled first departure, minutes (n=2039): p5=-1.0, p25=-0.9, p50=-0.8, p75=-0.3, p95=5.3

## 9. Route-type split

Not applicable: only one route_type (700) observed among matched trips.

## 10. Observations

- 716 snapshot files share a header_timestamp with another file in the same day (deduplicated to one canonical file per timestamp).
- 2490 / 5082642 (0.0%) stop_time_updates carry schedule_relationship=SKIPPED. D-005's description states the feed 'never marks it SKIPPED' - this contradicts that.
- 82 (snapshot, trip) observations carry trip-level schedule_relationship=CANCELED, relevant to the Cancelled trips definition's dependence on this field being emitted.
- 21 / 2036 (1.0%) of matched, uncensored trips left the feed with only their final static stop remaining; 2015 / 2036 (99.0%) left with 2+ stops still listed.
- 11 drops were not from the front of the list and 729 intervals dropped 2+ stops at once, out of 30904 total.
- 8 stop events across 1 trip(s) dropped more than once (8 repeat drops total), in 1 reappearance event(s); 0 / 1 (0.0%) reproduce an earlier snapshot of the same trip exactly.
- 1273263 / 5080152 (25.1%) of stop_time_updates with a populated time field are more than 60s stale relative to the snapshot's own header_timestamp.
- Across 30126 resolved clean drops, predicted departure.time falls before the drop window in 30126 of 30126 cases (offset percentiles: p1=-772.4, p5=-669.0, p25=-606.0, p50=-601.0, p75=-596.0, p95=-592.0, p99=-589.5), and this holds even for trips running close to on-time - the predicted departure/arrival time is not close to when the stop actually leaves the feed.
- For the 21 trips that left with only their final stop remaining, the predicted-arrival-vs-drop offset is far more spread out and not consistently one-sided (offset percentiles: p1=-1659.3, p5=-1380.5, p25=-883.0, p50=-597.0, p75=-55.5, p95=611.0, p99=819.4; before/inside/after: 19/0/2), unlike the tight, consistently-before pattern for mid-route clean drops.
