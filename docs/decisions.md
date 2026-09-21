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
