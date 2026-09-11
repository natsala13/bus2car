"""Resolve addresses and persist normalized Google geocoding results.

The filename intentionally follows the project-requested ``adresses`` spelling.
"""

from __future__ import annotations

import json
import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping

import click
from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class Address(BaseModel):
    """A named benchmark point represented in exactly one supported form."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    label: str | None = None
    address: str | None = None
    place_id: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    formatted_address: str | None = None
    verified: bool = False
    verification_provider: str | None = None
    verified_at_utc: str | None = None

    @model_validator(mode="after")
    def validate_location_form(self) -> Address:
        has_address = bool(self.address and self.address.strip())
        has_place_id = bool(self.place_id and self.place_id.strip())
        has_latitude = self.latitude is not None
        has_longitude = self.longitude is not None
        if has_latitude != has_longitude:
            raise ValueError("latitude and longitude must be provided together")
        if sum((has_address, has_place_id, has_latitude and has_longitude)) != 1:
            raise ValueError(
                "provide exactly one of address, place_id, or latitude/longitude"
            )
        if self.verified and not (
            has_place_id
            and self.formatted_address
            and self.verification_provider
            and self.verified_at_utc
        ):
            raise ValueError(
                "verified addresses require place_id, formatted_address, "
                "verification_provider, and verified_at_utc"
            )
        return self

    def to_waypoint(self) -> dict[str, Any]:
        if self.address:
            return {"address": self.address}
        if self.place_id:
            return {"placeId": self.place_id}
        return {
            "location": {
                "latLng": {
                    "latitude": self.latitude,
                    "longitude": self.longitude,
                }
            }
        }

    @property
    def display_value(self) -> str:
        if self.address:
            return self.address
        if self.place_id:
            return f"place_id:{self.place_id}"
        return f"{self.latitude},{self.longitude}"


class AddressVerificationError(ValueError):
    """Raised when Google does not return a specific Tel Aviv-area match."""


def _address_id(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if slug:
        return slug
    # Hebrew-only labels do not transliterate through the standard library.
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"address-{digest}"


def _tel_aviv_query(address: str) -> str:
    normalized = address.casefold()
    named_localities = ("tel aviv", "תל אביב", "ramat gan", "רמת גן")
    if any(locality in normalized for locality in named_localities):
        return address.strip()
    return f"{address.strip()}, Tel Aviv-Yafo, Israel"


def add_new_adress(
    address: str,
    *,
    identifier: str | None = None,
    label: str | None = None,
    client: GoogleMapsClient | None = None,
    use_cache: bool = True,
    data_path: Path = GEOCODING_RESULTS_PATH,
) -> Address:
    """Resolve, verify, and return a canonical Tel Aviv-area benchmark address.

    The misspelled function name is retained to match the public module name and
    requested API. A verified address routes by Place ID, while keeping its original
    label and Google's formatted address as auditable metadata.
    """

    if not address.strip():
        raise AddressVerificationError("address cannot be empty")
    query = _tel_aviv_query(address)
    rows = resolve_address(
        query,
        client=client,
        save=True,
        data_path=data_path,
        language_code="en",
        region_code="IL",
        use_cache=use_cache,
    )
    accepted_types = {
        "street_address",
        "premise",
        "establishment",
        "point_of_interest",
        "university",
    }
    for row in rows:
        formatted = str(row.get("formatted_address", ""))
        types = set(str(row.get("types", "")).split("|"))
        normalized_formatted = formatted.casefold()
        in_tel_aviv_area = any(
            locality in normalized_formatted
            for locality in ("tel aviv", "תל אביב", "ramat gan", "רמת גן")
        )
        if row.get("place_id") and in_tel_aviv_area and types.intersection(accepted_types):
            return Address(
                id=identifier or _address_id(address),
                label=label or address.strip(),
                place_id=str(row["place_id"]),
                formatted_address=formatted,
                verified=True,
                verification_provider=str(row["provider"]),
                verified_at_utc=str(row["resolved_at_utc"]),
            )
    raise AddressVerificationError(
        f"Google did not return a specific Tel Aviv-area match for {address!r}"
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
    use_cache: bool = True,
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
            use_cache=use_cache,
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
@click.option("--no-cache", is_flag=True, help="Bypass cache reads and writes.")
def cli(
    address: str,
    language_code: str,
    region_code: str,
    no_save: bool,
    no_cache: bool,
) -> None:
    """Resolve ADDRESS to Google Place IDs and coordinates."""

    try:
        rows = resolve_address(
            address,
            language_code=language_code,
            region_code=region_code,
            save=not no_save,
            use_cache=not no_cache,
        )
    except (GoogleApiError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cli()
