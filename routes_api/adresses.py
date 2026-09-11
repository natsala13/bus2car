"""Resolve addresses and persist normalized Google geocoding results.

The filename intentionally follows the project-requested ``adresses`` spelling.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import click

from routes_api.api import GoogleApiError, GoogleMapsClient
from routes_api.utils import GEOCODING_RESULTS_PATH, append_csv_row, utc_now_iso


GEOCODING_FIELDS = (
    "resolved_at_utc",
    "provider",
    "query",
    "place_id",
    "formatted_address",
    "latitude",
    "longitude",
    "granularity",
    "types",
)


def save_geocoding_results(
    rows: Iterable[Mapping[str, object]],
    *,
    path: Path = GEOCODING_RESULTS_PATH,
) -> None:
    for row in rows:
        append_csv_row(path, GEOCODING_FIELDS, row)


def normalize_geocoding_result(
    query: str,
    result: Mapping[str, Any],
    *,
    provider: str = "google_geocoding_v4",
) -> dict[str, object]:
    location = result.get("location") or {}
    return {
        "resolved_at_utc": utc_now_iso(),
        "provider": provider,
        "query": query,
        "place_id": result.get("placeId", ""),
        "formatted_address": result.get("formattedAddress", ""),
        "latitude": location.get("latitude", ""),
        "longitude": location.get("longitude", ""),
        "granularity": result.get("granularity", ""),
        "types": "|".join(result.get("types", [])),
    }


def resolve_address(
    address: str,
    *,
    client: GoogleMapsClient | None = None,
    save: bool = True,
    data_path: Path = GEOCODING_RESULTS_PATH,
    language_code: str = "en",
    region_code: str = "IL",
) -> list[dict[str, object]]:
    """Resolve an address through Geocoding API v4 and optionally save all matches."""

    if not address.strip():
        raise ValueError("address cannot be empty")

    owns_client = client is None
    api_client = client or GoogleMapsClient()
    try:
        payload = api_client.geocode_address(
            address,
            language_code=language_code,
            region_code=region_code,
        )
    finally:
        if owns_client:
            api_client.close()

    rows = [normalize_geocoding_result(address, result) for result in payload.get("results", [])]
    if not rows:
        raise GoogleApiError(f"Google found no geocoding result for {address!r}")
    if save:
        save_geocoding_results(rows, path=data_path)
    return rows


def route_geocoding_rows(
    source: str,
    destination: str,
    response: Mapping[str, Any],
) -> list[dict[str, object]]:
    """Normalize embedded ``geocodingResults`` from a Routes response."""

    geocoding = response.get("geocodingResults") or {}
    routes = response.get("routes") or []
    leg = (routes[0].get("legs") or [{}])[0] if routes else {}
    endpoints = (
        ("origin", source, leg.get("startLocation") or {}),
        ("destination", destination, leg.get("endLocation") or {}),
    )
    rows: list[dict[str, object]] = []
    for key, query, endpoint in endpoints:
        result = geocoding.get(key)
        if not result:
            continue
        location = endpoint.get("latLng") or {}
        rows.append(
            {
                "resolved_at_utc": utc_now_iso(),
                "provider": "google_routes_v2",
                "query": query,
                "place_id": result.get("placeId", ""),
                "formatted_address": "",
                "latitude": location.get("latitude", ""),
                "longitude": location.get("longitude", ""),
                "granularity": "",
                "types": "|".join(result.get("type", [])),
            }
        )
    return rows


@click.command()
@click.argument("address")
@click.option("--language", "language_code", default="en", show_default=True)
@click.option("--region", "region_code", default="IL", show_default=True)
@click.option("--no-save", is_flag=True, help="Print results without writing data CSV.")
def cli(address: str, language_code: str, region_code: str, no_save: bool) -> None:
    """Resolve ADDRESS to Google Place IDs and coordinates."""

    try:
        rows = resolve_address(
            address,
            language_code=language_code,
            region_code=region_code,
            save=not no_save,
        )
    except (GoogleApiError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cli()
