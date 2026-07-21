"""
Big Day optimizer diagnostic harness.

Replicates the logic of api.run_plan_big_day() locally so we can observe
internal state (candidate counts, seed picks, travel-matrix sentinels,
passes-to-convergence) without touching production code.

Usage:
    python scripts/big_day_diag.py --state US-VT \
        --date 2026-05-10:2026-05-20 --window 05:00-20:00

    python scripts/big_day_diag.py \
        --counties "Chittenden,Addison,Franklin" \
        --date 2026-05-10:2026-05-20 --window 05:00-20:00 --cap 2000

All outputs are printed; nothing is written to disk.
"""
from __future__ import annotations

import argparse
import sys
import os
from datetime import date
from pathlib import Path
from typing import Optional
import itertools

import duckdb
import numpy as np

# Add repo root to path so we can import the project modules
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from hotspot_optimizer import (
    load_probability_matrix,
    load_hotspot_durations,
    plan_big_day,
    date_range_to_days_of_year,
    _generate_seeds,
    _greedy_insert,
    _local_search,
    _expected_species,
    _tour_time,
    DEFAULT_HOTSPOT_MINUTES,
)

DB_PATH = str(REPO_ROOT / "data" / "combined.duckdb")
OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "http://localhost:5000").rstrip("/")

VT_ALL_COUNTIES = [
    "Addison", "Bennington", "Caledonia", "Chittenden", "Essex",
    "Franklin", "Grand Isle", "Lamoille", "Orange", "Orleans",
    "Rutland", "Washington", "Windham", "Windsor",
]


def _osrm_duration_matrix(coords: list[tuple[float, float]]) -> np.ndarray:
    """Copied from api.py to avoid importing FastAPI."""
    from urllib.request import Request, urlopen
    import json
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


def _instrumented_local_search(
    tour: list[int],
    prob_matrix: np.ndarray,
    durations: np.ndarray,
    travel_matrix: np.ndarray,
    window_minutes: float,
    anchor: Optional[int] = None,
    max_passes: int = 50,
) -> tuple[list[int], int]:
    """Local search that also returns the number of improving passes found."""
    improving_passes = 0
    for pass_num in range(max_passes):
        from hotspot_optimizer import _two_opt
        tour = _two_opt(tour, travel_matrix)
        before_species = _expected_species(tour, prob_matrix)
        before_time = _tour_time(tour, durations, travel_matrix)
        improved = False

        for s_pos in range(len(tour)):
            reduced = tour[:s_pos] + tour[s_pos + 1:]
            candidate = _greedy_insert(
                reduced, prob_matrix, durations, travel_matrix, window_minutes
            )
            cand_species = _expected_species(candidate, prob_matrix)
            cand_time = _tour_time(candidate, durations, travel_matrix)
            if cand_species > before_species + 1e-6 or (
                abs(cand_species - before_species) < 1e-6 and cand_time + 1.0 < before_time
            ):
                tour = candidate
                before_species = cand_species
                before_time = cand_time
                improved = True
                improving_passes += 1
                break

        if anchor is not None and anchor in tour:
            reduced = [h for h in tour if h != anchor]
            from hotspot_optimizer import _greedy_insert as _gi
            candidate = _gi(
                reduced, prob_matrix, durations, travel_matrix, window_minutes,
                locked={anchor},
            )
            cand_species = _expected_species(candidate, prob_matrix)
            cand_time = _tour_time(candidate, durations, travel_matrix)
            if cand_species > before_species + 1e-6 or (
                abs(cand_species - before_species) < 1e-6 and cand_time + 1.0 < before_time
            ):
                tour = candidate
                before_species = cand_species
                before_time = cand_time
                improved = True
                improving_passes += 1

        if not improved:
            break

    return tour, improving_passes


