"""Typed benchmark configuration and high-level Route Matrix execution."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Literal, Sequence

import click
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from routes_api.adresses import Address
from routes_api.api import GoogleApiError, GoogleMapsClient
from routes_api.routes import TRANSPORT_MODES
from routes_api.utils import DATA_DIR, append_csv_row, normalize_departure_time, parse_google_duration, utc_now_iso


ISRAEL = "Israel"
MATRIX_FIELD_MASK = ",".join(
    (
        "originIndex",
        "destinationIndex",
        "status",
        "condition",
        "distanceMeters",
        "duration",
        "staticDuration",
        "fallbackInfo",
    )
)

BENCHMARK_RESULT_FIELDS = (
    "run_id",
    "measured_at_utc",
    "benchmark_name",
    "benchmark_version",
    "state",
    "departure_time_utc",
    "source_id",
    "source_label",
    "source",
    "source_place_id",
    "destination_id",
    "destination_label",
    "destination",
    "destination_place_id",
    "transport_type",
    "google_travel_mode",
    "routing_preference",
    "condition",
    "status_code",
    "status_message",
    "route_exists",
    "duration_seconds",
    "static_duration_seconds",
    "distance_meters",
    "used_fallback",
)

TransportationWay = Literal["car", "bus", "bike", "walk", "transit"]


class Benchmark(BaseModel):
    """Versioned, Israel-only benchmark definition loaded from YAML."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    state: Literal["Israel"] = ISRAEL
    sources: list[Address] = Field(min_length=1)
    destinations: list[Address] = Field(min_length=1)
    times: list[datetime] = Field(min_length=1)
    transportation_ways: list[TransportationWay] = Field(min_length=1)

    @field_validator("times")
    @classmethod
    def require_aware_times(cls, values: list[datetime]) -> list[datetime]:
        for value in values:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("every benchmark time must include a UTC offset")
        return values

    @field_validator("sources", "destinations")
    @classmethod
    def require_unique_point_ids(cls, values: list[Address]) -> list[Address]:
        identifiers = [point.id for point in values]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("point ids must be unique within their list")
        return values

    @field_validator("transportation_ways")
    @classmethod
    def require_unique_transportation_ways(
        cls,
        values: list[TransportationWay],
    ) -> list[TransportationWay]:
        if len(values) != len(set(values)):
            raise ValueError("transportation_ways must not contain duplicates")
        return values


@dataclass(frozen=True)
class BenchmarkRun:
    benchmark_name: str
    run_id: str
    output_path: Path
    result_count: int
    matrix_request_count: int


def load_benchmark(path: str | Path) -> Benchmark:
    benchmark_path = Path(path)
    try:
        with benchmark_path.open(encoding="utf-8") as benchmark_file:
            raw = yaml.safe_load(benchmark_file)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid benchmark YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("benchmark YAML must contain a mapping at its root")
    return Benchmark.model_validate(raw)


def _iter_matrix_batches(
    sources: Sequence[Address],
    destinations: Sequence[Address],
    *,
    max_elements: int,
) -> Iterator[tuple[int, Sequence[Address], int, Sequence[Address]]]:
    # Square chunks maximize elements while satisfying Google's 50-address sum.
    chunk_side = min(25, math.isqrt(max_elements))
    for destination_start in range(0, len(destinations), chunk_side):
        destination_batch = destinations[
            destination_start : destination_start + chunk_side
        ]
        origin_chunk_size = min(
            50 - len(destination_batch),
            max_elements // len(destination_batch),
        )
        for source_start in range(0, len(sources), origin_chunk_size):
            yield (
                source_start,
                sources[source_start : source_start + origin_chunk_size],
                destination_start,
                destination_batch,
            )


def _matrix_request(
    sources: Sequence[Address],
    destinations: Sequence[Address],
    transport: TransportationWay,
    departure_time_utc: str,
) -> tuple[dict[str, Any], str, str | None]:
    google_mode = TRANSPORT_MODES[transport]
    request: dict[str, Any] = {
        "origins": [{"waypoint": source.to_waypoint()} for source in sources],
        "destinations": [
            {"waypoint": destination.to_waypoint()} for destination in destinations
        ],
        "travelMode": google_mode,
    }
    routing_preference: str | None = None
    if google_mode == "DRIVE":
        routing_preference = "TRAFFIC_AWARE"
        request["routingPreference"] = routing_preference
    if transport == "bus":
        request["transitPreferences"] = {"allowedTravelModes": ["BUS"]}
    if google_mode in {"DRIVE", "TRANSIT"}:
        request["departureTime"] = departure_time_utc
    return request, google_mode, routing_preference


def _seconds(element: dict[str, Any], field: str) -> float | str:
    value = element.get(field)
    return parse_google_duration(value) if value else ""


