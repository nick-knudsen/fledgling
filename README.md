# Fledgling

Hotspot search and trip planning for birders, backed by the eBird Basic Dataset. Given a
life list, a date range, and a geographic area, Fledgling ranks eBird hotspots by expected
new species ("lifers") using a submodular greedy optimizer over per-hotspot, per-species
detection probabilities.

## How it works

- **Data pipeline** (`ebird_pipeline.py` and friends) processes the eBird Basic Dataset
  (EBD) into a DuckDB database of hotspots and rolling Wilson-score detection probabilities
  per species/hotspot/day-of-year.
- **Optimizer** (`hotspot_optimizer.py`) loads the relevant slice of that probability matrix
  for a query (date range + geographic filter) and greedily selects hotspots that maximize
  expected new species, down-weighting species already picked up by earlier selections.
- **API** (`api.py`) is a FastAPI app exposing search/optimize endpoints over the DuckDB
  database, serving a static frontend (`static/`, Leaflet-based) with no separate frontend
  build step.

## Local development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups
```

### Data

The app expects a DuckDB database at `data/combined.duckdb`, built by the ingestion
pipeline below. `data/` is gitignored — it's tens of GB and isn't distributed with the repo.
Building it from scratch requires an [eBird Basic Dataset](https://ebird.org/data/download)
account.

```bash
uv run python ebird_pipeline.py          # full world dataset: download, split, process
uv run python fetch_taxonomy.py          # species taxonomy table
uv run python frequency_data_pipeline.py <region_code> <update_month-year>  # incremental region update
```

`ebird_pipeline.py --help` lists resume/dry-run flags for re-running partial pipeline
progress without starting over.

### OSRM (driving-distance filtering)

The driving-time filter on `/api/optimize` calls a self-hosted OSRM instance rather than
the public OSRM demo API, scoped to North America. Locally, point it at the small Vermont
extract already in `data/osrm/` (built via `osrm-extract`/`osrm-partition`/`osrm-customize`,
same layout as production's full North America graph):

```bash
docker run --rm -p 5000:5000 -v "$(pwd)/data/osrm:/data" \
  ghcr.io/project-osrm/osrm-backend osrm-routed --algorithm mld /data/vermont-latest.osrm
```

The app reads the OSRM base URL from `OSRM_BASE_URL`, defaulting to `http://localhost:5000`
so this just works locally with no `.env` entry needed. Without it running, everything else
in the app still works — the driving filter degrades gracefully rather than blocking startup.

### Secrets

- `fetch_taxonomy.py` reads an eBird API key from a gitignored `secrets.toml`:
  ```toml
  [ebird]
  api_key = "..."
  ```
- The running app reads its eBird API key from the `EBIRD_API_KEY` environment variable
  instead (see `docker-compose.yml`).

### Running the app

```bash
export EBIRD_API_KEY=...
uv run python run.py
```

This starts the FastAPI app with auto-reload at `http://localhost:8000`.

## Testing & linting

```bash
uv run pytest        # test suite
uv run ruff check .  # lint
uv run mypy .        # type check
```

All three run in CI on every PR and are required to pass before merging to `main`.

## Deployment

Built and run via Docker:

```bash
docker compose up -d --build
```

`docker-compose.yml` mounts `./data` read-only into the container and expects
`EBIRD_API_KEY` in the environment.

## License

[AGPL-3.0](LICENSE)
