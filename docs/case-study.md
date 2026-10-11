# Measuring Kronoberg transit punctuality without hiding missing observations

**September 2026 · 30 complete service dates · timing-stop departures**

For a shorter portfolio presentation: [English](portfolio-en.md) ·
[Svenska](portfolio-sv.md). This page preserves the detailed evidence and
reproduction steps.

Kronoberg's realtime records show **78.23% of observed timing-stop departures
on time**, with **98.44% coverage**. The lowest reportable weekday hourly
punctuality was **62.62% at 16:00–16:59**. These results identify where a further
operational investigation could be useful; they do not establish why buses were
late or measure performance against the operator's own reporting standard.

The project turns historical transit feeds into a daily pipeline, a public
Parquet dataset and a six-page Data Studio dashboard. Its main analytical
challenge was deciding which realtime values count as observations and which
scheduled departures belong in the denominator.

## Question and scope

How punctual are departures in Kronoberg, where and when is punctuality lower,
and how much of the schedule can the realtime feed actually observe?

The primary population is in-scope Länstrafiken Kronoberg departures at timing
stops, excluding final stops. On time means a delay between −60 and +180 seconds,
inclusive. Coverage and sample size accompany punctuality; missing observations
are never treated as on-time departures.

This is a complete September analysis, not the earlier September 1–21 local
dashboard snapshot. The live report also contains daily monitoring from later
dates. Neither later dates nor incomplete-month totals enter the results below.

## What the month shows

| Measure | Numerator / denominator | Result |
|---|---|---:|
| Punctuality | 99,396 on-time / 127,059 observed departures | 78.23% |
| Coverage | 127,059 observed / 129,069 eligible departures | 98.44% |
| Early departures | 4,428 / 127,059 observed | 3.48% |
| Late departures | 23,235 / 127,059 observed | 18.29% |
| +60-second sensitivity | 70,179 / 127,059 observed | 55.23% |
| +300-second sensitivity | 112,548 / 127,059 observed | 88.58% |

The observed population covers **51,279 distinct service-date/trip pairs**.
There are **2,010 eligible but unobserved departures**. The sensitivity results
retain the same −60-second early limit; they show how strongly the headline
depends on the chosen late threshold.

### Weekday and weekend comparisons

| Service-date grouping | Observed departures | On-time departures | Coverage | Punctuality |
|---|---:|---:|---:|---:|
| Weekday | 111,587 | 86,060 | 98.27% | 77.12% |
| Saturday | 8,645 | 7,319 | 99.67% | 84.66% |
| Sunday | 6,827 | 6,017 | 99.82% | 88.14% |

Weekdays account for most observations and have lower punctuality than either
weekend grouping. These are descriptive comparisons: route mix, service
frequency and operating conditions differ. The three count columns reconcile
to the monthly headline; their percentages are not averaged.

The lowest reportable weekday hours were 16:00–16:59 (**62.62%**, 10,514 observed
departures), 15:00–15:59 (**68.27%**, 10,111) and 17:00–17:59 (**70.03%**, 8,426).
Hours use scheduled departure time in Europe/Stockholm. A sensible follow-up is
to examine route and direction mix within those windows before attributing the
pattern to congestion, timetable design or any other cause.

### Category and station comparisons require their own quality checks

Regional lines had **75.06%** punctuality over 56,386 observed departures; Växjö
city lines had **83.24%** over 60,145. Their coverage was 99.12% and 99.71%,
respectively. Different route lengths and stop patterns make this a comparison
of populations, not a controlled comparison of operating performance.

**School-route category punctuality is withheld.** Its timing-stop coverage is
only **83.39%** (6,443 observed / 7,726 eligible departures), below the 90%
reporting floor. Omitting that quality check would turn a visibly incomplete
population into an apparently comparable result. Its counts remain in the
warehouse and contribute to the properly pooled network denominator.

At station level, **256 of 425 timing-stop station groups** meet the reporting
floor. Examples among the busiest reportable stations:

| Station | Observed departures | On-time departures | Coverage | Punctuality |
|---|---:|---:|---:|---:|
| Stortorget | 21,598 | 18,154 | 99.82% | 84.05% |
| Växjö station | 5,323 | 4,795 | 99.66% | 90.08% |
| Dalbo Centrum | 3,736 | 2,247 | 99.63% | 60.14% |
| Ljungby terminal | 3,669 | 3,376 | 99.46% | 92.01% |

These are station aggregates across their observed services, not passenger
wait-time estimates. The dashboard's station map uses real GTFS coordinates,
observed departure counts for bubble size and punctuality for colour.

