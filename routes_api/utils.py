"""Small shared utilities for routing and geocoding modules."""

from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
GEOCODING_RESULTS_PATH = DATA_DIR / "geocoding_results.csv"
ROUTE_TIMES_PATH = DATA_DIR / "route_times.csv"

_GOOGLE_DURATION_PATTERN = re.compile(r"^(?P<seconds>\d+(?:\.\d+)?)s$")


def utc_now_iso() -> str:
    """Return the current UTC time in stable RFC 3339 form."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_departure_time(value: str | datetime | None) -> str | None:
    """Normalize a timezone-aware timestamp for Google, or preserve ``None``.

    Naive timestamps are rejected because they make benchmark runs ambiguous.
    """

    if value is None:
        return None

    if isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(
                "departure time must be ISO 8601, for example "
                "2026-09-14T08:00:00+03:00"
            ) from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise TypeError("departure time must be a string, datetime, or None")

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("departure time must include a UTC offset or Z suffix")

    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_google_duration(value: str) -> float:
    """Convert a Google protobuf duration such as ``596s`` to seconds."""

    match = _GOOGLE_DURATION_PATTERN.fullmatch(value)
    if not match:
        raise ValueError(f"invalid Google duration: {value!r}")
    return float(match.group("seconds"))


def append_csv_row(
    path: Path,
    fieldnames: Sequence[str],
    row: Mapping[str, object],
) -> None:
    """Append one row and create the CSV header on first use."""

    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
        if needs_header:
            writer.writeheader()
        writer.writerow(row)
