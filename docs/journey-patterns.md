# Where delay builds, and which departures repeat the pattern

**September 2026 · exploratory study · 11 October 2026**

The September records support both questions: following recorded departure delay
through a journey, and comparing the same scheduled departure across dates.
Two examples show why these views are useful. A monthly route average can hide
both a recurring weak departure and recovery partway through its journey.

This is a frozen September analysis, not a statement about today's services.
The examples were selected after screening weekday departures starting from
15:00 to 17:59 by late-observation volume. They are investigation leads, not
estimates of the typical route or a claim to have found the network's worst services.

## Route 840: the 16:15 departure from Växjö towards Ronneby

This exact stop and running-time pattern operated on all 22 September weekdays.
There were 131 observed timing-stop departures out of 132 eligible: 99.24%
coverage. Only 26 of those observations were on time (19.85%). Every weekday
had at least one late timing-stop observation; this does **not** mean every
departure left Växjö late.

| Timing stop, in journey order | Observed / eligible | Median recorded departure delay | On time / observed |
|---|---:|---:|---:|
| Växjö station | 21 / 22 | +51 s | 13 / 21 |
| Kyrkan, Ingelstad | 22 / 22 | +375 s | 2 / 22 |
| Väckelsång centrum | 22 / 22 | +340 s | 1 / 22 |
| Tingsryd terminal | 22 / 22 | +233 s | 10 / 22 |
| Hallabro | 22 / 22 | +428 s | 0 / 22 |
| Backaryd v.27 | 22 / 22 | +594.5 s | 0 / 22 |

These are separate medians at each stop, not one observed journey. The paired
records confirm the shape: median change was +268 seconds between Växjö and
Ingelstad (21 observed pairs / 22 eligible), −167.5 seconds between Väckelsång
and Tingsryd (22 / 22), and +217.5 seconds between Tingsryd and Hallabro (22 / 22).
Do not subtract the stop medians to estimate these changes.

Nine of the 22 paired trips changed from late at Väckelsång to on time at
Tingsryd. Ten changed from on time at Tingsryd to late at Hallabro. Recovery at
one point therefore did not necessarily last through the remaining timing stops.
The terminal arrival at Ronneby is not measured here: final-stop departure
punctuality is excluded by the existing definition.

One actual journey illustrates this: on 30 September, recorded delay was
+12 seconds at Växjö, +237 at Ingelstad, +248 at Väckelsång, +77 at Tingsryd,
+352 at Hallabro and +582 at Backaryd. That is a gain, a recovery, and another
gain within the same service-date/trip pair. The reproduction exports individual
journeys, including missing observations, under the ignored `data/` directory.

### The scheduled departure time matters

The same direction, full stop sequence, timing-stop flags and scheduled
running-time pattern give a useful comparison across start times. All rows below
have 22 distinct observed trips over 22 weekdays; the percentages pool counts
across eligible timing stops rather than averaging daily percentages.

| Scheduled start | Observed departures | Coverage | Punctuality | Median recorded delay |
|---|---:|---:|---:|---:|
| 06:15 | 132 | 100.00% | 74.24% | +50 s |
| 08:15 | 131 | 99.24% | 68.70% | +97 s |
| 10:15 | 132 | 100.00% | 65.15% | +92.5 s |
| 12:15 | 132 | 100.00% | 58.33% | +146 s |
| 14:15 | 130 | 98.48% | 59.23% | +147.5 s |
| 15:15 | 130 | 98.48% | 34.62% | +255 s |
| 16:15 | 131 | 99.24% | 19.85% | +364 s |
| 18:20 | 132 | 100.00% | 87.88% | +61 s |

The afternoon result is much weaker than the earlier and later departures in
this pattern. This describes recorded September performance. It does not identify
traffic, timetable allowances, passenger volumes or vehicle carryover as its cause.

## Växjö route 1: Stortorget to Dalbo Centrum

For one fixed direction-0 stop and running-time pattern, weekday journeys
starting between 15:00 and 17:59 supplied 362 observed adjacent pairs out of
363 eligible pairs (99.72% paired coverage). The median recorded delay increase
between Stortorget and Dalbo Centrum was **163 seconds**. Both are intermediate
timing stops, which makes this a useful companion to the first-stop example above.

