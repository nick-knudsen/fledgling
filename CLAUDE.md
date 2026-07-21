# Fledgling (listr)

Birding hotspot optimizer that helps birders maximize lifers when traveling. Analyzes the eBird dataset using greedy submodular optimization to find optimal birding locations. Live at fledgli.ng.

## Tech Stack

- **Backend:** Python 3.12, FastAPI, DuckDB (OLAP), NumPy/Pandas
- **Frontend:** Vanilla JS, Leaflet.js (maps), no framework
- **Infrastructure:** Docker, Nginx reverse proxy, Certbot TLS
- **External APIs:** eBird API, OSRM (driving time routing)

## Commands

```bash
python run.py                          # Start dev server (localhost:8000, auto-reload)
pip install -r requirements.txt        # Install dependencies
docker-compose up -d                   # Production deployment
```

### Data Pipeline

```bash
python ebird_pipeline.py               # Full pipeline: download → extract → process
python fetch_taxonomy.py               # Fetch & cache eBird species taxonomy
```

## Project Structure

- `api.py` — FastAPI server, all REST endpoints
- `hotspot_optimizer.py` — Greedy submodular optimization algorithm
- `ebird_pipeline.py` — Downloads & processes eBird world dataset
- `frequency_data_pipeline.py` — Converts eBird TSV to probability tables
- `fetch_taxonomy.py` — Fetches eBird species taxonomy via API
- `static/app.js` — Main frontend logic (~2800 lines)
- `static/index.html` — Single-page app HTML
- `static/styles.css` — Responsive styling with CSS variables and dark mode

## Code Conventions

### Python
- Modern type hints throughout (`list[str]`, `Optional[int]`, `tuple[date, date]`)
- Dataclasses for data structures (`SpeciesProb`, `HotspotResult`, `OptimizationResult`)
- Snake_case naming (PEP 8)
- Standard `logging` module (`logger = logging.getLogger(__name__)`)
- DuckDB SQL written inline; f-strings for parameterization
- No test framework currently in use

### JavaScript
- Vanilla JS with direct DOM manipulation, no framework
- Global state variables for UI state
- Async/await for API calls via fetch
- Feature-organized code (themes, forms, dropdowns, map handling)

### Git
- Commit messages: imperative, lowercase, concise (e.g., "Error handling for large queries")
- Main branch is primary development branch

## Key Architecture Details

- **Database:** `data/combined.duckdb` (~20 GB) contains hotspots, rolling_wilson_score (species observation probabilities), species taxonomy, and merge metadata
- **Optimization:** Greedy algorithm picks K hotspots maximizing expected new species via multiplicative miss probability updates. Complexity: O(K × H × S)
- **Performance:** Large date ranges sampled every Nth day; hotspot queries capped at 5,000; database sorted by query access patterns
- **Privacy:** No user data stored server-side. Life lists are temporary pandas DataFrames during query execution only
- **Atomic updates:** Pipeline creates `combined_new.duckdb`, then atomically swaps to `combined.duckdb`

## Environment

- Requires `EBIRD_API_KEY` environment variable for eBird API access
- DuckDB file mounted as read-only volume in production (not baked into Docker image)
- Beta gating via hardcoded username whitelist in `api.py`
