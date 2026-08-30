from datetime import date

import numpy as np
import pytest

from hotspot_optimizer import (
    date_range_to_days_of_year,
    greedy_optimize,
    load_probability_matrix,
    optimize_hotspots,
)

# --- date_range_to_days_of_year -------------------------------------------


def test_date_range_single_day():
    assert date_range_to_days_of_year(date(2023, 4, 10), date(2023, 4, 10)) == [100]


def test_date_range_normal_span():
    days = date_range_to_days_of_year(date(2023, 1, 1), date(2023, 1, 5))
    assert days == [1, 2, 3, 4, 5]


def test_date_range_wraps_year_boundary():
    # Dec 28 - Jan 3: should count forward through Dec 31, wrapping into
    # the next year's day-of-year numbering for Jan 1-3.
    days = date_range_to_days_of_year(date(2023, 12, 28), date(2023, 1, 3))
    assert days == [362, 363, 364, 365, 1, 2, 3]


# --- greedy_optimize ---------------------------------------------------
# 3 hotspots x 3 species, hand-computable submodular selection order:
#   hotspot 0: [0.9, 0.5, 0.0]
#   hotspot 1: [0.3, 0.0, 0.8]
#   hotspot 2: [0.0, 0.6, 0.2]
# Row-sum-greedy picks hotspot 0 first (gain 1.4); after down-weighting by
# (1 - p), hotspot 1 beats hotspot 2 next (0.83 vs 0.5); hotspot 2 last.

PROB_MATRIX = np.array(
    [
        [0.9, 0.5, 0.0],
        [0.3, 0.0, 0.8],
        [0.0, 0.6, 0.2],
    ]
)


def test_greedy_optimize_picks_highest_value_first():
    selected, gains, _ = greedy_optimize(PROB_MATRIX, k=1)
    assert selected == [0]
    assert gains == pytest.approx([1.4])


def test_greedy_optimize_submodular_order_and_gains():
    selected, gains, final_miss = greedy_optimize(PROB_MATRIX, k=3)
    assert selected == [0, 1, 2]
    assert gains == pytest.approx([1.4, 0.83, 0.34])
    assert final_miss == pytest.approx([0.07, 0.2, 0.16])


def test_greedy_optimize_no_duplicate_selections():
    selected, _, _ = greedy_optimize(PROB_MATRIX, k=3)
    assert len(selected) == len(set(selected))


def test_greedy_optimize_k_larger_than_hotspot_count_is_clamped():
    selected, gains, _ = greedy_optimize(PROB_MATRIX, k=100)
    assert len(selected) == 3
    assert len(gains) == 3


def test_greedy_optimize_k_zero_returns_nothing():
    selected, gains, final_miss = greedy_optimize(PROB_MATRIX, k=0)
    assert selected == []
    assert gains == []
    assert final_miss == pytest.approx([1.0, 1.0, 1.0])


def test_greedy_optimize_stops_early_when_no_positive_gain():
    # A hotspot with all-zero probabilities never yields a positive gain,
    # so greedy should stop selecting once every remaining candidate is 0.
    matrix = np.array([[0.5, 0.0], [0.0, 0.0]])
    selected, gains, _ = greedy_optimize(matrix, k=2)
    assert selected == [0]
    assert gains == pytest.approx([0.5])


def test_greedy_optimize_empty_matrix():
    selected, gains, final_miss = greedy_optimize(np.empty((0, 0)), k=5)
    assert selected == []
    assert gains == []
    assert final_miss.shape == (0,)


# --- load_probability_matrix --------------------------------------------
# Fixture data (see conftest.py): 3 hotspots (loc 1-3), all observed on
# day_of_year=100. Species alphabetical order: American Robin, Blue Jay,
# Cedar Waxwing.


def test_load_probability_matrix_no_filters_returns_all_hotspots(sample_con):
    hotspot_info, prob_matrix, species_list = load_probability_matrix(
        sample_con, days_of_year=[100], life_list_names=[]
    )
    assert list(hotspot_info["locality_id"]) == [1, 2, 3]
    assert species_list == ["American Robin", "Blue Jay", "Cedar Waxwing"]
    assert prob_matrix == pytest.approx(
        np.array(
            [
                [0.9, 0.5, 0.0],
                [0.3, 0.0, 0.8],
                [0.0, 0.6, 0.2],
            ]
        )
    )


def test_load_probability_matrix_filters_by_state(sample_con):
    hotspot_info, _, _ = load_probability_matrix(
        sample_con, days_of_year=[100], life_list_names=[], states=["NH"]
    )
    assert list(hotspot_info["locality_id"]) == [3]


