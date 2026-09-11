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

`bus` requests prefer bus service, but Google documents that this preference is not
a strict exclusion of other transit modes. Transit steps should therefore be
inspected before classifying a route as bus-only.

## Tests

```bash
uv run pytest
```

The test configuration disables network sockets and all API responses are mocked,
so tests cannot call or bill Google.
