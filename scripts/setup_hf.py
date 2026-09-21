#!/usr/bin/env python3
"""
setup_hf.py - create the Hugging Face dataset repo for kronoberg-transit and
publish its initial README dataset card.

Usage:
    uv run --env-file .env python scripts/setup_hf.py
"""

import sys

from huggingface_hub import HfApi

DATASET_CARD = """\
---
license: cc0-1.0
tags:
  - gtfs
  - public-transport
  - sweden
---

# Kronoberg transit punctuality

Cleaned, stop-level punctuality data for public transport in Kronoberg county, Sweden
(Länstrafiken Kronoberg, Trafiklab operator code `krono`), built from historical
GTFS-Realtime archives.

**Status:** work in progress. No data has been published yet.

## Source

- [Trafiklab](https://www.trafiklab.se/) GTFS Regional static and realtime feeds for
  Länstrafiken Kronoberg (`krono`).
- [KoDa](https://www.trafiklab.se/api/our-apis/koda/) (Kollektivtrafikdata), provided by
  Trafiklab in collaboration with RISE and Vinnova - historical GTFS and GTFS-Realtime
  archives.

Both sources are released under CC0 1.0 (public domain).

## Schema

To be defined once the transform stage is built. See the
[kronoberg-transit repo](https://github.com/Iskarioth/kronoberg-transit) for the
pipeline and metric definitions.
"""


def main() -> int:
    api = HfApi()

    user = api.whoami()["name"]
    repo_id = f"{user}/kronoberg-transit-punctuality"

    api.create_repo(repo_id=repo_id, repo_type="dataset", private=False, exist_ok=True)
    print(f"Dataset repo ready: https://huggingface.co/datasets/{repo_id}")

    api.upload_file(
        path_or_fileobj=DATASET_CARD.encode("utf-8"),
        path_in_repo="README.md",
        repo_id=repo_id,
        repo_type="dataset",
    )
    print("Uploaded README.md dataset card")

    files = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
    print(f"Repo files: {files}")

    print(f"\nSet HF_DATASET_REPO={repo_id} in .env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
