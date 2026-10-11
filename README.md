# kronoberg-transit

Public transport punctuality in Kronoberg county, Sweden — measured from historical
GTFS-Realtime data for Länstrafiken Kronoberg (Trafiklab operator code `krono`). Built as
a portfolio data project: fetching, parsing and transforming real transit data into a
dashboard, with the full pipeline, decisions and definitions open for review.

**Status:** daily pipeline implemented; six-page dashboard accepted on 11 October 2026.

- [Live Data Studio report](https://datastudio.google.com/reporting/5dcf0882-e605-4eee-a53d-4a2e3f41797d)
  (access follows the report's existing Google sharing settings).
- [Parquet dataset](https://huggingface.co/datasets/Traumenteize/kronoberg-transit-punctuality)
- [Dashboard guide, verification and next steps](docs/dashboard.md)
- [September 2026 portfolio case study](docs/case-study.md)
- Portfolio presentation: [English](docs/portfolio-en.md) · [Svenska](docs/portfolio-sv.md)

The report contains Overview, When, Routes, Stations, Data quality and About.
September 2026 is the first complete reporting month. Daily monitoring includes
newer service dates; freshness is displayed in the report rather than inferred
from the current date. An earlier local dashboard uses an archived September 1–21
snapshot and is a separate exploratory artifact.

## Pipeline

```
KoDa archive (raw, never stored by us)
  → Python: fetch + parse protobuf
  → DuckDB SQL: dedupe, join to same-date static schedule, compute delays
  → Parquet partitions on Hugging Face (warehouse)
  → daily aggregates to Google Sheets (serving layer)
  → Google Data Studio (formerly Looker Studio)
```

Metric definitions live in [`docs/definitions.md`](docs/definitions.md) — that file is
the source of truth. Design decisions are logged in
[`docs/decisions.md`](docs/decisions.md).

### Scheduling

The daily workflow is started by [cron-job.org](https://cron-job.org) through GitHub's
`workflow_dispatch` API at 06:47 Europe/Stockholm. GitHub's own scheduled trigger
originally did not fire and was retained as a fallback (D-023). Once it began
producing duplicate daily runs, it was removed on 11 October 2026 (D-024).
Manual dispatch remains available for recovery and backfills. Data normally lags
two days behind the current date.

## Data sources

- **[Trafiklab](https://www.trafiklab.se/)** — GTFS Regional static and realtime feeds
  for Länstrafiken Kronoberg (`krono`).
- **[KoDa](https://www.trafiklab.se/api/our-apis/koda/)** (Kollektivtrafikdata), provided
  by Trafiklab in collaboration with RISE and Vinnova — historical GTFS and
  GTFS-Realtime archives.

## Development

See [`CLAUDE.md`](CLAUDE.md) for project conventions and rules. Common commands:

```bash
uv sync                                  # install/update dependencies
uv run pytest                            # tests
uv run ruff check . && uv run ruff format .
uv run --env-file .env python scripts/<script>.py
```
