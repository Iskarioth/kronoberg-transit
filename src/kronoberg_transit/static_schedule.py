"""static_schedule.py - download, cache and extract KoDa historical GTFS static
schedules.

CLAUDE.md rule 4: prefer KoDa's historical static endpoint over the live GTFS
Regional static endpoint (Bronze key: 50 calls/month). Every download is
cached under `data/static/` and reused on subsequent calls for the same date.
"""

import io
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

KODA_STATIC_URL = "https://api.koda.trafiklab.se/KoDa/api/v2/gtfs-static/krono"
OPERATOR = "krono"
ZIP_MAGIC = b"PK\x03\x04"
SEVEN_ZIP_MAGIC = b"\x37\x7a\xbc\xaf\x27\x1c"
POLL_SECONDS = 30
MAX_WAIT_MINUTES = 20
TIMEOUT = 120
STATIC_DIR = Path("data/static")
STATIC_FILES = ["trips.txt", "stop_times.txt", "calendar_dates.txt", "routes.txt"]


def fetch_koda_static(date: str, key: str, operator: str = OPERATOR) -> bytes:
    url = f"{KODA_STATIC_URL}?date={date}&key={key}"
    print(f"Requesting KoDa historical static for {operator} on {date}")

    req = urllib.request.Request(url, headers={"User-Agent": "kronoberg-transit/1.0"})
    started = time.time()
    deadline = started + MAX_WAIT_MINUTES * 60

    while True:
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 202:
                waited = int(time.time() - started)
                print(f"  HTTP 202 - archive is being built (waited {waited}s)")
                if time.time() + POLL_SECONDS > deadline:
                    raise RuntimeError(
                        f"KoDa static archive still building after {MAX_WAIT_MINUTES} min"
                    ) from e
                time.sleep(POLL_SECONDS)
                continue
            detail = e.read()[:300].decode("utf-8", "replace").strip()
            raise RuntimeError(f"HTTP {e.code} {e.reason}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Connection failed: {e.reason}") from e


def extract_gtfs_text_files(body: bytes, names: list[str]) -> dict[str, str]:
    """Extract named text files from a zip or 7z GTFS static archive."""
    out: dict[str, str] = {}
    if body[:4] == ZIP_MAGIC:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            for name in names:
                out[name] = zf.read(name).decode("utf-8-sig")
    elif body[:6] == SEVEN_ZIP_MAGIC:
        import py7zr

        with tempfile.TemporaryDirectory() as tmpdir:
            with py7zr.SevenZipFile(io.BytesIO(body), mode="r") as archive:
                archive.extract(path=tmpdir, targets=names)
            for name in names:
                out[name] = (Path(tmpdir) / name).read_text(encoding="utf-8-sig")
    else:
        raise RuntimeError(f"Unrecognized archive format (first bytes: {body[:8]!r})")
    return out


def load_static_gtfs(
    svc_date: str,
    key: str | None,
    tmpdir: Path,
    extra_files: list[str] | None = None,
    static_dir: Path = STATIC_DIR,
    operator: str = OPERATOR,
) -> Path:
    """Return a directory containing this date's trips/stop_times/calendar_dates/routes
    (plus any extra_files, e.g. stops.txt) as plain CSV files, fetching from KoDa
    (or reusing a cached data/static/ archive) only if not already cached."""
    cached = None
    for ext in (".zip", ".7z"):
        candidate = static_dir / f"{operator}_{svc_date}{ext}"
        if candidate.exists():
            cached = candidate
            break

    if cached is not None:
        body = cached.read_bytes()
    else:
        if not key:
            raise SystemExit(
                f"No cached static GTFS for {svc_date} and TRAFIKLAB_KODA_KEY is not set."
            )
        body = fetch_koda_static(svc_date, key, operator=operator)
        static_dir.mkdir(parents=True, exist_ok=True)
        (static_dir / f"{operator}_{svc_date}.zip").write_bytes(body)

    wanted = list(STATIC_FILES) + [f for f in (extra_files or []) if f not in STATIC_FILES]
    files = extract_gtfs_text_files(body, wanted)
    out_dir = tmpdir / "static"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        # KoDa's static archives carry "\r\r\n" line endings, which trips up
        # DuckDB's CSV sniffer even though Python's csv module tolerates it.
        (out_dir / name).write_text(text.replace("\r", ""), encoding="utf-8")
    return out_dir
