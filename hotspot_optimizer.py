import duckdb
import numpy as np
import pandas as pd
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


@dataclass
class SpeciesProb:
    common_name: str
    probability: float


@dataclass
class HotspotResult:
    rank: int
    locality: str
    locality_id: int
    latitude: float
    longitude: float
    county: str
    state: str
    marginal_gain: float
    cumulative_expected: float
    target_species: list[SpeciesProb]


@dataclass
class OptimizationResult:
    selected_hotspots: list[HotspotResult]
    total_expected_lifers: float
    num_candidate_hotspots: int
    num_potential_lifers: int
    date_range: tuple[date, date]
    geographic_filter: str
    species_combined_probs: list[SpeciesProb]
    num_total_hotspots: int = 0


def date_range_to_days_of_year(start_date: date, end_date: date) -> list[int]:
    """Convert a date range to a list of day_of_year integers (1-366).

    Handles year-boundary wrapping (e.g., Dec 28 - Jan 3).
    """
    days = []
    current = start_date
    if end_date < start_date:
        end_date = end_date.replace(year=end_date.year + 1)
    while current <= end_date:
        days.append(current.timetuple().tm_yday)
        current += timedelta(days=1)
    return days


def load_probability_matrix(
    con: duckdb.DuckDBPyConnection,
    days_of_year: list[int],
    life_list_names: list[str],
    state_counties: Optional[list[tuple[str, str]]] = None,
    states: Optional[list[str]] = None,
    country: Optional[str] = None,
    locality_ids: Optional[list[int]] = None,
    target_species: Optional[list[str]] = None,
    exclude_locality_ids: Optional[list[int]] = None,
) -> tuple[pd.DataFrame, np.ndarray, list[str]]:
    """Load and prepare the probability matrix from the database.

    Registers life_list_names as a temporary view to avoid storing user data.

    Returns:
        hotspot_info: DataFrame with locality, locality_id, latitude, longitude, county
        prob_matrix: numpy array shape (num_hotspots, num_species)
        species_list: list of species names (columns of prob_matrix)
    """
    # For wide date ranges, sample every Nth day to reduce the scan size.
    # Rolling windows make adjacent days nearly identical, so the MAX()
    # aggregate gives very similar results with fewer days.
    if len(days_of_year) > 30:
        step = max(1, len(days_of_year) // 30)
        sampled_days = days_of_year[::step]
        # Always include the last day so the range endpoints are represented
        if days_of_year[-1] not in sampled_days:
            sampled_days.append(days_of_year[-1])
        day_list = ", ".join(str(d) for d in sampled_days)
    else:
        day_list = ", ".join(str(d) for d in days_of_year)

    geo_filter = "1=1"
    params: list = []
    if locality_ids:
        id_placeholders = ", ".join("?" for _ in locality_ids)
        geo_filter = f"h.locality_id IN ({id_placeholders})"
        params.extend(locality_ids)
    elif state_counties:
        pairs = " OR ".join("(h.state = ? AND h.county = ?)" for _ in state_counties)
        geo_filter = f"({pairs})"
        for s, c in state_counties:
            params.extend([s, c])
    elif states:
        state_placeholders = ", ".join("?" for _ in states)
        geo_filter = f"h.state IN ({state_placeholders})"
        params.extend(states)
        if country:
            geo_filter += " AND h.country = ?"
            params.append(country)
    elif country:
        geo_filter = "h.country = ?"
        params.append(country)

    if exclude_locality_ids:
        exclude_placeholders = ", ".join("?" for _ in exclude_locality_ids)
        geo_filter += f" AND h.locality_id NOT IN ({exclude_placeholders})"
        params.extend(exclude_locality_ids)

    if target_species:
        species_placeholders = ", ".join("?" for _ in target_species)
        species_filter = f"r.common_name IN ({species_placeholders})"
        params.extend(target_species)
    elif life_list_names:
        life_df = pd.DataFrame({"common_name": life_list_names})
        con.register("life_list_view", life_df)
        species_filter = "r.common_name NOT IN (SELECT common_name FROM life_list_view)"
    else:
        species_filter = "1=1"

    # Cap hotspots to prevent memory/timeout issues on broad queries.
    # Keep only the most-observed hotspots when the candidate set is too large.
    MAX_HOTSPOTS = 5000

    query = f"""
    WITH candidate_hotspots AS (
        SELECT h.locality_id
        FROM hotspots h
        WHERE {geo_filter}
        ORDER BY h.total_checklists DESC
        LIMIT {MAX_HOTSPOTS}
    ),
    filtered_freq AS (
        SELECT
            r.locality_id,
            r.common_name,
            MAX(GREATEST(r.wilson_lower_bound, 0)) AS detection_prob
        FROM rolling_wilson_score r
        WHERE r.day_of_year IN ({day_list})
          AND {species_filter}
          AND r.locality_id IN (SELECT locality_id FROM candidate_hotspots)
        GROUP BY r.locality_id, r.common_name
    )
    SELECT
        f.locality_id,
        h.locality,
        h.latitude,
        h.longitude,
        h.county,
        h.state,
        f.common_name,
        f.detection_prob
    FROM filtered_freq f
    JOIN hotspots h ON f.locality_id = h.locality_id
    WHERE f.detection_prob > 0
    ORDER BY f.locality_id, f.common_name
    """

    df = con.execute(query, params).fetchdf()

    if not target_species and life_list_names:
        con.unregister("life_list_view")

    if df.empty:
        empty_info = pd.DataFrame(columns=["locality", "locality_id", "latitude", "longitude", "county", "state"])
        return empty_info, np.empty((0, 0)), []

    pivot = df.pivot_table(
        index="locality_id",
        columns="common_name",
        values="detection_prob",
        fill_value=0.0,
    )

    species_list = list(pivot.columns)
    prob_matrix = pivot.values.astype(np.float64)

    hotspot_info = (
        df[["locality_id", "locality", "latitude", "longitude", "county", "state"]]
        .drop_duplicates(subset="locality_id")
        .set_index("locality_id")
        .loc[pivot.index]
        .reset_index()
    )

    return hotspot_info, prob_matrix, species_list


DEFAULT_HOTSPOT_MINUTES = 15
MIN_SAMPLE_COUNT = 5


def load_hotspot_durations(
    con: duckdb.DuckDBPyConnection, locality_ids: list[int]
) -> dict[int, int]:
    """Median `duration_minutes` per hotspot from the sampling table.

    Hotspots with fewer than MIN_SAMPLE_COUNT checklists (or no entries) are
    omitted; callers should fall back to DEFAULT_HOTSPOT_MINUTES.
    """
    if not locality_ids:
        return {}
    placeholders = ", ".join("?" for _ in locality_ids)
    rows = con.execute(
        f"""
        SELECT locality_id,
               CAST(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY duration_minutes) AS INTEGER) AS median_minutes,
               COUNT(*) AS sample_count
        FROM sampling
        WHERE locality_id IN ({placeholders})
          AND duration_minutes BETWEEN 5 AND 480
        GROUP BY locality_id
        HAVING COUNT(*) >= {MIN_SAMPLE_COUNT}
        """,
        locality_ids,
    ).fetchall()
    return {int(lid): int(m) for lid, m, _ in rows}


def greedy_optimize(
    prob_matrix: np.ndarray, k: int
) -> tuple[list[int], list[float], np.ndarray]:
    """Run greedy submodular optimization.

    Args:
        prob_matrix: shape (H, S), detection probabilities per hotspot/species
        k: number of hotspots to select

    Returns:
        selected_indices: row indices into prob_matrix
        marginal_gains: expected new lifers added at each step
        final_miss_probs: shape (S,), miss probability for each species
    """
    H, S = prob_matrix.shape
    k = min(k, H)

    miss_prob = np.ones(S, dtype=np.float64)
    selected = []
    gains = []
    available_mask = np.ones(H, dtype=bool)

    for _ in range(k):
        candidate_gains = prob_matrix @ miss_prob
        candidate_gains[~available_mask] = -1.0
        best_idx = int(np.argmax(candidate_gains))
        best_gain = candidate_gains[best_idx]

        if best_gain <= 0:
            break

        selected.append(best_idx)
        gains.append(float(best_gain))
        available_mask[best_idx] = False
        miss_prob *= 1.0 - prob_matrix[best_idx]

    return selected, gains, miss_prob


def _expected_species(tour: list[int], prob_matrix: np.ndarray) -> float:
    if not tour:
        return 0.0
    miss = np.prod(1.0 - prob_matrix[tour], axis=0)
    return float(np.sum(1.0 - miss))


def _tour_time(tour: list[int], durations: np.ndarray, travel_matrix: np.ndarray) -> float:
    if not tour:
        return 0.0
    total = float(durations[tour].sum())
    for i in range(len(tour) - 1):
        total += float(travel_matrix[tour[i], tour[i + 1]])
    return total


def _insertion_delta(
    tour: list[int], pos: int, h: int,
    durations: np.ndarray, travel_matrix: np.ndarray,
) -> float:
    dt = float(durations[h])
    if not tour:
        return dt
    if pos == 0:
        dt += float(travel_matrix[h, tour[0]])
    elif pos == len(tour):
        dt += float(travel_matrix[tour[-1], h])
    else:
        prev_h, next_h = tour[pos - 1], tour[pos]
        dt += float(
            travel_matrix[prev_h, h]
            + travel_matrix[h, next_h]
            - travel_matrix[prev_h, next_h]
        )
    return dt


def _greedy_insert(
    seed_tour: list[int],
    prob_matrix: np.ndarray,
    durations: np.ndarray,
    travel_matrix: np.ndarray,
    window_minutes: float,
    locked: Optional[set[int]] = None,
) -> list[int]:
    """Extend `seed_tour` by best-ratio insertion until nothing fits."""
    H, S = prob_matrix.shape
    tour = list(seed_tour)
    in_tour = set(tour)
    if locked:
        in_tour |= locked

    if tour:
        miss = np.prod(1.0 - prob_matrix[tour], axis=0)
    else:
        miss = np.ones(S, dtype=np.float64)
    current_time = _tour_time(tour, durations, travel_matrix)

    if current_time > window_minutes:
        return tour

    while True:
        gains = prob_matrix @ miss  # (H,)
        best_score = 0.0
        best_choice: Optional[tuple[int, int, float]] = None
        for h in range(H):
            if h in in_tour or gains[h] <= 0:
                continue
            for pos in range(len(tour) + 1):
                dt = _insertion_delta(tour, pos, h, durations, travel_matrix)
                if current_time + dt > window_minutes:
                    continue
                score = gains[h] / dt
                if score > best_score:
                    best_score = score
                    best_choice = (h, pos, dt)
        if best_choice is None:
            break
        h, pos, dt = best_choice
        tour.insert(pos, h)
        in_tour.add(h)
        miss *= 1.0 - prob_matrix[h]
        current_time += dt

    return tour


def _two_opt(tour: list[int], travel_matrix: np.ndarray) -> list[int]:
    """Reverse segments to reduce travel time. Species coverage is invariant."""
    n = len(tour)
    if n < 4:
        return tour
    improved = True
    while improved:
        improved = False
        for i in range(n - 1):
            for j in range(i + 2, n):
                a = tour[i - 1] if i > 0 else None
                b = tour[i]
                c = tour[j]
                d = tour[j + 1] if j + 1 < n else None
                delta = 0.0
                if a is not None:
                    delta += travel_matrix[a, c] - travel_matrix[a, b]
                if d is not None:
                    delta += travel_matrix[b, d] - travel_matrix[c, d]
                if delta < -1e-9:
                    tour[i : j + 1] = tour[i : j + 1][::-1]
                    improved = True
                    break
            if improved:
                break
    return tour


def _local_search(
    tour: list[int],
    prob_matrix: np.ndarray,
    durations: np.ndarray,
    travel_matrix: np.ndarray,
    window_minutes: float,
    anchor: Optional[int] = None,
    max_passes: int = 50,
) -> list[int]:
    """2-opt + drop-and-reinsert + anchor-removal."""
    for _ in range(max_passes):
        tour = _two_opt(tour, travel_matrix)
        before_species = _expected_species(tour, prob_matrix)
        before_time = _tour_time(tour, durations, travel_matrix)

        improved = False

        for s_pos in range(len(tour)):
            reduced = tour[:s_pos] + tour[s_pos + 1 :]
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
                break

        if anchor is not None and anchor in tour:
            reduced = [h for h in tour if h != anchor]
            candidate = _greedy_insert(
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

        if not improved:
            break
    return tour


def _generate_seeds(
    prob_matrix: np.ndarray, travel_matrix: np.ndarray
) -> list[list[int]]:
    H = prob_matrix.shape[0]
    seeds: list[list[int]] = [[]]
    if H == 0:
        return seeds

    single_gains = prob_matrix.sum(axis=1)
    for i in np.argsort(-single_gains)[:3]:
        seeds.append([int(i)])

    if H > 1:
        mean_travel = travel_matrix.mean(axis=1)
        for i in np.argsort(mean_travel)[:3]:
            seeds.append([int(i)])

    seen: set[tuple[int, ...]] = set()
    unique: list[list[int]] = []
    for s in seeds:
        key = tuple(s)
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


def plan_big_day(
    prob_matrix: np.ndarray,
    durations: np.ndarray,
    travel_matrix: np.ndarray,
    window_minutes: float,
) -> tuple[list[int], list[float], np.ndarray, float]:
    """Multi-start greedy insertion + local search for orienteering.

    Args:
        prob_matrix: (H, S) detection probabilities
        durations: (H,) minutes at each hotspot
        travel_matrix: (H, H) driving minutes between hotspots
        window_minutes: total time budget

    Returns:
        ordered_tour: list of hotspot row indices in visit order
        marginal_gains: expected new species added by each stop (in visit order)
        final_miss_probs: (S,) miss probability per species after all stops
        total_travel_minutes: sum of leg durations in the final tour
    """
    H, S = prob_matrix.shape
    if H == 0:
        return [], [], np.ones(S if S > 0 else 0, dtype=np.float64), 0.0

    seeds = _generate_seeds(prob_matrix, travel_matrix)

    best_tour: list[int] = []
    best_score = -1.0
    best_time = float("inf")

    for seed in seeds:
        if seed and durations[seed[0]] > window_minutes:
            continue

        constructed = _greedy_insert(
            seed, prob_matrix, durations, travel_matrix, window_minutes
        )
        anchor = constructed[0] if constructed else None
        refined = _local_search(
            constructed, prob_matrix, durations, travel_matrix, window_minutes,
            anchor=anchor,
        )

        score = _expected_species(refined, prob_matrix)
        total_time = _tour_time(refined, durations, travel_matrix)
        if score > best_score + 1e-6 or (
            abs(score - best_score) < 1e-6 and total_time < best_time
        ):
            best_score = score
            best_time = total_time
            best_tour = refined

    miss = np.ones(S, dtype=np.float64)
    gains = []
    for idx in best_tour:
        gain = float(prob_matrix[idx] @ miss)
        gains.append(gain)
        miss *= 1.0 - prob_matrix[idx]

    total_travel = sum(
        float(travel_matrix[best_tour[i], best_tour[i + 1]])
        for i in range(len(best_tour) - 1)
    )
    return best_tour, gains, miss, total_travel


def optimize_hotspots(
    db_path: str,
    life_list_names: list[str],
    start_date: date,
    end_date: date,
    k: int = 5,
    state_counties: Optional[list[tuple[str, str]]] = None,
    states: Optional[list[str]] = None,
    country: Optional[str] = None,
    locality_ids: Optional[list[int]] = None,
    target_species: Optional[list[str]] = None,
    exclude_locality_ids: Optional[list[int]] = None,
) -> OptimizationResult:
    """Main entry point for the hotspot optimizer."""
    days_of_year = date_range_to_days_of_year(start_date, end_date)

    geo_parts = []
    if state_counties:
        geo_parts.append(", ".join(c for _, c in state_counties))
    if states:
        geo_parts.append(", ".join(states))
    if country:
        geo_parts.append(country)
    geo_description = ", ".join(geo_parts) if geo_parts else "All areas"

    con = duckdb.connect(db_path, read_only=True)
    try:
        hotspot_info, prob_matrix, species_list = load_probability_matrix(
            con, days_of_year, life_list_names,
            state_counties=state_counties, states=states, country=country,
            locality_ids=locality_ids, target_species=target_species,
            exclude_locality_ids=exclude_locality_ids,
        )
    finally:
        con.close()

    if prob_matrix.size == 0:
        return OptimizationResult(
            selected_hotspots=[],
            total_expected_lifers=0.0,
            num_candidate_hotspots=0,
            num_potential_lifers=0,
            date_range=(start_date, end_date),
            geographic_filter=geo_description,
            species_combined_probs=[],
        )

    selected_indices, marginal_gains, final_miss_probs = greedy_optimize(prob_matrix, k)

    # Assemble results
    hotspots = []
    cumulative = 0.0
    for rank_idx, (mat_idx, gain) in enumerate(zip(selected_indices, marginal_gains)):
        cumulative += gain
        row = hotspot_info.iloc[mat_idx]

        species_probs = []
        for s_idx, sp_name in enumerate(species_list):
            p = prob_matrix[mat_idx, s_idx]
            if p >= 0.001:
                species_probs.append(SpeciesProb(sp_name, float(p)))
        species_probs.sort(key=lambda x: x.probability, reverse=True)

        hotspots.append(HotspotResult(
            rank=rank_idx + 1,
            locality=row["locality"],
            locality_id=int(row["locality_id"]),
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            county=row["county"],
            state=row["state"],
            marginal_gain=gain,
            cumulative_expected=cumulative,
            target_species=species_probs,
        ))

    # Combined probability for each species across all selected hotspots
    combined_probs = []
    for s_idx, sp_name in enumerate(species_list):
        combined_p = 1.0 - final_miss_probs[s_idx]
        if combined_p >= 0.001:
            combined_probs.append(SpeciesProb(sp_name, combined_p))
    combined_probs.sort(key=lambda x: x.probability, reverse=True)

    return OptimizationResult(
        selected_hotspots=hotspots,
        total_expected_lifers=cumulative,
        num_candidate_hotspots=len(hotspot_info),
        num_potential_lifers=len(combined_probs),
        date_range=(start_date, end_date),
        geographic_filter=geo_description,
        species_combined_probs=combined_probs,
    )
