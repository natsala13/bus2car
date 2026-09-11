import csv

import httpx
import pytest
from click.testing import CliRunner

from routes_api.adresses import (
    AddressVerificationError,
    add_new_adress,
    cli,
    resolve_address,
)
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


def test_add_new_adress_qualifies_and_signs_tel_aviv_match(monkeypatch) -> None:
    observed = {}

    def fake_resolve(query, **kwargs):
        observed["query"] = query
        return [
            {
                "resolved_at_utc": "2026-09-11T00:00:00Z",
                "provider": "google_geocoding_v4",
                "place_id": "place-a",
                "formatted_address": "1 Example St, Tel Aviv-Yafo, Israel",
                "types": "street_address",
            }
        ]

    monkeypatch.setattr("routes_api.adresses.resolve_address", fake_resolve)
    result = add_new_adress("Example 1", identifier="example-1", client=object())

    assert observed["query"] == "Example 1, Tel Aviv-Yafo, Israel"
    assert result.place_id == "place-a"
    assert result.verified is True
    assert result.id == "example-1"


def test_add_new_adress_rejects_non_tel_aviv_match(monkeypatch) -> None:
    monkeypatch.setattr(
        "routes_api.adresses.resolve_address",
        lambda *args, **kwargs: [
            {
                "resolved_at_utc": "2026-09-11T00:00:00Z",
                "provider": "google_geocoding_v4",
                "place_id": "wrong-place",
                "formatted_address": "1 Example St, Haifa, Israel",
                "types": "street_address",
            }
        ],
    )
    with pytest.raises(AddressVerificationError, match="Tel Aviv"):
        add_new_adress("Example 1", client=object())


def test_add_new_adress_accepts_explicit_ramat_gan_match(monkeypatch) -> None:
    observed = {}

    def fake_resolve(query, **kwargs):
        observed["query"] = query
        return [
            {
                "resolved_at_utc": "2026-09-11T00:00:00Z",
                "provider": "google_geocoding_v4",
                "place_id": "place-tuval",
                "formatted_address": "Tuval St 13, Ramat Gan, Israel",
                "types": "street_address",
            }
        ]

    monkeypatch.setattr("routes_api.adresses.resolve_address", fake_resolve)
    result = add_new_adress("Tuval 13, Ramat Gan", client=object())

    assert observed["query"] == "Tuval 13, Ramat Gan"
    assert result.place_id == "place-tuval"
    assert result.verified is True
