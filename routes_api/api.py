"""Low-level authenticated HTTP access to Google Maps Platform APIs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

import httpx
from dotenv import load_dotenv


ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
GEOCODING_ADDRESS_URL = "https://geocode.googleapis.com/v4/geocode/address"
DEFAULT_TIMEOUT_SECONDS = 30.0


class GoogleApiError(RuntimeError):
    """Raised when Google rejects a request or returns malformed data."""


def get_api_key(env_file: str | Path | None = None) -> str:
    """Load ``GOOGLE_MAPS_API_KEY`` from the environment or local ``.env``."""

    load_dotenv(dotenv_path=env_file, override=False)
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        raise GoogleApiError(
            "GOOGLE_MAPS_API_KEY is missing; put it in .env or the environment"
        )
    return api_key


class GoogleMapsClient:
    """Minimal injectable client for Routes and Geocoding APIs."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        http_client: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = api_key or get_api_key()
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(timeout=timeout)

    def __enter__(self) -> GoogleMapsClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self.http_client.close()

    def compute_routes(
        self,
        request: Mapping[str, Any],
        *,
        field_mask: str,
    ) -> dict[str, Any]:
        response = self.http_client.post(
            ROUTES_URL,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": field_mask,
            },
            json=dict(request),
        )
        return self._decode_response(response)

    def geocode_address(
        self,
        address: str,
        *,
        language_code: str = "en",
        region_code: str = "IL",
    ) -> dict[str, Any]:
        encoded_address = quote(address, safe="")
        response = self.http_client.get(
            f"{GEOCODING_ADDRESS_URL}/{encoded_address}",
            headers={
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": (
                    "results.placeId,results.formattedAddress,results.location,"
                    "results.granularity,results.types"
                ),
            },
            params={"languageCode": language_code, "regionCode": region_code},
        )
        return self._decode_response(response)

    @staticmethod
    def _decode_response(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise GoogleApiError(
                f"Google returned non-JSON HTTP {response.status_code}"
            ) from exc

        if response.is_error:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            message = error.get("message") or response.reason_phrase
            raise GoogleApiError(f"Google API HTTP {response.status_code}: {message}")
        if not isinstance(payload, dict):
            raise GoogleApiError("Google returned an unexpected response shape")
        return payload
