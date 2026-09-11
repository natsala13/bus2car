import csv
import json

import httpx
from click.testing import CliRunner

from routes_api.api import GoogleMapsClient
from routes_api.cache import ResponseCache
from routes_api.routes import build_route_request, cli, get_route_time, parse_waypoint


def mocked_route_response() -> dict:
    return {
        "routes": [
            {
                "duration": "830s",
                "distanceMeters": 2404,
                "legs": [
                    {
                        "startLocation": {"latLng": {"latitude": 32.07, "longitude": 34.79}},
                        "endLocation": {"latLng": {"latitude": 32.06, "longitude": 34.78}},
                        "steps": [{"travelMode": "TRANSIT"}],
                    }
                ],
            }
        ],
        "geocodingResults": {
            "origin": {"placeId": "place-a", "type": ["street_address"]},
            "destination": {"placeId": "place-b", "type": ["establishment"]},
        },
    }


def test_parse_waypoints() -> None:
    assert parse_waypoint("place_id:abc") == {"placeId": "abc"}
    assert parse_waypoint("32.1,34.8")["location"]["latLng"]["latitude"] == 32.1
    assert parse_waypoint("Tel Aviv") == {"address": "Tel Aviv"}


def test_bus_request_has_bus_preference_and_time() -> None:
    request, mode, preference = build_route_request(
        "A", "B", "bus", "2026-09-14T05:00:00Z"
    )
    assert mode == "TRANSIT"
    assert preference is None
    assert request["departureTime"] == "2026-09-14T05:00:00Z"
    assert request["transitPreferences"] == {"allowedTravelModes": ["BUS"]}


def test_get_route_time_saves_mocked_results(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["travelMode"] == "TRANSIT"
        return httpx.Response(200, json=mocked_route_response())

    route_path = tmp_path / "routes.csv"
    geocode_path = tmp_path / "geocoding.csv"
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = GoogleMapsClient(
            "test-key",
            http_client=http_client,
            cache=ResponseCache(tmp_path / "cache"),
        )
        result = get_route_time(
            "A",
            "B",
            "bus",
            "2026-09-14T08:00:00+03:00",
            client=client,
            route_data_path=route_path,
            geocoding_data_path=geocode_path,
        )

    assert result.duration_seconds == 830
    assert result.source_place_id == "place-a"
    with route_path.open(encoding="utf-8") as csv_file:
        saved_routes = list(csv.DictReader(csv_file))
    assert saved_routes[0]["transport_type"] == "bus"
    assert saved_routes[0]["departure_time_utc"] == "2026-09-14T05:00:00Z"
    with geocode_path.open(encoding="utf-8") as csv_file:
        saved_geocodes = list(csv.DictReader(csv_file))
    assert {row["place_id"] for row in saved_geocodes} == {"place-a", "place-b"}


def test_cli_uses_mocked_function(monkeypatch) -> None:
    arguments = {}

    def fake_get_route_time(*args, **kwargs):
        arguments.update(kwargs)
        return get_route_time_result()

    monkeypatch.setattr("routes_api.routes.get_route_time", fake_get_route_time)
    result = CliRunner().invoke(
        cli,
        ["A", "B", "--transport", "bike", "--no-save", "--no-cache"],
    )
    assert result.exit_code == 0
    assert '"duration_seconds": 60' in result.output
    assert arguments["use_cache"] is False


def get_route_time_result():
    from routes_api.routes import RouteResult

    return RouteResult(
        measured_at_utc="2026-09-11T00:00:00Z",
        source="A",
        destination="B",
        source_place_id="",
        destination_place_id="",
        departure_time_utc=None,
        transport_type="bike",
        google_travel_mode="BICYCLE",
        routing_preference=None,
        duration_seconds=60,
        distance_meters=500,
        warnings=(),
    )
