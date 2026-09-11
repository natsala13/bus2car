"""Typed benchmark configuration and high-level Route Matrix execution."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterator, Literal, Sequence
from zoneinfo import ZoneInfo

import click
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from routes_api.adresses import Address, add_new_adress
from routes_api.api import GoogleApiError, GoogleMapsClient
from routes_api.routes import TRANSPORT_MODES
from routes_api.utils import DATA_DIR, append_csv_row, normalize_departure_time, parse_google_duration, utc_now_iso


ISRAEL = "Israel"
TEL_AVIV_TIME_ZONE = ZoneInfo("Asia/Jerusalem")
BENCHMARKS_DIR = Path(__file__).resolve().parent.parent / "benchmarks"
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
BenchmarkTime = datetime | time


class Benchmark(BaseModel):
    """Versioned, Israel-only benchmark definition loaded from YAML."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    state: Literal["Israel"] = ISRAEL
    sources: list[Address] = Field(min_length=1)
    destinations: list[Address] = Field(min_length=1)
    times: list[BenchmarkTime] = Field(min_length=1)
    transportation_ways: list[TransportationWay] = Field(min_length=1)

    @field_validator("times")
    @classmethod
    def require_aware_times(cls, values: list[BenchmarkTime]) -> list[BenchmarkTime]:
        for value in values:
            if isinstance(value, datetime) and (
                value.tzinfo is None or value.utcoffset() is None
            ):
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


def _benchmark_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def create_benchmark(
    *,
    name: str,
    version: str,
    sources: Sequence[str],
    destinations: Sequence[str],
    times: Sequence[str | time | datetime],
    transportation_ways: Sequence[TransportationWay],
    output_path: str | Path | None = None,
    client: GoogleMapsClient | None = None,
    use_cache: bool = True,
) -> Benchmark:
    """Verify Tel Aviv points, build a typed benchmark, and save it as YAML."""

    owns_client = client is None
    api_client = client or GoogleMapsClient()
    try:
        signed_sources = [
            add_new_adress(
                source,
                client=api_client,
                use_cache=use_cache,
            )
            for source in sources
        ]
        signed_destinations = [
            add_new_adress(
                destination,
                client=api_client,
                use_cache=use_cache,
            )
            for destination in destinations
        ]
    finally:
        if owns_client:
            api_client.close()

    benchmark = Benchmark.model_validate(
        {
            "name": name,
            "version": version,
            "state": ISRAEL,
            "sources": signed_sources,
            "destinations": signed_destinations,
            "times": list(times),
            "transportation_ways": list(transportation_ways),
        }
    )
    destination_path = (
        Path(output_path)
        if output_path
        else BENCHMARKS_DIR / f"{_benchmark_slug(name)}.yaml"
    )
    if destination_path.exists():
        raise FileExistsError(f"refusing to overwrite existing benchmark: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = benchmark.model_dump(mode="json", exclude_none=True)
    destination_path.write_text(
        yaml.safe_dump(serialized, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return benchmark


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
    slug = _benchmark_slug(benchmark.name)
    return DATA_DIR / "benchmarks" / f"{slug}-{benchmark.version}-{run_id}.csv"


def _measurement_departure_time(
    benchmark_time: BenchmarkTime,
    service_date: date | None,
) -> str:
    if isinstance(benchmark_time, datetime):
        normalized = normalize_departure_time(benchmark_time)
    else:
        if service_date is None:
            raise ValueError(
                "benchmark contains times of day; provide service_date or --date YYYY-MM-DD"
            )
        local_departure = datetime.combine(
            service_date,
            benchmark_time,
            tzinfo=TEL_AVIV_TIME_ZONE,
        )
        normalized = normalize_departure_time(local_departure)
    assert normalized is not None
    return normalized


def measure_benchmark(
    benchmark_file: str | Path,
    *,
    client: GoogleMapsClient | None = None,
    output_path: str | Path | None = None,
    use_cache: bool = True,
    service_date: date | str | None = None,
) -> BenchmarkRun:
    """Measure every source × destination × time × mode combination."""

    benchmark = load_benchmark(benchmark_file)
    if isinstance(service_date, str):
        try:
            service_date = date.fromisoformat(service_date)
        except ValueError as exc:
            raise ValueError("service_date must use YYYY-MM-DD format") from exc
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
            departure_time_utc = _measurement_departure_time(departure, service_date)
            for transport in benchmark.transportation_ways:
                max_elements = 100 if TRANSPORT_MODES[transport] == "TRANSIT" else 625
                for _, sources, _, destinations in _iter_matrix_batches(
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
@click.option("--date", "service_date", help="Service date for time-only slots (YYYY-MM-DD).")
@click.option("--no-cache", is_flag=True, help="Bypass cache reads and writes.")
def cli(
    benchmark_file: Path,
    output: Path | None,
    service_date: str | None,
    no_cache: bool,
) -> None:
    """Measure every route defined by BENCHMARK_FILE."""

    try:
        result = measure_benchmark(
            benchmark_file,
            output_path=output,
            use_cache=not no_cache,
            service_date=service_date,
        )
    except (GoogleApiError, OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    payload = asdict(result)
    payload["output_path"] = str(result.output_path)
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cli()
