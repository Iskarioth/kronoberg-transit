# HANDOFF: bootstrap kronoberg-transit

You are setting up the infrastructure for this project. **This session is setup only.**
Do not build the pipeline (fetching, parsing, transforming) yet; that is the next session.

## How to run this handoff

1. Read `CLAUDE.md` first. Every rule in it applies here, especially rule 1 (no AI
   attribution in commits) and rule 2 (secrets).
2. Work through the phases in order. At the end of each phase, report what you did and
   the result of its verification step before moving on.
3. Steps marked **HUMAN** need Marcus. Stop, tell him exactly what to do, and wait for
   confirmation. If the value already exists, verify it and skip the pause.
4. Check whether a `.env` value is present without revealing it:
   `grep -q '^HF_TOKEN=.\+' .env && echo present || echo missing`
5. Do not install software without asking first.
6. If something fails, stop and report. Do not work around a failure silently.

## Already in the folder

| File | Purpose |
|---|---|
| `CLAUDE.md` | Rules and conventions for you |
| `HANDOFF.md` | This file |
| `.claude/settings.json` | Attribution off, `.env` and `secrets/` read-denied |
| `.githooks/commit-msg` | Strips AI attribution from commit messages |
| `.gitignore`, `.gitattributes` | Gitignore and LF line endings (the hook needs LF) |
| `.env.example` | Variable names, no values |
| `docs/definitions.md` | Metric definitions, source of truth |
| `docs/decisions.md` | Decision log, seeded with D-001 to D-004 |
| `scripts/koda_check.py` | Verifies KoDa serves `krono` data |

---

## Phase 0 · Preflight

1. Report versions of `git`, `gh` and `uv`. Python is managed by `uv`, so no separate
   install is needed.
2. If anything is missing, show Marcus the install commands and wait:
   ```
   winget install --id GitHub.cli -e
   winget install --id astral-sh.uv -e
   ```
   A new terminal may be needed afterwards for PATH to update.
3. Check `git config --global user.name` and `git config --global user.email`. If either
   is unset, **HUMAN:** ask Marcus what to use. Never invent or change a git identity.
4. Check `~/.claude/settings.json` and report whether `attribution.commit` and
   `attribution.pr` are empty strings there. Report only; do not edit user settings.

**Verify:** all three tools report versions and the git identity is set.

## Phase 1 · Local repository

1. `git init -b main`
2. `git config core.hooksPath .githooks`
3. `uv init --package --python 3.12 --vcs none`. Afterwards check that it did not
   overwrite `.gitignore` or any existing file. Restore anything it replaced.
4. Dependencies:
   ```
   uv add duckdb pyarrow gtfs-realtime-bindings py7zr httpx python-dotenv gspread google-auth huggingface_hub
   uv add --dev pytest ruff
   ```
5. Add ruff config to `pyproject.toml`: line length 100, target `py312`.
6. Create `src/kronoberg_transit/sql/`, `tests/fixtures/` and `.github/workflows/`, each
   with a `.gitkeep`. Create `data/` and `secrets/` locally (both gitignored).
7. Create `docs/data_dictionary.md` with a section per Google Sheets tab (schema in
   Phase 5) and a placeholder section for the Parquet schema.
8. Create `README.md`: title, a one-paragraph description of the project, status
   "Setup in progress", the pipeline in text form (copy from `CLAUDE.md`), and a data
   source section crediting Trafiklab and KoDa (RISE, Vinnova, Trafiklab).
9. Stage everything, then mark the hook executable in git:
   ```
   git add -A
   git update-index --chmod=+x .githooks/commit-msg
   git ls-files --stage .githooks/commit-msg   # must show mode 100755
   ```
10. Run `git status` and confirm that `.env`, `secrets/` and `data/` are not staged.
11. Commit: `chore: initial project scaffold`
12. Test the hook with a throwaway commit:
    ```
    git commit --allow-empty -m "test: hook check" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
    git log -1 --format=%B        # must NOT contain the Co-Authored-By line
    git reset --soft HEAD~1
    ```

**Verify:** one commit on `main`, the hook test stripped the trailer, and
`git log --format=%B` of the scaffold commit contains no attribution.

## Phase 2 · GitHub

1. `gh auth status`. If not logged in: **HUMAN:** Marcus runs `gh auth login` in his own
   terminal (it is an interactive browser flow). Wait.
