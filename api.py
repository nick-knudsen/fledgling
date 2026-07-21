import csv
import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen
import json
import logging

logger = logging.getLogger(__name__)

import duckdb
from fastapi import FastAPI, HTTPException
from fastapi import Request as Request_
from fastapi.responses import Response as FastAPIResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import numpy as np
from hotspot_optimizer import (
    optimize_hotspots,
    load_probability_matrix,
    load_hotspot_durations,
    date_range_to_days_of_year,
    plan_big_day,
    DEFAULT_HOTSPOT_MINUTES,
)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = str(BASE_DIR / "data" / "combined.duckdb")

EBIRD_API_KEY = os.environ["EBIRD_API_KEY"]
OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org").rstrip("/")

app = FastAPI(title="Fledgling")

class StateCounty(BaseModel):
    state: str
    county: str


class OptimizeRequest(BaseModel):
    life_list: list[str] = []
    start_date: date
    end_date: date
    k: int = 5
    state_counties: list[StateCounty] | None = None
    states: list[str] | None = None
    country: str | None = None
    center_lat: float | None = None
    center_lon: float | None = None
    max_driving_minutes: int | None = None
    target_species: list[str] | None = None
    exclude_locality_ids: list[int] | None = None


class PlanBigDayRequest(BaseModel):
    life_list: list[str] = []
    start_date: date
    end_date: date
    window_start: str
    window_end: str
    state_counties: list[StateCounty] | None = None
    states: list[str] | None = None
    country: str | None = None
    center_lat: float | None = None
    center_lon: float | None = None
    max_driving_minutes: int | None = None
    target_species: list[str] | None = None
    exclude_locality_ids: list[int] | None = None


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
    with open(BASE_DIR / "data" / "subnational1.csv", newline="", encoding="utf-8") as f:
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
            f"{OSRM_BASE_URL}/table/v1/driving/{coords_str}"
            f"?sources=0&annotations=duration"
        )
        req = Request(url, headers={"User-Agent": "Fledgling/1.0"})
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


def osrm_duration_matrix(coords: list[tuple[float, float]]) -> np.ndarray:
    """N×N driving-duration matrix (in minutes) from OSRM /table.

    `coords` is a list of (lat, lon). Missing cells are filled with a large
    sentinel so the optimizer won't select unreachable pairs.
    """
    n = len(coords)
    if n == 0:
        return np.zeros((0, 0), dtype=np.float64)
    coord_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"{OSRM_BASE_URL}/table/v1/driving/{coord_str}?annotations=duration"
    req = Request(url, headers={"User-Agent": "Fledgling/1.0"})
    with urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM /table returned {data.get('code')}")
    matrix = np.full((n, n), 9999.0, dtype=np.float64)
    for i, row in enumerate(data["durations"]):
        for j, sec in enumerate(row):
            if sec is not None:
                matrix[i, j] = float(sec) / 60.0
    return matrix


