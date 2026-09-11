# Generated data

The command-line tools create two UTF-8 CSV tables here:

- `geocoding_results.csv`: address queries, Place IDs, coordinates, match quality,
  and resolution timestamps.
- `route_times.csv`: source, destination, requested departure time, transport mode,
  duration, distance, and measurement provenance.

Generated CSV files are intentionally ignored by Git because they can contain
private addresses and Google Maps Platform content. Only this schema description
is versioned.