def run_diagnostic(
    state_counties: Optional[list[tuple[str, str]]],
    states: Optional[list[str]],
    start_date: date,
    end_date: date,
    window_start_min: int,
    window_end_min: int,
    life_list: list[str],
    cap: int,
    max_passes: int,
    verbose: bool = True,
) -> dict:
    """Run a single big-day diagnostic run. Returns a summary dict."""
    window_minutes = float(window_end_min - window_start_min)
    days_of_year = date_range_to_days_of_year(start_date, end_date)

    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        hotspot_info, prob_matrix, species_list = load_probability_matrix(
            con, days_of_year, life_list,
            state_counties=state_counties,
            states=states,
        )
        pre_cap_count = prob_matrix.shape[0]

        if cap > 0 and prob_matrix.shape[0] > cap:
            scores = prob_matrix.sum(axis=1)
            top_idx = np.argsort(-scores)[:cap]
            top_idx_sorted = np.sort(top_idx)
            prob_matrix = prob_matrix[top_idx_sorted]
            hotspot_info = hotspot_info.iloc[top_idx_sorted].reset_index(drop=True)
        post_cap_count = prob_matrix.shape[0]

        if prob_matrix.size == 0:
            return {"error": "No candidate hotspots after filtering"}

        locality_id_list = [int(lid) for lid in hotspot_info["locality_id"].tolist()]
        median_map = load_hotspot_durations(con, locality_id_list)
    finally:
        con.close()

    durations = np.full(len(locality_id_list), DEFAULT_HOTSPOT_MINUTES, dtype=np.float64)
    for i, lid in enumerate(locality_id_list):
        if lid in median_map:
            durations[i] = median_map[lid]

    coords = [
        (float(hotspot_info.iloc[i]["latitude"]), float(hotspot_info.iloc[i]["longitude"]))
        for i in range(len(hotspot_info))
    ]

    travel_matrix = _osrm_duration_matrix(coords)
    sentinel_count = int(np.sum(travel_matrix >= 9999.0))
    total_cells = travel_matrix.size

    seeds = _generate_seeds(prob_matrix, travel_matrix)

    # Per-seed results with instrumented local search
    seed_results = []
    best_score = -1.0
    best_time = float("inf")
    best_tour: list[int] = []
    best_passes = 0

    for seed in seeds:
        if seed and durations[seed[0]] > window_minutes:
            seed_results.append({"seed": seed, "skipped": True})
            continue
        constructed = _greedy_insert(seed, prob_matrix, durations, travel_matrix, window_minutes)
        anchor = constructed[0] if constructed else None
        refined, improving_passes = _instrumented_local_search(
            constructed, prob_matrix, durations, travel_matrix, window_minutes,
            anchor=anchor, max_passes=max_passes,
        )
        score = _expected_species(refined, prob_matrix)
        total_time = _tour_time(refined, durations, travel_matrix)

        seed_name = None
        if seed:
            seed_name = hotspot_info.iloc[seed[0]]["locality"]
        seed_results.append({
            "seed": seed,
            "seed_name": seed_name,
            "constructed_len": len(constructed),
            "refined_len": len(refined),
            "expected_species": round(score, 4),
            "tour_minutes": round(total_time, 1),
            "improving_passes": improving_passes,
            "skipped": False,
        })

        if score > best_score + 1e-6 or (
            abs(score - best_score) < 1e-6 and total_time < best_time
        ):
            best_score = score
            best_time = total_time
            best_tour = refined
            best_passes = improving_passes

    # Reconstruct final miss for total expected
    S = prob_matrix.shape[1]
    miss = np.ones(S, dtype=np.float64)
    for idx in best_tour:
        miss *= 1.0 - prob_matrix[idx]
    total_expected = float(np.sum(1.0 - miss))

    # Tour composition
    tour_counties = []
    for idx in best_tour:
        row = hotspot_info.iloc[idx]
        tour_counties.append(f"{row['locality']} ({row['county']})")

    result = {
        "pre_cap_count": pre_cap_count,
        "post_cap_count": post_cap_count,
        "cap_applied": pre_cap_count > cap and cap > 0,
        "hotspots_dropped_by_cap": pre_cap_count - post_cap_count if pre_cap_count > cap and cap > 0 else 0,
        "travel_matrix_size": total_cells,
        "sentinel_count": sentinel_count,
        "sentinel_pct": round(100.0 * sentinel_count / total_cells, 2) if total_cells > 0 else 0,
        "seed_count": len(seeds),
        "seed_results": seed_results,
        "best_tour_len": len(best_tour),
        "best_improving_passes": best_passes,
        "total_expected_species": round(total_expected, 4),
        "total_tour_minutes": round(best_time, 1),
        "tour_composition": tour_counties,
    }

    if verbose:
        _print_diagnostic(result, cap, max_passes, state_counties, states)

    return result


