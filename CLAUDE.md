# CLAUDE.md — kronoberg-transit

## Project

Portfolio data project measuring public transport punctuality in Kronoberg county
(Länstrafiken Kronoberg, Trafiklab operator code `krono`) from historical GTFS-Realtime
data. Built by Marcus for Swedish data analyst roles and Upwork clients, so the code,
commits and docs are all public and read by hiring managers.

Pipeline:

```
KoDa archive (raw, never stored by us)
  → Python: fetch + parse protobuf
  → DuckDB SQL: dedupe, join to same-date static schedule, compute delays
  → Parquet partitions on Hugging Face (warehouse)
  → daily aggregates to Google Sheets (serving layer)
  → Looker Studio (public dashboard)
```

`kronoberg_transit.pipeline` runs this end to end for one or more service dates: fetch,
transform, upload that date's partitions to the Hugging Face dataset, then rebuild the
Sheet from the full dataset (D-020). A scheduled GitHub Actions workflow
(`.github/workflows/daily.yml`) runs it once a day, picking up the dates missing from
the dataset. Stop events in the daylight-saving window are marked `dst_ambiguous`
rather than blocking the date (D-021).

Metric definitions live in `docs/definitions.md`. That file is the source of truth.
Design decisions live in `docs/decisions.md`.

The Hugging Face dataset card's source is `docs/dataset_card.md`. Edit it there, then
upload it as `README.md` with `HfApi.upload_file` (commit message "docs: update the
dataset card"). Never edit the card on Hugging Face.

## Non-negotiable rules

### 1. No AI attribution in commits or pull requests

This applies even though global settings already disable attribution.

- Never add `Co-Authored-By` trailers naming Claude, Anthropic or any AI model.
- Never add "Generated with Claude Code" lines, robot emoji signatures, `Claude-Session:`
  trailers or claude.ai session URLs.
- The same applies to PR titles and descriptions, release notes and tags.
- Commits are authored by the repo owner's existing git identity. Never change
  `user.name` or `user.email`.
- `.githooks/commit-msg` strips attribution as a backstop. Never bypass it with
  `--no-verify`, never edit or delete it, never change `core.hooksPath`.

### 2. Secrets

- Never read, print, log, paste or commit `.env` or anything in `secrets/`.
- Refer to secrets by variable name only. To pass them to tools, use
  `uv run --env-file .env ...` or shell substitution that never echoes the value.
- If a secret appears in any output, stop and tell Marcus immediately.

### 3. Definitions are fixed unless Marcus approves a change

Never change a threshold, exclusion rule or metric formula silently. Propose the change,
explain the effect on results, and wait. Once approved, update `docs/definitions.md` and
add an entry to `docs/decisions.md` in the same commit.

### 4. Respect API quotas

- Trafiklab GTFS Regional **static**: Bronze key allows 50 calls per month. Cache every
  download. Never call it in tests, loops or retries.
- Trafiklab GTFS Regional **realtime**: Bronze key allows 30,000 calls per month.
- KoDa: no hard quota, but never more than 2 archive requests in flight. Handle HTTP 202
  by polling every 30 seconds. Prefer KoDa's historical static endpoint over the live
  static endpoint.

### 5. Data never goes in git

Raw archives, Parquet, DuckDB files and exports go under `data/` (gitignored). The only
exception is test fixtures under `tests/fixtures/`, each under 1 MB.

## Environment

- Windows 11. Claude Code's shell is Git Bash. Keep all scripts cross-platform: use
  `pathlib`, never hardcode backslashes or drive letters.
- `.env` files written on Windows may have CRLF line endings. Strip `\r` when parsing.
- Python 3.12, managed by `uv`. Never use `pip install` directly.

## Commands

```bash
uv sync                                  # install/update dependencies
uv run pytest                            # tests
uv run ruff check . && uv run ruff format .
uv run --env-file .env python scripts/<script>.py
uv run --env-file .env python -m kronoberg_transit.pipeline   # the daily pipeline
```

## Code conventions

- Transformations are DuckDB SQL in `src/kronoberg_transit/sql/`. Python handles IO and orchestration.
- Type hints on all functions. `ruff` for lint and format.
- Store timestamps in UTC. Convert to `Europe/Stockholm` only when assigning service
  days or producing reports.
- Every stage is idempotent per service date: re-running a date overwrites that
  date's partition, never duplicates rows.
- Every stage logs row counts in and out. Row loss without an explanation is a bug.
- Tests use small real fixtures, not invented data.

## Commit style

- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
- Imperative subject line, 72 characters max. Body explains why, not what.
- One logical change per commit. Commit only when tests and lint pass.
- Push to `origin/main` when a task is complete and green. Never force-push, never
  rewrite pushed history.

## Working style

- Flag uncertainty. If a number looks too clean, data is missing, or coverage is low,
  say so before presenting results. A caveat is better than an oversold chart.
- Record every data assumption in `docs/decisions.md` (date, decision, reason).
- Keep `docs/data_dictionary.md` in sync with every schema change.
- When something outside the repo is needed (accounts, keys, console settings), stop
  and ask Marcus rather than working around it.

## Repo map

```
kronoberg-transit/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── .env.example            # variable names only, no values
├── .githooks/commit-msg    # attribution backstop, do not touch
├── .claude/settings.json
├── .github/workflows/
├── docs/
│   ├── definitions.md      # source of truth for metrics
│   ├── decisions.md        # decision log
│   └── data_dictionary.md
├── scripts/                # one-off and setup scripts
├── src/kronoberg_transit/  # pipeline package
│   └── sql/
├── tests/
│   └── fixtures/
├── data/                   # gitignored
└── secrets/                # gitignored
```
