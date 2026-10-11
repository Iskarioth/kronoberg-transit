# Växjö route 1: recurring delay between Stortorget and Dalbo

**Complete September 2026 · weekday study · 11 October 2026**

The delay increase between Stortorget and Dalbo is repeated across dates and
scheduled departures. It is stronger around midday and afternoon, but it is not
limited to a few unusually bad afternoons. This study concerns one fixed Växjö
city-bus pattern, separately from regional services and other route 1 variants.

## What is included

The selected direction-0 pattern runs from Löparvägen towards Kurortsvägen,
with non-final timing stops at Löparvägen, Teleborg C, Stortorget and Dalbo Centrum.
There were 1,354 scheduled journeys in this pattern across 22 September weekdays.
It is the same pattern identified in the earlier journey study; this is a focused
follow-up, not a new search for the worst route.

Other route 1 directions, endpoints and scheduled running-time patterns remain
separate. Scheduled start times are measured at Löparvägen in Stockholm local
service-day time; a time beyond 24:00 remains attached to its original service date.

## Delay builds at a particular section

| Consecutive timing stops | Observed / eligible pairs | Paired coverage | Median delay change | Trips gaining delay |
|---|---:|---:|---:|---:|
| Löparvägen → Teleborg C | 1,332 / 1,342 | 99.25% | +25 s | 900 / 1,332 |
| Teleborg C → Stortorget | 1,336 / 1,339 | 99.78% | −23 s | 406 / 1,336 |
| Stortorget → Dalbo Centrum | 1,338 / 1,343 | 99.63% | +124 s | 1,289 / 1,338 |

Across the last section, recorded delay increased on **96.34%** of observed
pairs. There were 442 transitions from on time to late, compared with one from
late to on time. The central 80% of paired changes ran from +30 to +236 seconds;
this is observed variation, not a confidence interval.

The direction of change is consistent across the month. Each of the 22 weekday
medians was positive, ranging from +94 to +152 seconds. Recalculating the pooled
median after leaving out each date in turn gave +123 to +125 seconds, close to
the full-period +124. A single day does not explain the pooled result. This check
does not establish stability in another month or identify the cause.

## Which scheduled departures repeat it?

There are 64 recurring start-time groups in this fixed pattern. Sixty meet the
20-observed-trip and 90% paired-coverage floor. The 16:53 group has only 18
observed pairs; the 24:33, 25:33 and 26:33 groups have four each. Their delay
summaries are withheld from displayed results, even though paired coverage is
100%. An eligible denominator can exclude skipped or cancelled departures, so
high coverage alone does not establish a sufficient sample.

The following examples span weak daytime departures and later comparators.
They are selected illustrations, not a complete ranking; the notebook plots all
60 reportable recurring departures.

| Scheduled start at Löparvägen | Observed pairs | Paired coverage | Median increase to Dalbo | Dates gaining delay | On time at Stortorget → Dalbo, among pairs |
|---|---:|---:|---:|---:|---:|
| 12:13 | 22 | 100.00% | +209 s | 22 / 22 | 16 → 2 |
| 15:23 | 22 | 100.00% | +223 s | 22 / 22 | 20 → 1 |
| 15:53 | 21 | 100.00% | +210 s | 21 / 21 | 9 → 2 |
| 16:03 | 22 | 100.00% | +204 s | 22 / 22 | 16 → 3 |
| 22:33 | 22 | 100.00% | +14.5 s | 14 / 22 | 22 → 19 |
| 23:33 | 22 | 100.00% | +9 s | 13 / 22 | 22 → 21 |

All 60 reportable recurring groups had a positive median change. However,
"gaining delay" is different from "becoming late": a trip can add a few seconds
and remain within the −60 to +180 second on-time interval. The late-evening
comparators make that distinction visible.

For the 15:23 departure, the 10th–90th percentile range of section changes was
+156.5 to +307 seconds. The difference persists across dates rather than coming
only from one extreme trip. All four timing stops were observed on all 22 dates.

| Timing stop for the 15:23 departure | Median recorded departure delay | On time / observed |
|---|---:|---:|
| Löparvägen | +21.5 s | 21 / 22 |
| Teleborg C | +60.5 s | 19 / 22 |
| Stortorget | +38.5 s | 20 / 22 |
| Dalbo Centrum | +266 s | 1 / 22 |

These medians describe each stop separately. The +223-second section change
comes from differences within individual journeys, not subtraction of these medians.

## Does recovery last?

For journeys starting from 15:00 to 17:59, the preceding Teleborg–Stortorget
section had a median change of −15 seconds; Stortorget–Dalbo then added a median
163 seconds. Of 358 journeys observed at all three of these timing stops,
225 first reduced recorded delay and then increased it again. This links the
recovery and renewed gain within the same journeys, rather than relying only
on unrelated stop summaries.

There is an important measurement boundary: **Dalbo is the last non-final timing
stop in this pattern.** We cannot establish recovery after Dalbo from the primary
population. Final-stop departure metrics remain excluded, and intermediate stops
with estimated timetable times should not silently extend this timing-stop analysis.

## Interpretation and next action

The evidence identifies a repeatable recorded-delay increase on Stortorget–Dalbo,
with the largest selected examples around midday and afternoon. It does not show
whether the explanation is traffic, dwell time, timetable allowances or another
operational factor. The section starts and ends at intermediate timing stops;
this avoids directly depending on a first-stop pair, but does not independently
validate every retained realtime timestamp.

The next useful comparison is the opposite direction within route 1, keeping its
own pattern and schedule separate. Before recommending timetable or operational
changes, examine the scheduled allowance and available observation validation for
the selected section, then repeat the same study over another complete month.
A journey explorer should retain missing records, individual dates and the
reporting floor so these distinctions remain visible.

## Reproduction and validation

Run `python scripts/analyze_city_route1.py` after preparing the pinned September
snapshot. The [executed notebook](../notebooks/city-route1-journeys.ipynb) contains
three complementary figures and the key exact-value tables. Running it regenerates
the evidence through the same script. Plotting additionally requires Matplotlib
and a Python notebook kernel; these are local analysis tools, not production
pipeline dependencies.

Individual observations, CSV outputs, figures and `evidence.json` are stored in
ignored `data/case-study/city-route1/`. SQL is recorded in
[`city_route1.sql`](../scripts/sql/city_route1.sql) and uses the shared scheduled
journey/pair construction from [`journey_patterns.sql`](../scripts/sql/journey_patterns.sql).

Checks passed for all 180 snapshot hashes, date coverage, source-key uniqueness,
route category, raw-versus-joined eligibility, stored delay versus held/scheduled
timestamps, recurring-to-section count reconciliation and pair arithmetic. The
route-wide September population independently reconciles to 13,194 eligible,
13,140 observed and 10,539 on-time timing-stop departures; it is broader than
this selected weekday pattern.

Definitions and exclusions are preserved. Paired coverage is observed pairs /
eligible pairs, requiring eligibility at both adjacent scheduled timing stops.
Skipped, cancelled and DST-ambiguous records cannot silently bridge a missing
pair. Missing realtime remains visible. The one-day first-stop GPS caveat remains
uncorrected. No causal, statistical-significance or passenger-impact claim is made.
No live report, Sheet, sharing settings or pipeline definitions were changed.

Sources: [metric definitions](definitions.md), [route mapping](../config/route_categories.csv),
[data dictionary](data_dictionary.md), and the
[pinned September warehouse](https://huggingface.co/datasets/Traumenteize/kronoberg-transit-punctuality/tree/3cb190163d9c400fd915bd62a919f8f9bfc90dd7).
