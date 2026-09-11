import httpx
import pytest

from routes_api.api import GoogleApiError, GoogleMapsClient
from routes_api.cache import ResponseCache


def test_compute_routes_uses_key_and_field_mask(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Goog-Api-Key"] == "test-key"
        assert request.headers["X-Goog-FieldMask"] == "routes.duration"
        return httpx.Response(200, json={"routes": [{"duration": "10s"}]})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = GoogleMapsClient(
            "test-key",
            http_client=http_client,
            cache=ResponseCache(tmp_path / "cache"),
        )
        assert client.compute_routes({}, field_mask="routes.duration")["routes"]


def test_api_error_hides_key(tmp_path) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(403, json={"error": {"message": "forbidden"}})
    )
    with httpx.Client(transport=transport) as http_client:
        client = GoogleMapsClient(
            "secret-key",
            http_client=http_client,
            cache=ResponseCache(tmp_path / "cache"),
        )
        with pytest.raises(GoogleApiError, match="forbidden") as caught:
            client.compute_routes({}, field_mask="routes.duration")
    assert "secret-key" not in str(caught.value)


def test_successful_response_is_reused_from_cache(tmp_path) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"routes": [{"duration": "10s"}]})

    cache = ResponseCache(tmp_path / "cache")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = GoogleMapsClient("test-key", http_client=http_client, cache=cache)
        client.compute_routes({"travelMode": "WALK"}, field_mask="routes.duration")
        client.compute_routes({"travelMode": "WALK"}, field_mask="routes.duration")

    assert calls == 1


def test_no_cache_bypasses_reads_and_writes(tmp_path) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"routes": [{"duration": "10s"}]})

    cache_directory = tmp_path / "cache"
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = GoogleMapsClient(
            "test-key",
            http_client=http_client,
            cache=ResponseCache(cache_directory),
        )
        for _ in range(2):
            client.compute_routes(
                {"travelMode": "WALK"},
                field_mask="routes.duration",
                use_cache=False,
            )

    assert calls == 2
    assert not cache_directory.exists()
