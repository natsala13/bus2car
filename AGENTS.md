# Bus vs. Car Index

## Project goal

Build a credible, reproducible benchmark that measures how competitive public transportation is compared with driving.

Begin with Tel Aviv and later expand the methodology and benchmark to all of Israel. The primary measure is the door-to-door travel-time ratio for a trip from point A to point B:

```text
bus-to-car index = public-transport travel time / car travel time
```

A value of `1.0` means both options take the same time. A value of `1.5` means public transportation takes 50% longer than driving.

The index should make it possible to:

- evaluate the quality and competitiveness of public transportation;
- compare areas and cities;
- identify which kinds of trips are poorly served;
- measure improvements or regressions over time.

## Phase 1: Accurate travel-time calculation

First, develop and validate a reliable way to calculate travel time between any origin and destination using different modes of transportation.

At minimum, support:

- public transportation, initially buses and later all relevant transit;
- private car;
- walking;
- bicycle, when practical.

Compare complete door-to-door journeys, not only time spent inside a vehicle. Public-transport time should include walking to and from stops, expected waiting, transfers, and in-vehicle time. Car time should account for traffic and, when reliable data or assumptions are available, parking/search time and walking from parking.

Results must be tied to a specific departure date and time. The methodology should cover meaningful periods such as weekday peaks, midday, evenings, Fridays, and Saturdays. Keep inputs, assumptions, data versions, and routing-engine versions so results can be reproduced and compared over time.

Validate the routing approach against an independent source or a carefully reviewed sample of real trips before treating it as authoritative.

## Phase 2: Representative benchmark trips

Second, define a stable set of origin-destination pairs that represents everyday life in Israel, starting with Tel Aviv.

Origins should represent where people live. Destinations should represent common needs and activities, including:

- employment;
- education;
- shopping and services;
- healthcare;
- leisure and recreation;
- major transport hubs.

Do not rely on an arbitrary list of landmarks. Select and weight trips using evidence such as population distribution, employment and activity centers, land use, and observed travel patterns when available. The benchmark should represent different neighborhoods, trip lengths, population groups, and times of day.

Keep the benchmark versioned and stable enough for comparisons over time. Changes to its points, weights, or sampling method must be documented and should create a new benchmark version.

## Reporting the index

Do not reduce the result to a single average. Report at least:

- the weighted median bus-to-car ratio;
- relevant percentiles and the distribution of results;
- the share of trips below useful thresholds such as `1.5`, `2.0`, and `3.0`;
- results by time period, geography, trip type, and trip length;
- the share of trips for which no practical public-transport route exists.

Preserve the underlying travel times alongside the ratio so changes can be explained rather than merely observed.

## Working principles

- Accuracy and reproducibility take priority over quickly producing a headline number.
- Prefer open, versionable data and routing tools for the long-term index. Proprietary APIs may be used for prototyping and validation when their cost and terms are documented.
- Separate measured data from assumptions and modeled values.
- Treat missing or unavailable routes explicitly; do not silently discard them.
- Avoid false precision. Publish uncertainty and known limitations.
- Design Tel Aviv work so the same pipeline can later cover all of Israel.