2. Create and push the repo:
   ```
   gh repo create kronoberg-transit --public --source=. --remote=origin --push \
     --description "Public transport punctuality in Kronoberg county from historical GTFS-Realtime data"
   gh repo edit --add-topic gtfs,gtfs-realtime,public-transport,data-analysis,duckdb,sweden
   ```

**Verify:** `gh repo view` shows the repo as public, and `git log origin/main` matches
local `main`.

## Phase 3 · Trafiklab

1. **HUMAN:** Marcus creates an account at developer.trafiklab.se, creates a project named
   `kronoberg-transit`, and adds three API keys: **KoDa**, **GTFS Regional Static** and
   **GTFS Regional Realtime**.
2. Run `cp .env.example .env`. **HUMAN:** Marcus pastes the three keys into `.env`.
3. Run the KoDa check:
   ```
   uv run --env-file .env python scripts/koda_check.py
   ```
   This can take up to 20 minutes if the archive is being built. Expect
   `VERDICT: KoDa is alive`. If it fails, stop and report the full output.
4. Write `scripts/smoke_trafiklab.py` with two checks:
   - **Realtime (default):** exactly ONE request to
     `https://opendata.samtrafiken.se/gtfs-rt/krono/TripUpdates.pb?key=...`, parse it
     with `gtfs-realtime-bindings`, print the feed header timestamp in
     `Europe/Stockholm` and the number of entities.
   - **Historical static:** download the static GTFS for one date from KoDa
     (`https://api.koda.trafiklab.se/KoDa/api/v2/gtfs-static/krono?date=YYYY-MM-DD&key=...`,
     using the KoDa key). Handle HTTP 202 by polling every 30 seconds. Detect the archive
     format from magic bytes (zip or 7z). Save under `data/static/`. Then report: number
     of trips scheduled on that service date (use `calendar_dates.txt`, which is how
     GTFS Regional defines service dates) and the number of `stop_times` rows for those
     trips.
   - Do **not** call the live GTFS Regional static endpoint. It is capped at 50 calls a
     month and KoDa's static endpoint covers the need.
5. Run both checks.

**Verify:** realtime returns entities, and the trip and stop-time counts are reported.
Show Marcus the stop-time count: it replaces the 30,000 to 60,000 per day estimate in
`docs/decisions.md` D-001. Update D-001 with the real figure once he confirms.

## Phase 4 · Hugging Face

1. **HUMAN:** Marcus creates a token with write access at
   huggingface.co/settings/tokens and adds it to `.env` as `HF_TOKEN`.
2. Read the KoDa license page at `https://www.trafiklab.se/api/our-apis/koda/license`
   and summarise its terms for Marcus. GTFS Regional data is CC0, but confirm KoDa's
   terms before choosing the dataset license. If unclear, ask.
3. Write `scripts/setup_hf.py`:
   - `whoami()` to get the username.
   - `create_repo(repo_id=f"{user}/kronoberg-transit-punctuality", repo_type="dataset",
     private=False, exist_ok=True)`.
   - Upload a `README.md` dataset card: YAML header with the confirmed license and tags
     (`gtfs`, `public-transport`, `sweden`), a description, source attribution, status
     "Work in progress", and the planned schema as "to be defined".
   - List the repo files to confirm.
4. **HUMAN:** Marcus adds `HF_DATASET_REPO=<user>/kronoberg-transit-punctuality` to
   `.env` (tell him the exact value).

**Verify:** the dataset URL loads and shows the card.

## Phase 5 · Google Sheets

1. **HUMAN:** in console.cloud.google.com, Marcus:
   1. Creates a project named `kronoberg-transit`.
   2. Enables **Google Sheets API** and **Google Drive API**.
   3. Creates a service account named `pipeline-writer` (no roles needed).
   4. Adds a JSON key and saves it as `secrets/google_service_account.json` in this repo.
   5. In **his own** Google Drive, creates a blank spreadsheet named
      `Kronoberg transit punctuality` and shares it with the service account's email as
      Editor. It must be his file, not one the service account creates, so that the
      Claude Project's Drive connector and Looker Studio can see it.
   6. Copies the Sheet ID (the part of the URL between `/d/` and `/edit`) into `.env` as
      `GOOGLE_SHEET_ID`.
