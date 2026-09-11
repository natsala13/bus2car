# Bus vs. Car Index

Foundation for measuring door-to-door travel times with Google Maps Platform.

## Setup

Enable both **Routes API** and **Geocoding API** in the Google Cloud project, and
allow both APIs in the key's API restrictions. Keep the key in an ignored `.env`:

```dotenv
GOOGLE_MAPS_API_KEY=your-restricted-key
```

Install the locked environment:

```bash
uv sync
```

## Resolve an address

The standalone resolver uses Geocoding API v4 and saves every returned match to
`data/geocoding_results.csv`:

```bash
uv run resolve-address "הסוללים 3, תל אביב-יפו, ישראל"
```

Use `--no-save` to inspect a result without adding it to the CSV.
Use `--no-cache` to force a new billable API request.

## Measure one route

Sources and destinations may be address strings, `place_id:...`, or
`latitude,longitude`. Times must include an explicit UTC offset:

```bash
uv run route-time \
  "place_id:ORIGIN_PLACE_ID" \
  "place_id:DESTINATION_PLACE_ID" \
  --transport bus \
  --time "2026-09-14T08:00:00+03:00"
```

Supported transport names are `car`, `bus`, `bike`, `walk`, plus their Google-like
aliases. Results are saved to `data/route_times.csv`. When address strings are used,
embedded Routes API geocoding results are also saved to the geocoding table.

By default, successful Google responses are cached locally for 24 hours under
`.cache/google_maps/`. Repeating an identical request reuses that response. Pass
`--no-cache` to bypass both cache reads and writes.

`bus` requests prefer bus service, but Google documents that this preference is not
a strict exclusion of other transit modes. Transit steps should therefore be
inspected before classifying a route as bus-only.

## Measure a benchmark

Benchmarks are typed, versioned YAML files. They are fixed to `state: Israel` and
contain source points, destination points, timezone-aware departure times, and
transportation ways. Points accept exactly one of an address, Place ID, or coordinate
pair. See `benchmarks/example.yaml` for a complete example.

```bash
uv run measure-benchmark benchmarks/example.yaml
```

This calls Compute Route Matrix in API-sized batches and writes one normalized CSV
under `data/benchmarks/`. Transit batches never exceed 100 elements; other batches
never exceed 625 elements. Every requested pair is written explicitly, including
missing or failed routes.

Use `--no-cache` to force fresh matrix requests or `--output PATH` to choose the CSV
path. Update example departure times before running because Google only accepts
limited past/future routing windows.

## Tests

```bash
uv run pytest
```

The test configuration disables network sockets and all API responses are mocked,
so tests cannot call or bill Google.