def osrm_route_geojson(coords: list[tuple[float, float]]) -> dict | None:
    """Polyline for the ordered visit sequence. Returns GeoJSON geometry or None."""
    if len(coords) < 2:
        return None
    coord_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"{OSRM_BASE_URL}/route/v1/driving/{coord_str}?overview=full&geometries=geojson"
    req = Request(url, headers={"User-Agent": "Fledgling/1.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        logger.warning(f"OSRM /route error: {e}")
        return None
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    return data["routes"][0]["geometry"]


def _parse_hhmm(s: str) -> int:
    """Convert 'HH:MM' → minutes since midnight."""
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _format_hhmm(mins: int) -> str:
    mins %= 24 * 60
    return f"{mins // 60:02d}:{mins % 60:02d}"


@app.get("/osrm/{path:path}")
def osrm_proxy(path: str, request: Request_):
    """Dev-mode fallback proxy to OSRM.

    In production nginx intercepts /osrm/ before it reaches FastAPI, so this
    only runs under `python run.py` or `docker compose up` (no nginx front).
    """
    query = request.url.query
    url = f"{OSRM_BASE_URL}/{path}" + (f"?{query}" if query else "")
    req = Request(url, headers={"User-Agent": "Fledgling/1.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            body = resp.read()
            content_type = resp.headers.get("Content-Type", "application/json")
    except Exception as e:
        logger.warning(f"OSRM proxy error for {url}: {e}")
        raise HTTPException(status_code=502, detail="OSRM upstream error")
    return FastAPIResponse(content=body, media_type=content_type)


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

    try:
        result = optimize_hotspots(
            db_path=DB_PATH,
            life_list_names=req.life_list,
            start_date=req.start_date,
            end_date=req.end_date,
            k=req.k,
            state_counties=[(sc.state, sc.county) for sc in req.state_counties] if req.state_counties else None,
            states=req.states,
            country=req.country,
            locality_ids=locality_ids,
            target_species=req.target_species,
            exclude_locality_ids=req.exclude_locality_ids,
        )
    except Exception:
        logger.exception("Optimization failed")
        raise HTTPException(status_code=500, detail="Search failed. Try a smaller area or date range.")

    hotspots = [
        {
            "rank": h.rank,
            "locality": h.locality,
            "locality_id": h.locality_id,
            "latitude": h.latitude,
            "longitude": h.longitude,
            "county": h.county,
            "state": h.state,
            "marginal_gain": round(h.marginal_gain, 2),
            "cumulative_expected": round(h.cumulative_expected, 2),
            "target_species": [
                {"common_name": sp.common_name, "probability": round(sp.probability, 4)}
                for sp in h.target_species
            ],
        }
        for h in result.selected_hotspots
    ]

    hotspots = [h for h in hotspots if h["target_species"]]
    for i, h in enumerate(hotspots):
        h["rank"] = i + 1

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


@app.post("/api/plan-big-day")
def run_plan_big_day(req: PlanBigDayRequest):
    """Plan a single-day birding itinerary within a clock window.

    Orienteering over the hotspot graph: pick and order stops so driving +
    median per-stop time fit inside the user's window, maximizing expected
    new species. See hotspot_optimizer.plan_big_day for the algorithm.
    """
    try:
        start_min = _parse_hhmm(req.window_start)
        end_min = _parse_hhmm(req.window_end)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="window_start/window_end must be 'HH:MM'")
    if end_min <= start_min:
        raise HTTPException(status_code=400, detail="window_end must be after window_start")
    window_minutes = end_min - start_min

    locality_ids = None
    geo_description = None
    if (
        req.center_lat is not None
        and req.center_lon is not None
        and req.max_driving_minutes is not None
    ):
        locality_ids = osrm_filter_hotspots(
            req.center_lat, req.center_lon, req.max_driving_minutes
        )
        geo_description = f"Within {req.max_driving_minutes} min drive"
        if not locality_ids:
            return {
                "total_expected_species": 0.0,
                "window_minutes": window_minutes,
                "num_candidate_hotspots": 0,
                "itinerary": [],
                "leg_durations_minutes": [],
                "total_travel_minutes": 0,
                "route_geojson": None,
                "geographic_filter": geo_description,
            }

    days_of_year = date_range_to_days_of_year(req.start_date, req.end_date)
    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        hotspot_info, prob_matrix, species_list = load_probability_matrix(
            con, days_of_year, req.life_list,
            state_counties=[(sc.state, sc.county) for sc in req.state_counties] if req.state_counties else None,
            states=req.states,
            country=req.country,
            locality_ids=locality_ids,
            target_species=req.target_species,
            exclude_locality_ids=req.exclude_locality_ids,
        )

        MAX_BIGDAY_CANDIDATES = 500
        if prob_matrix.shape[0] > MAX_BIGDAY_CANDIDATES:
            scores = prob_matrix.sum(axis=1)
            top_idx = np.argsort(-scores)[:MAX_BIGDAY_CANDIDATES]
            top_idx_sorted = np.sort(top_idx)
            prob_matrix = prob_matrix[top_idx_sorted]
            hotspot_info = hotspot_info.iloc[top_idx_sorted].reset_index(drop=True)

        if prob_matrix.size == 0:
            return {
                "total_expected_species": 0.0,
                "window_minutes": window_minutes,
                "num_candidate_hotspots": 0,
                "itinerary": [],
                "leg_durations_minutes": [],
                "total_travel_minutes": 0,
                "route_geojson": None,
                "geographic_filter": geo_description or _describe_region(req),
            }

        locality_id_list = [int(lid) for lid in hotspot_info["locality_id"].tolist()]
        median_map = load_hotspot_durations(con, locality_id_list)
    finally:
        con.close()

    durations = np.full(len(locality_id_list), DEFAULT_HOTSPOT_MINUTES, dtype=np.float64)

    coords = [
        (float(hotspot_info.iloc[i]["latitude"]), float(hotspot_info.iloc[i]["longitude"]))
        for i in range(len(hotspot_info))
    ]
    try:
        travel_matrix = osrm_duration_matrix(coords)
    except Exception:
        logger.exception("OSRM duration matrix failed")
        raise HTTPException(status_code=502, detail="Routing service unavailable")

    tour, gains, final_miss, total_travel = plan_big_day(
        prob_matrix, durations, travel_matrix, float(window_minutes)
    )

    geographic_filter = geo_description or _describe_region(req)

    if not tour:
        return {
            "total_expected_species": 0.0,
            "window_minutes": window_minutes,
            "num_candidate_hotspots": int(hotspot_info.shape[0]),
            "itinerary": [],
            "leg_durations_minutes": [],
            "total_travel_minutes": 0,
            "route_geojson": None,
            "geographic_filter": geographic_filter,
        }

    itinerary = []
    leg_durations: list[int] = []
    clock = start_min
    cumulative = 0.0

    for pos, idx in enumerate(tour):
        if pos > 0:
            leg_min = int(round(travel_matrix[tour[pos - 1], idx]))
            leg_durations.append(leg_min)
            clock += leg_min

        row = hotspot_info.iloc[idx]
        duration_min = int(durations[idx])
        arrive = clock
        depart = arrive + duration_min
        clock = depart

        species_probs = []
        for s_idx, sp_name in enumerate(species_list):
            p = float(prob_matrix[idx, s_idx])
            if p >= 0.001:
                species_probs.append({"common_name": sp_name, "probability": round(p, 4)})
        species_probs.sort(key=lambda x: -x["probability"])

        cumulative += gains[pos]
        itinerary.append({
            "order": pos + 1,
            "locality_id": int(row["locality_id"]),
            "locality": row["locality"],
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "county": row["county"],
            "state": row["state"],
            "arrive": _format_hhmm(arrive),
            "depart": _format_hhmm(depart),
            "duration_minutes": duration_min,
            "marginal_gain": round(gains[pos], 2),
            "cumulative_expected": round(cumulative, 2),
            "target_species": species_probs[:20],
        })

    ordered_coords = [(it["latitude"], it["longitude"]) for it in itinerary]
    route_geojson = osrm_route_geojson(ordered_coords)

    total_expected = float(np.sum(1.0 - final_miss))
    return {
        "total_expected_species": round(total_expected, 2),
        "window_minutes": window_minutes,
        "num_candidate_hotspots": int(hotspot_info.shape[0]),
        "itinerary": itinerary,
        "leg_durations_minutes": leg_durations,
        "total_travel_minutes": int(round(total_travel)),
        "route_geojson": route_geojson,
        "geographic_filter": geographic_filter,
    }


def _describe_region(req: PlanBigDayRequest) -> str:
    parts = []
    if req.state_counties:
        parts.append(", ".join(sc.county for sc in req.state_counties))
    if req.states:
        parts.append(", ".join(req.states))
    if req.country:
        parts.append(req.country)
    return ", ".join(parts) if parts else "All areas"


@app.get("/api/search-hotspots")
def search_hotspots(
    q: str,
    country: str | None = None,
    states: str | None = None,
    state_counties: str | None = None,
):
    """Search hotspots by name for autocomplete."""
    if len(q) < 2:
        return []

    params = [f"%{q}%"]

    geo_filter = "1=1"
    if state_counties:
        pairs = []
        for pair in state_counties.split(","):
            parts = pair.split("|")
            if len(parts) == 2:
                pairs.append("(state = ? AND county = ?)")
                params.extend(parts)
        if pairs:
            geo_filter = f"({' OR '.join(pairs)})"
    elif states:
        state_list = states.split(",")
        placeholders = ", ".join("?" for _ in state_list)
        geo_filter = f"state IN ({placeholders})"
        params.extend(state_list)
        if country:
            geo_filter += " AND country = ?"
            params.append(country)
    elif country:
        geo_filter = "country = ?"
        params.append(country)

    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        rows = con.execute(
            f"SELECT locality_id, locality, latitude, longitude, county, state "
            f"FROM hotspots WHERE locality ILIKE ? AND {geo_filter} "
            f"ORDER BY locality",
            params,
        ).fetchall()
    finally:
        con.close()

    return [
        {
            "locality_id": r[0],
            "locality": r[1],
            "latitude": r[2],
            "longitude": r[3],
            "county": r[4],
            "state": r[5],
        }
        for r in rows
    ]


class HotspotDetailRequest(BaseModel):
    locality_id: int
    start_date: date
    end_date: date
    life_list: list[str] = []
    target_species: list[str] | None = None


@app.post("/api/hotspot-details")
def get_hotspot_details(req: HotspotDetailRequest):
    """Fetch full species probability data for a single hotspot."""
    days_of_year = date_range_to_days_of_year(req.start_date, req.end_date)

    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        hotspot_info, prob_matrix, species_list = load_probability_matrix(
            con, days_of_year, req.life_list,
            locality_ids=[req.locality_id],
            target_species=req.target_species,
        )
    finally:
        con.close()

    if prob_matrix.size == 0:
        # No species data — return basic hotspot info
        con = duckdb.connect(DB_PATH, read_only=True)
        try:
            row = con.execute(
                f"SELECT locality_id, locality, latitude, longitude, county, state "
                f"FROM hotspots WHERE locality_id = {req.locality_id}"
            ).fetchone()
        finally:
            con.close()
        if not row:
            raise HTTPException(status_code=404, detail="Hotspot not found")
        return {
            "locality_id": row[0],
            "locality": row[1],
            "latitude": row[2],
            "longitude": row[3],
            "county": row[4],
            "state": row[5],
            "target_species": [],
        }

    target_species = []
    for s_idx, sp_name in enumerate(species_list):
        p = float(prob_matrix[0, s_idx])
        if p >= 0.001:
            target_species.append({"common_name": sp_name, "probability": round(p, 4)})
    target_species.sort(key=lambda x: x["probability"], reverse=True)

    row = hotspot_info.iloc[0]
    result = {
        "locality_id": int(row["locality_id"]),
        "locality": row["locality"],
        "latitude": float(row["latitude"]),
        "longitude": float(row["longitude"]),
        "county": row["county"],
        "state": row["state"],
        "target_species": target_species,
    }

    result = add_recent_observations([result])[0]
    return result


# Serve the frontend
app.mount("/", StaticFiles(directory=str(BASE_DIR / "static"), html=True), name="static")

