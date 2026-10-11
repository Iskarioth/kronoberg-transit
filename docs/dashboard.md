# Dashboard guide and delivery status

Reviewed on **11 October 2026**. The six-page dashboard is finished within the
requested scope, combining inspected report settings and representative filter
tests with the owner's confirmation that all data is populated and the report
works as intended. This is dashboard acceptance, not a new independent pipeline
or source-calculation audit.

## Deliverables

- [Live Google Data Studio report](https://datastudio.google.com/reporting/5dcf0882-e605-4eee-a53d-4a2e3f41797d)
  (formerly Looker Studio). Sharing and access settings were preserved; anonymous
  access has not been verified.
- [Public Parquet dataset](https://huggingface.co/datasets/Traumenteize/kronoberg-transit-punctuality).
- [September portfolio case study](case-study.md), with pinned-data reproduction.
- Pipeline code, [metric definitions](definitions.md), [schema](data_dictionary.md)
  and [decision log](decisions.md) in this repository.

The earlier local dashboard is an exploratory snapshot of September 1–21, 2026.
It is not the live report, does not contain the same period, and must not be used
to reproduce complete-September figures without obtaining the appropriate data.

## Pages and populations

| Page | Purpose and population |
|---|---|
| Overview | Complete-month headline figures, separate all-stop comparison, day-type split and daily timing-stop trend |
| When | Hourly comparisons for one month, day grouping and stop set; defaults observed at acceptance: September, weekday, timing stops |
| Routes | Route comparisons by category, month, day grouping and stop set; defaults: September, all days, timing stops, Växjö city lines |
| Stations | September 2026, all days, timing stops, reportable stations only; table and Google Maps bubble map |
| Data quality | Available service dates, all non-final stops; coverage, missing realtime, outage minutes 06:00–22:00 and cancellations |
| About | Definitions, reporting floor, freshness interpretation and caveats |

The Stations table and map use the saved filter `day_type=all`,
`stop_set=timing_stops`, `month=2026-09`, `reportable=TRUE`. Bubble size represents
observed departures; colour represents punctuality. This page is intentionally
fixed to September and will require a deliberate update or month selector before
it can show a later complete month.

## Interpretation rules

[definitions.md](definitions.md) remains the source of truth. Dashboard work did
not change thresholds or metric formulas.

- On time: departure delay from −60 to +180 seconds, inclusive. Sensitivity
  measures retain the −60-second early limit and use +60 or +300 seconds.
- Primary population: timing stops, excluding final stops.
- Punctuality: on-time observed departures / observed departures.
- Coverage: observed departures / eligible departures. Cancelled, skipped and
  DST-ambiguous stop events are excluded from eligibility.
- Missing realtime and cancellations are separate measures.
- Reporting floor: at least 20 distinct observed trips and at least 90% coverage.
- Monthly results require a complete month. Daily monitoring may include a later,
  incomplete month.
- Select one stop set and one day grouping. Never sum overlapping all-days and
  individual-day-type aggregate rows, or average their percentages. Compute
  combined rates from valid, disjoint numerator and denominator counts.
- A one-day GPS validation found first-stop recorded times about 16 seconds
  earlier at the median. This is a caveat, not a network-wide correction; the
  figures are not directly comparable with operators' official statistics.

## Acceptance evidence

These are dated observations, not automatically current readings:

| Check on 11 October 2026 | Result |
|---|---|
| Report freshness badges | Data through 9 October; Sheet rebuild `2026-10-11T04:49:41Z` |
| September completeness | Complete-month flag true; 30 service dates |
| Stations | 256 reportable stations; map rendered |
| Stortorget spot check | 21,598 observed departures; 99.82% coverage; 84.05% punctuality |
| Data quality | 39 service dates; selecting October 1–8 returned exactly eight rows; reset restored 39 |
| When control test | Changing weekday to all changed hourly values; reset restored weekday |
| Routes control test | Regional lines returned 51 rows versus 14 Växjö city-line rows; reset restored defaults |
| Plot data inspection | Overview daily and both quality charts had 39 populated dates behind their plots |
| Rendered acceptance | Owner supplied a screenshot of Overview's plots and then confirmed all data is populated and the report works as intended |

Some plots appeared blank in the Codex in-app browser despite populated underlying
data. The subsequent rendered screenshot and owner review closed the dashboard
blocker; the browser-specific limitation remains relevant to future automation.
The working browser's identity was not recorded. Do not rewrite data or formulas
solely because that automation surface shows blank plot frames.

The Overview caveat was corrected to the bounded one-day finding. No Sheet values,
pipeline definitions, sharing settings or access permissions were changed.

## Operating checks

1. Compare the report's **Data through** date with the expected service date,
   normally today minus two days in Europe/Stockholm. A rebuild timestamp alone
   does not prove that new service dates arrived.
2. Check the [daily workflow runs](https://github.com/Iskarioth/kronoberg-transit/actions/workflows/daily.yml)
   and the Sheet's `run_log` if freshness falls behind. The external scheduler
   only checks that GitHub accepted the dispatch, not that the pipeline succeeded.
3. Keep missing-realtime counts separate from cancellations. Review coverage and
   outage minutes alongside any punctuality result.
4. At each completed month, check the completeness flag and reporting floor,
   review default filters, and deliberately update Stations if it should move to
   that month. Reset viewer filters after acceptance checks.

The seven most recent daily runs read from GitHub on 11 October all completed
successfully. The [11 October dispatch](https://github.com/Iskarioth/kronoberg-transit/actions/runs/38112682267)
started at 04:47:11 UTC. Both the external dispatch and GitHub schedule ran on
October 8, 9 and 10; the [October 10 scheduled run](https://github.com/Iskarioth/kronoberg-transit/actions/runs/38047675491)
also succeeded. This updates the original D-023 observation that the GitHub
schedule had not fired. It does not establish that every historical run succeeded.

## Next steps

1. **Scheduling consolidation completed (D-024).** Keep the existing external
   dispatch and manual recovery inputs; remove the redundant GitHub schedule.
   No external scheduler settings were changed. The first subsequent dispatch
   remains the next operational confirmation; no extra run was triggered merely
   to test the unchanged pipeline.
2. **Portfolio case study prepared.** Complete September data was retrieved at
   dataset revision `3cb190163d9c400fd915bd62a919f8f9bfc90dd7`. All 229 aggregation
   consistency checks passed; independent raw-stop-event counts match the
   headline. See [case-study.md](case-study.md) and the reproduction script.
3. **Reader access needs an explicit sharing decision.** On 11 October the
   report's sharing dialog showed **Restricted**, with only the owner listed.
   A portfolio reader cannot access that report under the verified settings.
   No access permissions were changed during the inspection.
4. **Resolve open definitions with evidence when needed.** The recorded-time
   marker alert threshold and public-holiday day classification remain OPEN in
   definitions.md. Do not choose thresholds or implement dependent logic without
   approval. Neither blocks the accepted September dashboard.

Avoid adding more dashboard pages until these maintenance and presentation steps
have a concrete purpose. The current dashboard already supports the original
network, time, route, station and data-quality questions.
