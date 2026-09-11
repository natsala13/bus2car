import httpx
import pytest

from routes_api.api import GoogleApiError, GoogleMapsClient


def test_compute_routes_uses_key_and_field_mask() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Goog-Api-Key"] == "test-key"
        assert request.headers["X-Goog-FieldMask"] == "routes.duration"
        return httpx.Response(200, json={"routes": [{"duration": "10s"}]})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = GoogleMapsClient("test-key", http_client=http_client)
        assert client.compute_routes({}, field_mask="routes.duration")["routes"]


def test_api_error_hides_key() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(403, json={"error": {"message": "forbidden"}})
    )
    with httpx.Client(transport=transport) as http_client:
        client = GoogleMapsClient("secret-key", http_client=http_client)
        with pytest.raises(GoogleApiError, match="forbidden") as caught:
            client.compute_routes({}, field_mask="routes.duration")
    assert "secret-key" not in str(caught.value)