def test_load_probability_matrix_filters_by_state_county(sample_con):
    hotspot_info, _, _ = load_probability_matrix(
        sample_con,
        days_of_year=[100],
        life_list_names=[],
        state_counties=[("NH", "County2")],
    )
    assert list(hotspot_info["locality_id"]) == [3]


def test_load_probability_matrix_states_and_country_combine_with_and(sample_con):
    hotspot_info, _, _ = load_probability_matrix(
        sample_con,
        days_of_year=[100],
        life_list_names=[],
        states=["VT"],
        country="US",
    )
    assert list(hotspot_info["locality_id"]) == [1, 2]


def test_load_probability_matrix_filters_by_locality_ids(sample_con):
    hotspot_info, _, _ = load_probability_matrix(
        sample_con, days_of_year=[100], life_list_names=[], locality_ids=[2, 3]
    )
    assert list(hotspot_info["locality_id"]) == [2, 3]


def test_load_probability_matrix_excludes_locality_ids(sample_con):
    hotspot_info, _, _ = load_probability_matrix(
        sample_con,
        days_of_year=[100],
        life_list_names=[],
        exclude_locality_ids=[1],
    )
    assert list(hotspot_info["locality_id"]) == [2, 3]


def test_load_probability_matrix_life_list_excludes_seen_species(sample_con):
    _, prob_matrix, species_list = load_probability_matrix(
        sample_con,
        days_of_year=[100],
        life_list_names=["American Robin"],
    )
    assert species_list == ["Blue Jay", "Cedar Waxwing"]
    assert prob_matrix == pytest.approx(
        np.array(
            [
                [0.5, 0.0],
                [0.0, 0.8],
                [0.6, 0.2],
            ]
        )
    )


def test_load_probability_matrix_target_species_overrides_life_list(sample_con):
    hotspot_info, prob_matrix, species_list = load_probability_matrix(
        sample_con,
        days_of_year=[100],
        life_list_names=["Cedar Waxwing"],  # would normally exclude it
        target_species=["Cedar Waxwing"],
    )
    assert species_list == ["Cedar Waxwing"]
    assert list(hotspot_info["locality_id"]) == [2, 3]
    assert prob_matrix == pytest.approx(np.array([[0.8], [0.2]]))


def test_load_probability_matrix_no_matching_days_returns_empty(sample_con):
    hotspot_info, prob_matrix, species_list = load_probability_matrix(
        sample_con, days_of_year=[1], life_list_names=[]
    )
    assert hotspot_info.empty
    assert prob_matrix.shape == (0, 0)
    assert species_list == []


# --- optimize_hotspots (end-to-end against a real DuckDB file) ----------


def test_optimize_hotspots_end_to_end(sample_db_path):
    result = optimize_hotspots(
        sample_db_path,
        life_list_names=[],
        start_date=date(2023, 4, 10),
        end_date=date(2023, 4, 10),
        k=3,
    )

    assert [h.locality_id for h in result.selected_hotspots] == [1, 2, 3]
    assert [h.rank for h in result.selected_hotspots] == [1, 2, 3]
    assert [h.marginal_gain for h in result.selected_hotspots] == pytest.approx(
        [1.4, 0.83, 0.34]
    )
    # Cumulative expected lifers should be monotonically increasing.
    cumulative = [h.cumulative_expected for h in result.selected_hotspots]
    assert cumulative == sorted(cumulative)
    assert cumulative[-1] == pytest.approx(result.total_expected_lifers)

    assert result.num_candidate_hotspots == 3
    assert result.num_potential_lifers == 3
    assert result.geographic_filter == "All areas"


def test_optimize_hotspots_geographic_filter_description(sample_db_path):
    result = optimize_hotspots(
        sample_db_path,
        life_list_names=[],
        start_date=date(2023, 4, 10),
        end_date=date(2023, 4, 10),
        k=1,
        states=["VT"],
    )
    assert result.geographic_filter == "VT"
    assert result.num_candidate_hotspots == 2


def test_optimize_hotspots_no_data_returns_empty_result(sample_db_path):
    result = optimize_hotspots(
        sample_db_path,
        life_list_names=[],
        start_date=date(2023, 1, 1),
        end_date=date(2023, 1, 1),
        k=5,
    )
    assert result.selected_hotspots == []
    assert result.total_expected_lifers == 0.0
    assert result.num_candidate_hotspots == 0
