"""Checksum and streaming-download helpers for pinned anatomical sources."""

import hashlib
from pathlib import Path
import shutil
import urllib.request


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def download(url: str, target: Path) -> None:
    partial = target.with_suffix(target.suffix + ".partial")
    print(f"Downloading {target.name}", flush=True)
    with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as output:
        shutil.copyfileobj(response, output)
    partial.replace(target)