2. Write `scripts/setup_sheet.py`:
   - Open the sheet by key with the service account file.
   - Set the spreadsheet locale to `en_US` and time zone to `Europe/Stockholm`. A Swedish
     locale parses `0.85` as text because it expects a decimal comma, which silently
     breaks numeric columns.
   - Create the tabs below with their header rows, freeze row 1.
   - Delete the default tab (it may be named `Sheet1` or `Blad1`).
   - Idempotent: if a tab already exists with matching headers, leave it. If headers
     differ, stop and report rather than overwrite.
   - Append one `run_log` row: `run_type=setup`, `status=ok`.
   - Add a `--ping` mode that only appends a `run_log` row, for use in CI.
   - Write numbers as numeric types, dates as ISO `YYYY-MM-DD`, with
     `value_input_option="USER_ENTERED"`.
3. Run it, then read back and print each tab's header row.

| Tab | Columns |
|---|---|
| `route_daily` | service_date, route_id, route_name, scheduled_departures, observed_departures, coverage_pct, on_time_pct, late_pct, early_pct, cancelled_trips, median_delay_s, p90_delay_s |
| `stop_hotspots` | period_start, period_end, stop_id, stop_name, observed_departures, median_delay_s, p90_delay_s, late_pct |
| `hour_of_day` | period_start, period_end, day_type, hour, observed_departures, on_time_pct, median_delay_s |
| `data_quality` | service_date, feed, expected_snapshots, received_snapshots, missing_hours, trips_scheduled, trips_observed, notes |
| `run_log` | run_ts_utc, run_type, service_date, stage, status, rows_in, rows_out, duration_s, message |

These schemas are a starting point and will evolve. `docs/data_dictionary.md` must
mirror them.

**Verify:** all five tabs exist with correct headers and `run_log` has the setup row.

## Phase 6 · GitHub Actions secrets and smoke workflow

1. Set secrets without ever echoing a value. `tr -d '\r'` handles Windows line endings:
   ```
   for name in TRAFIKLAB_KODA_KEY TRAFIKLAB_GTFS_STATIC_KEY TRAFIKLAB_GTFS_RT_KEY HF_TOKEN GOOGLE_SHEET_ID; do
     gh secret set "$name" --body "$(grep "^$name=" .env | cut -d= -f2- | tr -d '\r')"
   done
   gh secret set GOOGLE_SERVICE_ACCOUNT_JSON < secrets/google_service_account.json
   ```
   The Sheet ID is a secret so it is masked in the public Actions logs; access to the
   Sheet itself is controlled by its sharing settings (restricted to the owner and the
   pipeline service account).
2. Set non-secret configuration as repository variables:
   ```
   for name in HF_DATASET_REPO; do
     gh variable set "$name" --body "$(grep "^$name=" .env | cut -d= -f2- | tr -d '\r')"
   done
   ```
3. `gh secret list` and `gh variable list` (names only) to confirm.
4. Write `.github/workflows/smoke.yml`:
   - Trigger: `workflow_dispatch` with an optional `date` input.
   - `permissions: contents: read`, `timeout-minutes: 30`, `ubuntu-latest`.
   - Steps: checkout, `astral-sh/setup-uv`, `uv sync --frozen`, write
     `GOOGLE_SERVICE_ACCOUNT_JSON` to `$RUNNER_TEMP/sa.json` and point
     `GOOGLE_SERVICE_ACCOUNT_FILE` at it, run `scripts/koda_check.py` (with the date
     input if given), run `scripts/setup_sheet.py --ping` with `run_type=smoke-ci`.
   - Never print secret values in logs.
5. Commit (`ci: add smoke workflow`), push, then:
   ```
   gh workflow run smoke.yml
   gh run watch
   ```

**Verify:** the run succeeds and a `smoke-ci` row appears in `run_log`.

## Phase 7 · Connect the Claude Project

**HUMAN:** in the claude.ai Project, Marcus:

1. Adds the GitHub repo to project knowledge, selecting `CLAUDE.md`, `README.md` and
   `docs/`. After any change to those files, he clicks **Sync**.
2. Tests the Drive connector by asking the Project to read the `run_log` tab of
   `Kronoberg transit punctuality`. It should see the `setup` and `smoke-ci` rows.

## Phase 8 · Wrap-up

1. `git mv HANDOFF.md docs/SETUP.md`, then append a **Setup results** section: date,
   tool versions, each phase's verification outcome, the KoDa check date used, the trip
   and stop-time counts, the Hugging Face dataset URL, and the smoke run result. No
   secret values and no Sheet ID.
2. Commit (`docs: record setup results`) and push.
3. Give Marcus a final checklist of what is done and anything left open.

## Definition of done