## Why the definitions matter

The initial hypothesis was that the last prediction before a stop disappeared
from the feed represented its passage time. Full-day validation showed that
passed stops were retained for about ten minutes: disappearance was not the
passage event. The accepted observation rule instead requires the feed's
recorded-time marker, `uncertainty = 0`, on the final retained value. The
investigation and correction are preserved in D-005 through D-007 and the
[validation reports](validation/held_values_2026-09-07.md).

Final stops are excluded because trips can leave the feed before their final
stop receives a recorded time. Trips operated by neighbouring authorities are
kept in the warehouse as out of scope rather than counted as missing Kronoberg
observations. Cancelled, skipped and DST-ambiguous stop events are excluded
from eligibility and reported separately. On-time shares are published only
with at least 20 distinct observed trips and 90% coverage at the shown level.

Timing stops and all stops are different populations. The all-stop September
result is **63.20%** punctuality over **1,133,962 observations**, with **99.29%**
coverage. Non-timing-stop schedule times appear interpolated, so this is a
separate comparison rather than evidence that either headline is erroneous.
The investigation of their difference remains open.

## Missing data is not cancellation

September contains **290 in-scope trips with no realtime data** and **39
cancelled trips**. They are separate categories. Measured feed-outage minutes
between 06:00 and 22:00 total **zero** for these 30 service dates, but that does
not establish perfect realtime coverage: trips can be absent while the feed
itself continues, and outages outside those hours are excluded from that metric.
No September schedule-mismatch stop events, unmatched realtime trips or
unmapped-route trips were found in the daily quality outputs.

A one-day GPS validation found first-stop recorded times about **16 seconds
earlier at the median** than GPS-derived departures. This is a timestamp caveat,
not a network-wide correction. GPS validation has not established stability
across weekends or later months. These results are not directly comparable
with the operator's official punctuality statistics.

## Implementation and verification

Historical KoDa archives and same-service-date GTFS schedules are parsed in
Python and transformed in DuckDB SQL. Cleaned Parquet partitions are published
by service date; Google Sheets serves aggregates rather than storing millions
of stop events. The daily workflow is dispatched by the external scheduler,
with manual recovery available. Each rerun replaces a date's partitions and
the serving layer is rebuilt from the warehouse.

The six report pages cover Overview, When, Routes, Stations, Data quality and
About. Filters choose one stop set and day grouping at a time; pooled rates use
the underlying counts rather than averaging percentages. Visual acceptance
combined inspected settings and representative filter tests with the owner's
rendered review. Blank plots in the Codex browser had populated underlying
data; the working-browser review prevented unnecessary changes to valid data.

For this case study, all **180 September Parquet partitions** were downloaded
from a pinned public revision. All **229 aggregation consistency checks**
passed. Stop-event and trip keys were checked for duplicates. A separate query
over stop-event rows independently matched the eligible, observed, on-time and
distinct observed-trip headline counts. All **158 existing tests**, lint and
formatting checks passed; tests used a writable project-local temporary folder.

## Reproduce and inspect

- Dataset revision: [`3cb190163d9c400fd915bd62a919f8f9bfc90dd7`](https://huggingface.co/datasets/Traumenteize/kronoberg-transit-punctuality/tree/3cb190163d9c400fd915bd62a919f8f9bfc90dd7).
- Aggregation code used: [`dc61af5bfc7d1a24b9c54be9c808c12905430a9f`](https://github.com/Iskarioth/kronoberg-transit/tree/dc61af5bfc7d1a24b9c54be9c808c12905430a9f).
- [Reproduction script](../scripts/prepare_case_study.py): run
  `uv run python scripts/prepare_case_study.py` from the repository root. It uses
  unauthenticated dataset reads, checks all 30 dates, rebuilds aggregates and
  writes evidence plus SHA-256 input hashes under gitignored `data/case-study/`.
  It does not read credentials or write to Google Sheets.
- [Metric definitions](definitions.md), [data dictionary](data_dictionary.md)
  and [decision log](decisions.md).
- [Live dashboard](https://datastudio.google.com/reporting/5dcf0882-e605-4eee-a53d-4a2e3f41797d),
  subject to its Google sharing settings. The report may include later data;
  the pinned revision above controls this case study.

The project's contribution is a reproducible measurement system with explicit
quality limits and useful investigative cuts. It does not claim an operational
improvement, a causal explanation for delays or a validated network-wide
timestamp adjustment.
