import csv
import math
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen
import json
import logging

logger = logging.getLogger(__name__)

import duckdb
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hotspot_optimizer import optimize_hotspots

DB_PATH = "data/combined.duckdb"

with open("secrets.toml", "rb") as f:
    secrets = tomllib.load(f)
EBIRD_API_KEY = secrets["ebird"]["api_key"]

app = FastAPI(title="Listr")


class OptimizeRequest(BaseModel):
    life_list: list[str]
    start_date: date
    end_date: date
    k: int = 5
    counties: list[str] | None = None
    states: list[str] | None = None
    country: str | None = None
    center_lat: float | None = None
    center_lon: float | None = None
    max_driving_minutes: int | None = None


def fetch_recent_species(locality_id: int) -> set[str]:
    """Fetch species recently observed at a hotspot from the eBird API.

    Returns a set of common names.
    """
    url = f"https://api.ebird.org/v2/data/obs/L{locality_id}/recent?back=30"
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


@app.get("/api/region-names")
def get_region_names():
    """Return a mapping of eBird region codes to display names."""
    lookup = {}
    with open(Path("data/subnational1.csv"), newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = row["country_code"]
            lookup[code] = row["country_name"]
            sub_code = row["subnational1_code"]
            if sub_code and sub_code != f"{code}-":
                lookup[sub_code] = row["subnational1_name"]
    return lookup


@app.get("/api/search-areas")
def get_search_areas():
    """Return nested country/state/county hierarchy from the hotspots table."""
    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        rows = con.execute(
            "SELECT DISTINCT country, state, county FROM hotspots ORDER BY country, state, county"
        ).fetchall()
        result = {}
        for country, state, county in rows:
            result.setdefault(country, {}).setdefault(state, []).append(county)
        return result
    finally:
        con.close()


_taxonomy_cache: list[dict] | None = None


@app.get("/api/species")
def get_species():
    """Return bird species from the species table."""
    global _taxonomy_cache
    if _taxonomy_cache is None:
        con = duckdb.connect(DB_PATH, read_only=True)
        try:
            rows = con.execute(
                "SELECT species_code, common_name, scientific_name, category, "
                "banding_codes, com_name_codes, sci_name_codes "
                "FROM species WHERE category == 'species' ORDER BY taxon_order"
            ).fetchall()
        finally:
            con.close()
        _taxonomy_cache = [
            {
                "comName": row[1],
                "sciName": row[2],
                "speciesCode": row[0],
                "bandingCodes": row[4] or [],
                "comNameCodes": row[5] or [],
                "sciNameCodes": row[6] or [],
            }
            for row in rows
        ]
    return _taxonomy_cache


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def osrm_filter_hotspots(
    center_lat: float,
    center_lon: float,
    max_minutes: int,
) -> list[int]:
    """Filter hotspots by driving time using the public OSRM API.

    1. Load all hotspot coords from DB
    2. Pre-filter by crow-flies radius (max_minutes * 2 km)
    3. Call OSRM table API in batches to get actual driving times
    4. Return locality_ids within max_minutes
    """
    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        rows = con.execute(
            "SELECT locality_id, latitude, longitude FROM hotspots"
        ).fetchall()
    finally:
        con.close()

    # Pre-filter by crow-flies distance
    radius_km = max_minutes * 2.0
    candidates = []
    for lid, lat, lon in rows:
        if haversine_km(center_lat, center_lon, lat, lon) <= radius_km:
            candidates.append((lid, lat, lon))

    if not candidates:
        return []

    # Call OSRM table API in batches of 100
    BATCH_SIZE = 100
    result_ids = []

    for i in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[i:i + BATCH_SIZE]
        coords_str = f"{center_lon},{center_lat}"
        for _, lat, lon in batch:
            coords_str += f";{lon},{lat}"

        url = (
            f"https://router.project-osrm.org/table/v1/driving/{coords_str}"
            f"?sources=0&annotations=duration"
        )
        req = Request(url, headers={"User-Agent": "Listr/1.0"})
        try:
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            logger.warning(f"OSRM API error for batch {i}: {e}")
            continue

        if data.get("code") != "Ok":
            logger.warning(f"OSRM returned non-Ok: {data.get('code')}")
            continue

        durations = data["durations"][0]  # single source row
        max_seconds = max_minutes * 60
        for j, dur in enumerate(durations[1:]):  # skip source-to-source (index 0)
            if dur is not None and dur <= max_seconds:
                result_ids.append(batch[j][0])

    return result_ids


@app.post("/api/optimize")
def run_optimization(req: OptimizeRequest):
    """Run the hotspot optimization.

    The client sends the user's life list (species names) along with
    the search parameters. No user data is stored on the server.
    """
    locality_ids = None
    if req.center_lat is not None and req.center_lon is not None and req.max_driving_minutes is not None:
        locality_ids = osrm_filter_hotspots(req.center_lat, req.center_lon, req.max_driving_minutes)
        if not locality_ids:
            return {
                "total_expected_lifers": 0,
                "num_candidate_hotspots": 0,
                "num_potential_lifers": 0,
                "date_range": [req.start_date.isoformat(), req.end_date.isoformat()],
                "geographic_filter": f"Within {req.max_driving_minutes} min drive",
                "hotspots": [],
                "species_combined_probs": [],
            }

    result = optimize_hotspots(
        db_path=DB_PATH,
        life_list_names=req.life_list,
        start_date=req.start_date,
        end_date=req.end_date,
        k=req.k,
        counties=req.counties,
        states=req.states,
        country=req.country,
        locality_ids=locality_ids,
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