- [ ] Public repo on GitHub, `main` pushed, hook active and tested
- [ ] No attribution in any commit message (`git log --format=%B` is clean)
- [ ] KoDa check passes for `krono`
- [ ] Realtime smoke check passes; trip and stop-time counts recorded in D-001
- [ ] Hugging Face dataset repo exists with a card and a confirmed license
- [ ] Google Sheet has five tabs with headers, locale `en_US`
- [ ] Actions secrets and variables set; smoke workflow green
- [ ] `docs/SETUP.md` records the results

## Next session (not this one)

Build `src/kronoberg_transit/fetch_koda.py`: a resumable KoDa downloader with at most two
requests in flight and 202 polling. Then pull one real day of `krono` TripUpdates and
inspect how the feed behaves, so Marcus can settle the OPEN "observed time" definition in
`docs/definitions.md` before any transform logic is written.

---

## Setup results

**Date:** 2026-09-21

**Tool versions:** git 2.54.0.windows.1, GitHub CLI 2.101.0 (installed via winget during
this run), uv 0.12.5.

### Phase 0 · Preflight

Passed. All three tools reported versions; git identity was already set
(`user.name`/`user.email` present, unchanged). `~/.claude/settings.json` confirmed
`attribution.commit` and `attribution.pr` are empty strings, `sessionUrl: false`.

### Phase 1 · Local repository

Passed. `uv init --package` did not overwrite any existing file (verified by hash diff
before/after). Repo initialized on `main`, `core.hooksPath` set. `.githooks/commit-msg`
staged with mode `100755`. Hook tested with a throwaway commit containing a
`Co-Authored-By: Claude` trailer — the hook stripped it. `git log --format=%B` is clean
of attribution across all commits.

### Phase 2 · GitHub

Passed. Repo created and pushed: https://github.com/Iskarioth/kronoberg-transit
(public, HTTPS auth). Topics added. `origin/main` matched local `main`.

### Phase 3 · Trafiklab

Passed. KoDa check succeeded for `krono` (service date 2026-09-07, hour 08 TripUpdates).
Realtime smoke check returned 61 entities (the live feed required an
`Accept-Encoding: gzip, deflate` header, missing from the initial script — fixed).
Historical static smoke check (KoDa, service date **2026-09-07**): **2,162 trips
scheduled**, **49,204 `stop_times` rows** — recorded in D-001, replacing the earlier
30,000-60,000/day estimate, confirmed with Marcus before updating.

### Phase 4 · Hugging Face

Passed. KoDa license confirmed as CC0 1.0 (same as GTFS Regional), confirmed with
Marcus. Dataset repo created:
https://huggingface.co/datasets/Traumenteize/kronoberg-transit-punctuality, with a
README card (license, tags, source attribution, status "work in progress"). A bug in
the initial card (GitHub link used the wrong owner) was caught and fixed before
Marcus saw it.

### Phase 5 · Google Sheets

Passed. Locale set to `en_US`, time zone `Europe/Stockholm`. All five tabs created with
correct headers, row 1 frozen: `route_daily`, `stop_hotspots`, `hour_of_day`,
`data_quality`, `run_log`. Default `Sheet1` tab deleted. Idempotency verified on a
second run. One data-entry issue was caught and fixed along the way: `GOOGLE_SHEET_ID`
initially held extra URL text instead of the bare ID.

### Phase 6 · GitHub Actions secrets and smoke workflow

Passed. Five secrets and two variables set (names only, no values recorded here).
`smoke.yml` workflow added and run manually via `gh workflow run`; it passed in 12s
(checkout, setup-uv, `uv sync --frozen`, KoDa check, Sheets `--ping`). One bug was
caught and fixed: two Trafiklab keys had trailing whitespace in `.env` that broke the
KoDa request URL when passed to GitHub as a secret (local runs were unaffected because
`uv run --env-file` trims it) — both secrets were re-set clean.

### Phase 7 · Connect the Claude Project

Handled by Marcus outside this session: folder-level project knowledge sync wasn't
available, so he connected a filesystem MCP server with repo access instead and will
verify the Project can read the Sheet directly.

### Definition of done

- [x] Public repo on GitHub, `main` pushed, hook active and tested
- [x] No attribution in any commit message (`git log --format=%B` is clean)
- [x] KoDa check passes for `krono`
- [x] Realtime smoke check passes; trip and stop-time counts recorded in D-001
- [x] Hugging Face dataset repo exists with a card and a confirmed license
- [x] Google Sheet has five tabs with headers, locale `en_US`
- [x] Actions secrets and variables set; smoke workflow green
- [x] `docs/SETUP.md` records the results
