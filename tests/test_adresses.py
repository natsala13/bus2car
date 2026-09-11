import csv

import httpx
from click.testing import CliRunner

from routes_api.adresses import cli, resolve_address
from routes_api.api import GoogleMapsClient
from routes_api.cache import ResponseCache


def test_resolve_address_with_mocked_google_call(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "%D7%94%D7%A1%D7%95%D7%9C%D7%9C%D7%99%D7%9D" in str(request.url)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "placeId": "place-home",
                        "formattedAddress": "הסוללים 3, תל אביב-יפו, ישראל",
                        "location": {"latitude": 32.07, "longitude": 34.79},
                        "granularity": "ROOFTOP",
                        "types": ["street_address"],
                    }
                ]
            },
        )

    csv_path = tmp_path / "geocoding.csv"
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = GoogleMapsClient(
            "test-key",
            http_client=http_client,
            cache=ResponseCache(tmp_path / "cache"),
        )
        rows = resolve_address("הסוללים 3", client=client, data_path=csv_path)

    assert rows[0]["place_id"] == "place-home"
    with csv_path.open(encoding="utf-8") as csv_file:
        saved = list(csv.DictReader(csv_file))
    assert saved[0]["granularity"] == "ROOFTOP"


def test_cli_no_cache_flag_is_forwarded(monkeypatch) -> None:
    arguments = {}

    def fake_resolve(address, **kwargs):
        arguments.update(kwargs)
        return [{"query": address, "place_id": "place-a"}]

    monkeypatch.setattr("routes_api.adresses.resolve_address", fake_resolve)
    result = CliRunner().invoke(cli, ["Tel Aviv", "--no-cache", "--no-save"])

    assert result.exit_code == 0
    assert arguments["use_cache"] is False
