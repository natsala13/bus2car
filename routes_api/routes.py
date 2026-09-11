"""Compute one Google route and persist its normalized travel time."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import click

from routes_api.adresses import route_geocoding_rows, save_geocoding_results
from routes_api.api import GoogleApiError, GoogleMapsClient
from routes_api.utils import (
    GEOCODING_RESULTS_PATH,
    ROUTE_TIMES_PATH,
    append_csv_row,
    normalize_departure_time,
    parse_google_duration,
    utc_now_iso,
)


ROUTE_FIELDS = (
    "measured_at_utc",
    "provider",
    "source",
    "destination",
    "source_place_id",
    "destination_place_id",
    "departure_time_utc",
    "transport_type",
    "google_travel_mode",
    "routing_preference",
    "duration_seconds",
    "distance_meters",
)

ROUTES_FIELD_MASK = ",".join(
    (
        "routes.duration",
        "routes.distanceMeters",
        "routes.warnings",
        "routes.legs.startLocation",
        "routes.legs.endLocation",
        "routes.legs.steps.travelMode",
        "routes.legs.steps.transitDetails",
        "geocodingResults.origin.placeId",
        "geocodingResults.origin.type",
        "geocodingResults.destination.placeId",
        "geocodingResults.destination.type",
    )
)

TRANSPORT_MODES = {
    "car": "DRIVE",
    "drive": "DRIVE",
    "bus": "TRANSIT",
    "transit": "TRANSIT",
    "bike": "BICYCLE",
    "bicycle": "BICYCLE",
    "walk": "WALK",
}


@dataclass(frozen=True)
class RouteResult:
    measured_at_utc: str
    source: str
    destination: str
    source_place_id: str
    destination_place_id: str
    departure_time_utc: str | None
    transport_type: str
    google_travel_mode: str
    routing_preference: str | None
    duration_seconds: float
    distance_meters: int
    warnings: tuple[str, ...]


def parse_waypoint(value: str) -> dict[str, Any]:
    """Parse an address, ``place_id:...``, or ``latitude,longitude`` waypoint."""

    value = value.strip()
    if not value:
        raise ValueError("source and destination cannot be empty")
    if value.startswith("place_id:"):
        place_id = value.removeprefix("place_id:").strip()
        if not place_id:
            raise ValueError("place_id waypoint cannot be empty")
        return {"placeId": place_id}
    try:
        latitude_text, longitude_text = value.split(",", maxsplit=1)
        latitude = float(latitude_text)
        longitude = float(longitude_text)
    except ValueError:
        return {"address": value}
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("waypoint latitude or longitude is out of range")
    return {"location": {"latLng": {"latitude": latitude, "longitude": longitude}}}


def build_route_request(
    source: str,
    destination: str,
    transport: str,
    departure_time: str | None,
) -> tuple[dict[str, Any], str, str | None]:
    normalized_transport = transport.lower()
    try:
        google_mode = TRANSPORT_MODES[normalized_transport]
    except KeyError as exc:
        choices = ", ".join(sorted(TRANSPORT_MODES))
        raise ValueError(f"unsupported transport {transport!r}; choose one of: {choices}") from exc

    request: dict[str, Any] = {
        "origin": parse_waypoint(source),
        "destination": parse_waypoint(destination),
        "travelMode": google_mode,
        "languageCode": "en",
        "units": "METRIC",
    }
    routing_preference: str | None = None
    if google_mode == "DRIVE":
        routing_preference = "TRAFFIC_AWARE"
        request["routingPreference"] = routing_preference
    if google_mode == "TRANSIT" and normalized_transport == "bus":
        request["transitPreferences"] = {"allowedTravelModes": ["BUS"]}
    if departure_time is not None and google_mode in {"DRIVE", "TRANSIT"}:
        request["departureTime"] = departure_time
    return request, google_mode, routing_preference


def _place_id(geocoding: Mapping[str, Any], endpoint: str, waypoint: str) -> str:
    embedded = (geocoding.get(endpoint) or {}).get("placeId", "")
    if embedded:
        return embedded
    if waypoint.startswith("place_id:"):
        return waypoint.removeprefix("place_id:").strip()
    return ""


def get_route_time(
    source: str,
    destination: str,
    transport: str,
    departure_time: str | None = None,
    *,
    client: GoogleMapsClient | None = None,
    save: bool = True,
    route_data_path: Path = ROUTE_TIMES_PATH,
    geocoding_data_path: Path = GEOCODING_RESULTS_PATH,
) -> RouteResult:
    """Compute one route and return its duration, distance, and provenance."""

    normalized_departure = normalize_departure_time(departure_time)
    request, google_mode, routing_preference = build_route_request(
        source,
        destination,
        transport,
        normalized_departure,
    )

    owns_client = client is None
    api_client = client or GoogleMapsClient()
    try:
        response = api_client.compute_routes(request, field_mask=ROUTES_FIELD_MASK)
    finally:
        if owns_client:
            api_client.close()

    routes = response.get("routes") or []
    if not routes:
        raise GoogleApiError("Google returned no practical route")
    route = routes[0]
    geocoding = response.get("geocodingResults") or {}
    result = RouteResult(
        measured_at_utc=utc_now_iso(),
        source=source,
        destination=destination,
        source_place_id=_place_id(geocoding, "origin", source),
        destination_place_id=_place_id(geocoding, "destination", destination),
        departure_time_utc=normalized_departure,
        transport_type=transport.lower(),
        google_travel_mode=google_mode,
        routing_preference=routing_preference,
        duration_seconds=parse_google_duration(route["duration"]),
        distance_meters=int(route["distanceMeters"]),
        warnings=tuple(route.get("warnings", [])),
    )
    if save:
        route_row = asdict(result)
        route_row["provider"] = "google_routes_v2"
        route_row.pop("warnings")
        append_csv_row(route_data_path, ROUTE_FIELDS, route_row)
        address_rows = route_geocoding_rows(source, destination, response)
        save_geocoding_results(address_rows, path=geocoding_data_path)
    return result


@click.command()
@click.argument("source")
@click.argument("destination")
@click.option(
    "--transport",
    required=True,
    type=click.Choice(sorted(TRANSPORT_MODES), case_sensitive=False),
)
@click.option(
    "--time",
    "departure_time",
    default=None,
    help="Timezone-aware ISO 8601 departure time; omitted means Google's current time.",
)
@click.option("--no-save", is_flag=True, help="Print the result without writing data CSVs.")
def cli(
    source: str,
    destination: str,
    transport: str,
    departure_time: str | None,
    no_save: bool,
) -> None:
    """Measure one route from SOURCE to DESTINATION."""

    try:
        result = get_route_time(
            source,
            destination,
            transport,
            departure_time,
            save=not no_save,
        )
    except (GoogleApiError, KeyError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cli()
