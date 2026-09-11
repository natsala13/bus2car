import csv
import json

import httpx
import pytest
import yaml
from pydantic import ValidationError

from routes_api.adresses import Address
from routes_api.api import GoogleMapsClient
from routes_api.benchmark import (
    Benchmark,
    _iter_matrix_batches,
    create_benchmark,
    load_benchmark,
    measure_benchmark,
)
from routes_api.cache import ResponseCache


BENCHMARK_YAML = """\
name: tiny-test
version: "1"
state: Israel
sources:
  - id: source-a
    place_id: place-source-a
  - id: source-b
    latitude: 32.07
    longitude: 34.79
destinations:
  - id: destination-a
    address: Destination A, Tel Aviv, Israel
  - id: destination-b
    place_id: place-destination-b
times:
  - 2026-09-14T08:00:00+03:00
transportation_ways: [car, bus]
"""


def _matrix_response(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    elements = []
    for origin_index, _ in enumerate(body["origins"]):
        for destination_index, _ in enumerate(body["destinations"]):
            elements.append(
                {
                    "originIndex": origin_index,
                    "destinationIndex": destination_index,
                    "status": {},
                    "condition": "ROUTE_EXISTS",
                    "duration": "600s",
                    "staticDuration": "550s",
                    "distanceMeters": 3000,
                }
            )
    return httpx.Response(200, json=elements)


def test_benchmark_requires_israel() -> None:
    with pytest.raises(ValidationError, match="Israel"):
        Benchmark.model_validate(
            {
                "name": "invalid",
                "version": "1",
                "state": "France",
                "sources": [{"id": "a", "address": "A"}],
                "destinations": [{"id": "b", "address": "B"}],
                "times": ["2026-09-14T08:00:00+03:00"],
                "transportation_ways": ["car"],
            }
        )


def test_address_requires_exactly_one_location_form() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        Address(id="bad", address="A", place_id="place-a")


def test_load_and_measure_complete_matrix_with_mock(tmp_path) -> None:
    benchmark_path = tmp_path / "benchmark.yaml"
    benchmark_path.write_text(BENCHMARK_YAML, encoding="utf-8")
    output_path = tmp_path / "results.csv"
    assert load_benchmark(benchmark_path).state == "Israel"

    with httpx.Client(transport=httpx.MockTransport(_matrix_response)) as http_client:
        client = GoogleMapsClient(
            "test-key",
            http_client=http_client,
            cache=ResponseCache(tmp_path / "cache"),
        )
        run = measure_benchmark(
            benchmark_path,
            client=client,
            output_path=output_path,
        )

    assert run.result_count == 8
    assert run.matrix_request_count == 2
    with output_path.open(encoding="utf-8") as results_file:
        rows = list(csv.DictReader(results_file))
    assert len(rows) == 8
    assert {row["transport_type"] for row in rows} == {"car", "bus"}
    assert all(row["route_exists"] == "True" for row in rows)


def test_benchmark_reuses_cache_and_no_cache_bypasses_it(tmp_path) -> None:
    benchmark_path = tmp_path / "benchmark.yaml"
    benchmark_path.write_text(BENCHMARK_YAML, encoding="utf-8")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _matrix_response(request)

    cache = ResponseCache(tmp_path / "cache")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = GoogleMapsClient("test-key", http_client=http_client, cache=cache)
        measure_benchmark(
            benchmark_path,
            client=client,
            output_path=tmp_path / "cached-1.csv",
        )
        measure_benchmark(
            benchmark_path,
            client=client,
            output_path=tmp_path / "cached-2.csv",
        )
        assert calls == 2
        measure_benchmark(
            benchmark_path,
            client=client,
            output_path=tmp_path / "uncached.csv",
            use_cache=False,
        )

    assert calls == 4


def test_transit_batches_never_exceed_100_elements() -> None:
    sources = [Address(id=f"s-{index}", address=f"S {index}") for index in range(23)]
    destinations = [
        Address(id=f"d-{index}", address=f"D {index}") for index in range(17)
    ]
    batches = list(_iter_matrix_batches(sources, destinations, max_elements=100))

    assert sum(len(source_batch) * len(destination_batch) for _, source_batch, _, destination_batch in batches) == 23 * 17
    assert all(len(source_batch) * len(destination_batch) <= 100 for _, source_batch, _, destination_batch in batches)


def test_create_benchmark_signs_points_and_writes_time_slots(tmp_path, monkeypatch) -> None:
    def fake_add(address, **kwargs):
        return Address(
            id=address.lower().replace(" ", "-"),
            label=address,
            place_id=f"place-{address}",
            formatted_address=f"{address}, Tel Aviv-Yafo, Israel",
            verified=True,
            verification_provider="mock",
            verified_at_utc="2026-09-11T00:00:00Z",
        )

    monkeypatch.setattr("routes_api.benchmark.add_new_adress", fake_add)
    output = tmp_path / "created.yaml"
    benchmark = create_benchmark(
        name="created-test",
        version="1",
        sources=["Source A"],
        destinations=["Destination A"],
        times=["07:00", "18:00"],
        transportation_ways=["car", "bus"],
        output_path=output,
        client=object(),
    )

    assert benchmark.sources[0].verified is True
    serialized = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert serialized["times"] == ["07:00:00", "18:00:00"]
    assert serialized["sources"][0]["place_id"] == "place-Source A"


def test_time_only_benchmark_requires_service_date(tmp_path) -> None:
    benchmark_path = tmp_path / "time-only.yaml"
    benchmark_path.write_text(
        BENCHMARK_YAML.replace(
            "2026-09-14T08:00:00+03:00",
            '"07:00"',
        ),
        encoding="utf-8",
    )
    with httpx.Client(transport=httpx.MockTransport(_matrix_response)) as http_client:
        client = GoogleMapsClient(
            "test-key",
            http_client=http_client,
            cache=ResponseCache(tmp_path / "cache"),
        )
        with pytest.raises(ValueError, match="service_date"):
            measure_benchmark(
                benchmark_path,
                client=client,
                output_path=tmp_path / "missing-date.csv",
            )
        run = measure_benchmark(
            benchmark_path,
            client=client,
            output_path=tmp_path / "with-date.csv",
            service_date="2026-09-14",
        )

    assert run.result_count == 8
