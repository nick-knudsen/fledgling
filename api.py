import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.request import Request, urlopen
import json

import duckdb
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hotspot_optimizer import optimize_hotspots

DB_PATH = "data/vermont.duckdb"

with open("secrets.toml", "rb") as f:
    secrets = tomllib.load(f)
EBIRD_API_KEY = secrets["ebird"]["api_key"]

app = FastAPI(title="Listr")


class OptimizeRequest(BaseModel):
    life_list: list[str]
    start_date: date
    end_date: date
    k: int = 5
    county: str | None = None
    state: str | None = None


def fetch_recent_species(locality_id: int) -> set[str]:
    """Fetch species recently observed at a hotspot from the eBird API.

    Returns a set of common names.
    """
    url = f"https://api.ebird.org/v2/data/obs/L{locality_id}/recent"
    req = Request(url, headers={"X-eBirdApiToken": EBIRD_API_KEY})
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return {obs["comName"] for obs in data}
    except Exception:
        return set()


def add_recent_observations(hotspots: list[dict]) -> list[dict]:
    """For each hotspot, check which target species were recently observed."""
    locality_ids = [h["locality_id"] for h in hotspots]

    with ThreadPoolExecutor(max_workers=5) as pool:
        recent_by_hotspot = dict(zip(locality_ids, pool.map(fetch_recent_species, locality_ids)))

    for h in hotspots:
        recent = recent_by_hotspot.get(h["locality_id"], set())
        for sp in h["target_species"]:
            sp["recently_observed"] = sp["common_name"] in recent

    return hotspots


@app.get("/api/counties")
def get_counties():
    """Return the list of available counties."""
    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        rows = con.execute(
            "SELECT DISTINCT county FROM sightings_clean ORDER BY county"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


@app.post("/api/optimize")
def run_optimization(req: OptimizeRequest):
    """Run the hotspot optimization.

    The client sends the user's life list (species names) along with
    the search parameters. No user data is stored on the server.
    """
    result = optimize_hotspots(
        db_path=DB_PATH,
        life_list_names=req.life_list,
        start_date=req.start_date,
        end_date=req.end_date,
        k=req.k,
        county=req.county,
        state=req.state,
    )

    hotspots = [
        {
            "rank": h.rank,
            "locality": h.locality,
            "locality_id": h.locality_id,
            "latitude": h.latitude,
            "longitude": h.longitude,
            "county": h.county,
            "marginal_gain": round(h.marginal_gain, 2),
            "cumulative_expected": round(h.cumulative_expected, 2),
            "target_species": [
                {"common_name": sp.common_name, "probability": round(sp.probability, 4)}
                for sp in h.target_species
            ],
        }
        for h in result.selected_hotspots
    ]

    hotspots = add_recent_observations(hotspots)

    return {
        "total_expected_lifers": round(result.total_expected_lifers, 2),
        "num_candidate_hotspots": result.num_candidate_hotspots,
        "num_potential_lifers": result.num_potential_lifers,
        "date_range": [result.date_range[0].isoformat(), result.date_range[1].isoformat()],
        "geographic_filter": result.geographic_filter,
        "hotspots": hotspots,
        "species_combined_probs": [
            {"common_name": sp.common_name, "probability": round(sp.probability, 4)}
            for sp in result.species_combined_probs
        ],
    }


# Serve the frontend
app.mount("/", StaticFiles(directory="static", html=True), name="static")
