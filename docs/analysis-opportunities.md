# Where the Kronoberg analysis can go next

**Feasibility review · 11 October 2026 · complete September data**

The next useful step is to explain the shape of the afternoon punctuality dip:
is it spread across the network, or concentrated in certain routes, directions
and parts of a journey? The warehouse supports this detail without changing the
existing punctuality definition or collecting another month's data first.

This review uses the same pinned September snapshot as the case study, not a
new live-data refresh. The [executed feasibility notebook](../notebooks/detail-analysis-feasibility.ipynb)
records the queries and checks.

The first [journey and recurring-departure study](journey-patterns.md) now follows
route 840's 16:15 departure and Växjö route 1's Stortorget–Dalbo section through
complete September data, with reproducible queries and explicit paired coverage.
Regional buses are analysed separately from city buses; city comparisons retain
the town categories already defined in the route mapping.

## Recommended order

| Investigation | What it would answer | How to do it with the existing data |
|---|---|---|
| Route, direction and departure hour | Which services account for the afternoon dip? | Pool observed/on-time counts within each group; show coverage, distinct trips, service dates and late-departure volume alongside rates. Compare hours within the same route and direction. |
| Delay along a journey | Does a trip begin late, gain delay or recover between timing stops? | Match observations within each service-date/trip pair in stop-sequence order. Compare adjacent scheduled timing-stop pairs, keeping missing pairs visible. Separate route patterns and first-stop effects. |
| Recurring service patterns | Is a weak result repeated, or driven by a few unusual days? | Compare daily results within route, direction, stop pattern and scheduled start-time groups. Do not assume trip IDs identify the same recurring departure across dates. |
| Station results within comparable services | Are low station results still low when route and time mix are held comparable? | Break down station results by route/direction/hour, with sample sizes and coverage. Check whether the same pattern appears across multiple services. |
| Severe delays and missing observations | Are there long delay tails or specific services missing from realtime? | Inspect delay distributions beyond the headline threshold, and map unobserved eligible departures and no-realtime trips by route and scheduled time. Keep cancellations separate. |

## What the pilot established

- The September warehouse contains 1,257,432 scheduled stop-event rows and
  54,506 scheduled trip rows. The existing scope rules reduce the primary
  timing-stop population to 129,069 eligible and 127,059 observed departures.
- All 51,714 in-scope scheduled trips have a direction code. Codes 0 and 1
  need to be translated using the actual route patterns before naming directions.
- There are 1,509 weekday route/direction/hour groups with eligible departures;
  1,265 meet the existing reporting floor. Of these, 301 fall in 15:00–17:59.
- Of 51,279 trips with an observed timing-stop departure, 39,958 have observations
  at two or more timing stops. This makes journey progression feasible; it does
  not prove that every adjacent timing-stop pair has adequate coverage.

For example, route 1 at 16:00–16:59 on September weekdays gives:

| GTFS direction | Observed departures | Distinct observed trips | Coverage | Punctuality |
|---|---:|---:|---:|---:|
| 0 | 514 | 173 | 98.28% | 58.56% |
| 1 | 384 | 173 | 98.97% | 48.44% |

Both groups contain observations on all 22 September weekdays. The difference
is an investigation lead, not a causal finding. They may contain different stop
patterns and timing-stop populations, and departures from the same trip are
correlated. Any uncertainty estimate should respect clustering by trip and day.

## The first focused study

Start with weekday departures from 15:00 to 17:59, then compare other hours
within the same route/direction/pattern. Rank candidates by both late-departure
volume and punctuality, avoiding a list dominated by tiny samples. Follow the
most useful candidates through their timing stops and across service dates.

Use the original reporting floor for every displayed group: at least 20 distinct
observed service-date/trip pairs and 90% coverage. Below-floor counts and quality
diagnostics may remain visible, but punctuality should not be promoted as a
reportable result. Preserve the −60 to +180 second interval and the existing
exclusions. Do not sum overlapping distinct-trip counts across groups.

A journey study needs its own paired-observation coverage check. Do not treat
unobserved stops as zero delay, bridge missing timing stops as if adjacent,
include final-stop departure punctuality, or apply the one-day 16-second
first-stop caveat as a correction.

## What these records cannot establish by themselves

The current warehouse does not contain passenger counts, traffic conditions,
weather, operational explanations or vehicle assignments linking successive
trips. It can identify when and where recorded delay increases, but cannot
attribute that increase to congestion, crowding or another cause. Departure
counts are not passenger impact. The existing GPS validation covers one day;
it does not validate every September departure.

The Google Sheet provides useful aggregates, but the deeper cuts above require
the Parquet warehouse. No live Sheet, dashboard, pipeline or definitions were
changed during this feasibility review.

Sources: [data dictionary](data_dictionary.md), [metric definitions](definitions.md),
[September case study](case-study.md), and dataset revision
[`3cb190163d9c400fd915bd62a919f8f9bfc90dd7`](https://huggingface.co/datasets/Traumenteize/kronoberg-transit-punctuality/tree/3cb190163d9c400fd915bd62a919f8f9bfc90dd7).
