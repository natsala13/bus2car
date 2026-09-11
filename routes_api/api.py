"""Low-level authenticated HTTP access to Google Maps Platform APIs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

from routes_api.cache import ResponseCache


ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
ROUTE_MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
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
        cache: ResponseCache | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = api_key or get_api_key()
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(timeout=timeout)
        self.cache = cache or ResponseCache()

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
        use_cache: bool = True,
    ) -> dict[str, Any]:
        payload = self._request_json(
            "POST",
            ROUTES_URL,
            field_mask=field_mask,
            json_body=dict(request),
            use_cache=use_cache,
        )
        if not isinstance(payload, dict):
            raise GoogleApiError("Google Routes returned an unexpected response shape")
        return payload

    def compute_route_matrix(
        self,
        request: Mapping[str, Any],
        *,
        field_mask: str,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        payload = self._request_json(
            "POST",
            ROUTE_MATRIX_URL,
            field_mask=field_mask,
            json_body=dict(request),
            use_cache=use_cache,
        )
        if not isinstance(payload, list) or not all(
            isinstance(element, dict) for element in payload
        ):
            raise GoogleApiError("Google Route Matrix returned an unexpected response shape")
        return payload

    def geocode_address(
        self,
        address: str,
        *,
        language_code: str = "en",
        region_code: str = "IL",
        use_cache: bool = True,
    ) -> dict[str, Any]:
        encoded_address = quote(address, safe="")
        field_mask = (
            "results.placeId,results.formattedAddress,results.location,"
            "results.granularity,results.types"
        )
        payload = self._request_json(
            "GET",
            f"{GEOCODING_ADDRESS_URL}/{encoded_address}",
            field_mask=field_mask,
            params={"languageCode": language_code, "regionCode": region_code},
            use_cache=use_cache,
        )
        if not isinstance(payload, dict):
            raise GoogleApiError("Google Geocoding returned an unexpected response shape")
        return payload

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        field_mask: str,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        use_cache: bool,
    ) -> Any:
        cache_request = {
            "method": method,
            "url": url,
            "field_mask": field_mask,
            "json": json_body,
            "params": params,
        }
        if use_cache:
            cached = self.cache.get(cache_request)
            if cached is not None:
                return cached

        response = self.http_client.request(
            method,
            url,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": field_mask,
            },
            json=dict(json_body) if json_body is not None else None,
            params=dict(params) if params is not None else None,
        )
        payload = self._decode_response(response)
        if use_cache:
            self.cache.set(cache_request, payload)
        return payload

    @staticmethod
    def _decode_response(response: httpx.Response) -> Any:
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
        return payload