def _print_diagnostic(result: dict, cap: int, max_passes: int,
                       state_counties, states) -> None:
    region = states[0] if (states and not state_counties) else (
        ", ".join(f"{c}" for _, c in state_counties) if state_counties else "unknown"
    )
    print(f"\n{'='*60}")
    print(f"Region: {region}  |  cap={cap}  max_passes={max_passes}")
    print(f"{'='*60}")
    print(f"Hotspots pre-cap:          {result['pre_cap_count']}")
    print(f"Hotspots post-cap:         {result['post_cap_count']}  (dropped {result['hotspots_dropped_by_cap']})")
    print(f"Travel matrix sentinels:   {result['sentinel_count']} / {result['travel_matrix_size']} cells ({result['sentinel_pct']}%)")
    print(f"\nSeeds ({result['seed_count']}):")
    for sr in result["seed_results"]:
        if sr.get("skipped"):
            print(f"  seed {sr['seed']} SKIPPED (too long for window)")
        else:
            print(f"  seed={sr['seed_name'] or 'empty'}  "
                  f"constructed={sr['constructed_len']}  refined={sr['refined_len']}  "
                  f"expected={sr['expected_species']}  improving_passes={sr['improving_passes']}")
    print(f"\nBest tour: {result['best_tour_len']} stops, "
          f"{result['total_tour_minutes']} min, "
          f"{result['best_improving_passes']} improving passes")
    print(f"Total expected species: {result['total_expected_species']}")
    print("Tour:")
    for i, stop in enumerate(result["tour_composition"], 1):
        print(f"  {i}. {stop}")


def _parse_hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def main():
    parser = argparse.ArgumentParser(description="Big Day diagnostic")
    parser.add_argument("--state", default="Vermont", help="State name as stored in DB e.g. Vermont")
    parser.add_argument("--counties", help="Comma-separated county names (within the state)")
    parser.add_argument("--date", default="2026-05-10:2026-05-20",
                        help="Date range YYYY-MM-DD:YYYY-MM-DD")
    parser.add_argument("--window", default="05:00-20:00",
                        help="Time window HH:MM-HH:MM")
    parser.add_argument("--cap", type=int, default=500, help="MAX_BIGDAY_CANDIDATES")
    parser.add_argument("--max-passes", type=int, default=50, help="local search max passes")
    args = parser.parse_args()

    date_parts = args.date.split(":")
    start_date = date.fromisoformat(date_parts[0])
    end_date = date.fromisoformat(date_parts[1])

    win_parts = args.window.split("-")
    window_start_min = _parse_hhmm(win_parts[0])
    window_end_min = _parse_hhmm(win_parts[1])

    if args.counties:
        county_list = [c.strip() for c in args.counties.split(",")]
        state_counties = [(args.state, c) for c in county_list]
        states = None
    else:
        state_counties = None
        states = [args.state]

    if not state_counties and not states:
        parser.error("Provide --state or --counties")

    run_diagnostic(
        state_counties=state_counties,
        states=states,
        start_date=start_date,
        end_date=end_date,
        window_start_min=window_start_min,
        window_end_min=window_end_min,
        life_list=[],
        cap=args.cap,
        max_passes=args.max_passes,
        verbose=True,
    )


if __name__ == "__main__":
    main()
