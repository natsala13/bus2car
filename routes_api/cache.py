"""Short-lived, content-addressed cache for Google API responses."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping

from routes_api.utils import PROJECT_ROOT


DEFAULT_CACHE_DIR = PROJECT_ROOT / ".cache" / "google_maps"
DEFAULT_TTL_SECONDS = 24 * 60 * 60


class ResponseCache:
    """JSON file cache keyed by a canonical request description.

    The intentionally short default TTL only avoids duplicate development calls;
    it is not a historical datastore for Google Maps content.
    """

    def __init__(
        self,
        directory: Path = DEFAULT_CACHE_DIR,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        if ttl_seconds < 0:
            raise ValueError("cache TTL cannot be negative")
        self.directory = directory
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def key_for(request: Mapping[str, Any]) -> str:
        canonical = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def get(self, request: Mapping[str, Any]) -> Any | None:
        cache_path = self.directory / f"{self.key_for(request)}.json"
        try:
            with cache_path.open(encoding="utf-8") as cache_file:
                entry = json.load(cache_file)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

        created_at = entry.get("created_at_epoch")
        if not isinstance(created_at, (int, float)):
            return None
        if time.time() - created_at > self.ttl_seconds:
            return None
        return entry.get("response")

    def set(self, request: Mapping[str, Any], response: Any) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        cache_path = self.directory / f"{self.key_for(request)}.json"
        entry = {"created_at_epoch": time.time(), "response": response}
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.directory,
            prefix=".cache-",
            suffix=".json",
            delete=False,
        ) as temporary_file:
            json.dump(entry, temporary_file, ensure_ascii=False, sort_keys=True)
            temporary_path = Path(temporary_file.name)
        temporary_path.replace(cache_path)