def _result_row(
    *,
    benchmark: Benchmark,
    run_id: str,
    measured_at_utc: str,
    departure_time_utc: str,
    source: Address,
    destination: Address,
    transport: TransportationWay,
    google_mode: str,
    routing_preference: str | None,
    element: dict[str, Any] | None,
) -> dict[str, object]:
    element = element or {}
    status = element.get("status") or {}
    condition = element.get("condition", "MISSING_RESPONSE")
    return {
        "run_id": run_id,
        "measured_at_utc": measured_at_utc,
        "benchmark_name": benchmark.name,
        "benchmark_version": benchmark.version,
        "state": benchmark.state,
        "departure_time_utc": departure_time_utc,
        "source_id": source.id,
        "source_label": source.label or "",
        "source": source.display_value,
        "source_place_id": source.place_id or "",
        "destination_id": destination.id,
        "destination_label": destination.label or "",
        "destination": destination.display_value,
        "destination_place_id": destination.place_id or "",
        "transport_type": transport,
        "google_travel_mode": google_mode,
        "routing_preference": routing_preference or "",
        "condition": condition,
        "status_code": status.get("code", 0),
        "status_message": status.get("message", ""),
        "route_exists": condition == "ROUTE_EXISTS",
        "duration_seconds": _seconds(element, "duration"),
        "static_duration_seconds": _seconds(element, "staticDuration"),
        "distance_meters": element.get("distanceMeters", ""),
        "used_fallback": bool(element.get("fallbackInfo")),
    }


def _default_output_path(benchmark: Benchmark, run_id: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", benchmark.name.lower()).strip("-")
    return DATA_DIR / "benchmarks" / f"{slug}-{benchmark.version}-{run_id}.csv"


def measure_benchmark(
    benchmark_file: str | Path,
    *,
    client: GoogleMapsClient | None = None,
    output_path: str | Path | None = None,
    use_cache: bool = True,
) -> BenchmarkRun:
    """Measure every source × destination × time × mode combination."""

    benchmark = load_benchmark(benchmark_file)
    measured_at_utc = utc_now_iso()
    run_id = datetime.fromisoformat(measured_at_utc.removesuffix("Z") + "+00:00").strftime(
        "%Y%m%dT%H%M%SZ"
    )
    destination_path = (
        Path(output_path) if output_path else _default_output_path(benchmark, run_id)
    )
    if destination_path.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {destination_path}")

    owns_client = client is None
    api_client = client or GoogleMapsClient()
    result_count = 0
    request_count = 0
    try:
        for departure in benchmark.times:
            departure_time_utc = normalize_departure_time(departure)
            assert departure_time_utc is not None
            for transport in benchmark.transportation_ways:
                max_elements = 100 if TRANSPORT_MODES[transport] == "TRANSIT" else 625
                for source_start, sources, destination_start, destinations in _iter_matrix_batches(
                    benchmark.sources,
                    benchmark.destinations,
                    max_elements=max_elements,
                ):
                    request, google_mode, routing_preference = _matrix_request(
                        sources,
                        destinations,
                        transport,
                        departure_time_utc,
                    )
                    elements = api_client.compute_route_matrix(
                        request,
                        field_mask=MATRIX_FIELD_MASK,
                        use_cache=use_cache,
                    )
                    request_count += 1
                    indexed_elements = {
                        (element.get("originIndex", 0), element.get("destinationIndex", 0)): element
                        for element in elements
                    }
                    for local_source_index, source in enumerate(sources):
                        for local_destination_index, destination in enumerate(destinations):
                            element = indexed_elements.get(
                                (local_source_index, local_destination_index)
                            )
                            row = _result_row(
                                benchmark=benchmark,
                                run_id=run_id,
                                measured_at_utc=measured_at_utc,
                                departure_time_utc=departure_time_utc,
                                source=source,
                                destination=destination,
                                transport=transport,
                                google_mode=google_mode,
                                routing_preference=routing_preference,
                                element=element,
                            )
                            append_csv_row(destination_path, BENCHMARK_RESULT_FIELDS, row)
                            result_count += 1
    except Exception:
        # A failed run leaves no misleading partial matrix behind.
        if destination_path.exists():
            destination_path.unlink()
        raise
    finally:
        if owns_client:
            api_client.close()

    return BenchmarkRun(
        benchmark_name=benchmark.name,
        run_id=run_id,
        output_path=destination_path,
        result_count=result_count,
        matrix_request_count=request_count,
    )


@click.command()
@click.argument(
    "benchmark_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--no-cache", is_flag=True, help="Bypass cache reads and writes.")
def cli(benchmark_file: Path, output: Path | None, no_cache: bool) -> None:
    """Measure every route defined by BENCHMARK_FILE."""

    try:
        result = measure_benchmark(
            benchmark_file,
            output_path=output,
            use_cache=not no_cache,
        )
    except (GoogleApiError, OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cli()