The increase also appeared outside the afternoon. Grouping by the journey's
scheduled start hour, the median was +57.5 seconds at 08:00–08:59 (132 pairs),
+193 seconds at 15:00–15:59 (107 pairs), +172 seconds at 16:00–16:59
(124 pairs), and +9 seconds at 23:00–23:59 (22 pairs). These are journey start
hours, not the scheduled hour at Stortorget. Different departures within each
hour remain a possible source of variation.

The recurring **15:53 departure from Löparvägen towards Kurortsvägen** gives a
more specific example. Its Stortorget–Dalbo pair was observed on 21 eligible
weekday trips: median increase +210 seconds, with seven transitions from on time
to late and none from late to on time. Dalbo itself had 20 late observations
out of 22. Earlier pairs for this departure had only 19 observed trips and fail
the 20-trip reporting floor; their delay summaries are withheld here.

## Method and checks

- Source: the complete September warehouse pinned to revision
  `3cb190163d9c400fd915bd62a919f8f9bfc90dd7`. The reproduction checks local
  files against the saved SHA-256 manifest before querying.
- Journey identity within a date is `(service_date, trip_id)`. Recurring identity
  is route ID, direction, ordered stop IDs, timing-stop flags, scheduled running
  times, service-relative start time and weekday/Saturday/Sunday class. Times
  beyond midnight retain their service-day offset. Trip IDs are not used to
  identify recurring departures across dates.
- Sixty-five date/key combinations matched multiple scheduled trips. They belong
  to four recurring keys, which are excluded entirely from recurring comparisons.
  After exclusion, 1,634 weekday recurring groups meet the existing reporting
  floor. No individual weekend recurring departure meets it within this month;
  longer history is needed for reportable weekend comparisons.
- The timing-stop population reconciles to the dashboard's September baseline:
  129,069 eligible, 127,059 observed, and 99,396 on-time departures.
- Adjacent pairs are formed from the scheduled non-final timing stops **before**
  eligibility filtering. Missing, skipped or cancelled records cannot silently
  bridge a pair. September contains 77,567 scheduled pairs, 77,386 eligible
  pairs, and 75,587 observed pairs across 39,940 distinct trips.
- Pair coverage is observed pairs / eligible pairs, where both departures must
  be eligible. This is a diagnostic separate from departure coverage. Displayed
  group summaries require at least 20 distinct observed trips and 90% coverage;
  pair summaries must also meet these requirements at paired grain.
- On time remains −60 to +180 seconds inclusive. Cancelled, skipped and
  DST-ambiguous departures are excluded from eligibility. Missing realtime is
  not treated as zero delay or as cancellation.

Recorded departures are retained TripUpdates timestamps, not independently
confirmed passages. The one-day first-stop validation found a median difference
of about 16 seconds against GPS-derived departure, but no correction is applied.
Timetable allowances or operational causes cannot be established from these
records alone. This is a descriptive, selected September study; it contains no
causal or statistical-significance claim.

## Practical next step

Build a journey explorer with a route, direction, stop-pattern and scheduled-start
selector; a trace for each service date; and a paired-delay table that shows
missingness. Beside it, show recurring departure results by weekday date so
occasional outliers are distinguishable from repeatable weak sections. Route 840
16:15 and route 1 Stortorget–Dalbo are useful initial examples. The current Sheet
aggregates cannot supply these traces; the warehouse must provide a new analysis
table before extending the live report.

Reproduce locally with `python scripts/analyze_journey_patterns.py` after preparing
the September snapshot. Queries are in
[`scripts/sql/journey_patterns.sql`](../scripts/sql/journey_patterns.sql);
results and individual-journey exports stay under `data/case-study/journey-patterns/`.
No live report, Sheet, pipeline or sharing settings were changed.

Sources: [metric definitions](definitions.md), [data dictionary](data_dictionary.md),
[September case study](case-study.md), and the
[pinned warehouse revision](https://huggingface.co/datasets/Traumenteize/kronoberg-transit-punctuality/tree/3cb190163d9c400fd915bd62a919f8f9bfc90dd7).
