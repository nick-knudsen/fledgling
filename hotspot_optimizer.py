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
    target_species: Optional[str] = None,
    exclude_locality_ids: Optional[list[int]] = None,
) -> tuple[pd.DataFrame, np.ndarray, list[str]]:
    """Load and prepare the probability matrix from the database.

    Registers life_list_names as a temporary view to avoid storing user data.

    Returns:
        hotspot_info: DataFrame with locality, locality_id, latitude, longitude, county
        prob_matrix: numpy array shape (num_hotspots, num_species)
        species_list: list of species names (columns of prob_matrix)
    """
    day_list = ", ".join(str(d) for d in days_of_year)

    geo_filter = "1=1"
    if locality_ids:
        id_list = ", ".join(str(i) for i in locality_ids)
        geo_filter = f"h.locality_id IN ({id_list})"
    elif state_counties:
        pairs = " OR ".join(f"(h.state = '{s}' AND h.county = '{c}')" for s, c in state_counties)
        geo_filter = f"({pairs})"
    elif states:
        state_list = ", ".join(f"'{s}'" for s in states)
        geo_filter = f"h.state IN ({state_list})"
        if country:
            geo_filter += f" AND h.country = '{country}'"
    elif country:
        geo_filter = f"h.country = '{country}'"

    if exclude_locality_ids:
        exclude_list = ", ".join(str(i) for i in exclude_locality_ids)
        geo_filter += f" AND h.locality_id NOT IN ({exclude_list})"

    if target_species:
        species_filter = f"r.common_name = '{target_species.replace(chr(39), chr(39)+chr(39))}'"
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
        f.common_name,
        f.detection_prob
    FROM filtered_freq f
    JOIN hotspots h ON f.locality_id = h.locality_id
    WHERE f.detection_prob > 0
    ORDER BY f.locality_id, f.common_name
    """

    df = con.execute(query).fetchdf()

    if not target_species and life_list_names:
        con.unregister("life_list_view")

    if df.empty:
        empty_info = pd.DataFrame(columns=["locality", "locality_id", "latitude", "longitude", "county"])
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
        df[["locality_id", "locality", "latitude", "longitude", "county"]]
        .drop_duplicates(subset="locality_id")
        .set_index("locality_id")
        .loc[pivot.index]
        .reset_index()
    )

    return hotspot_info, prob_matrix, species_list


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
    target_species: Optional[str] = None,
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
