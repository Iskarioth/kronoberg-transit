# kronoberg-transit

Public transport punctuality in Kronoberg county, Sweden — measured from historical
GTFS-Realtime data for Länstrafiken Kronoberg (Trafiklab operator code `krono`). Built as
a portfolio data project: fetching, parsing and transforming real transit data into a
public dashboard, with the full pipeline, decisions and definitions open for review.

**Status:** setup in progress.

## Pipeline

```
KoDa archive (raw, never stored by us)
  → Python: fetch + parse protobuf
  → DuckDB SQL: dedupe, join to same-date static schedule, compute delays
  → Parquet partitions on Hugging Face (warehouse)
  → daily aggregates to Google Sheets (serving layer)
  → Looker Studio (public dashboard)
```

Metric definitions live in [`docs/definitions.md`](docs/definitions.md) — that file is
the source of truth. Design decisions are logged in
[`docs/decisions.md`](docs/decisions.md).

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
