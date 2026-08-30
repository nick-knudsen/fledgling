import os
from collections.abc import Iterator

import duckdb
import pytest

# api.py reads this at import time (module-level `os.environ["EBIRD_API_KEY"]`),
# so it must be set before anything imports api - setdefault so a real key in
# the environment (if any) isn't clobbered.
os.environ.setdefault("EBIRD_API_KEY", "test-key")

# Hotspots span two states (VT, NH) and one country (US), with distinct
# checklist totals so ORDER BY total_checklists DESC is deterministic.
HOTSPOTS = [
    # locality_id, locality, latitude, longitude, county, state, country, total_checklists
    (1, "Hotspot A", 40.0, -70.0, "County1", "VT", "US", 100),
    (2, "Hotspot B", 41.0, -71.0, "County1", "VT", "US", 80),
    (3, "Hotspot C", 42.0, -72.0, "County2", "NH", "US", 50),
]

# All observations fall on day_of_year=100 so a date range covering that day
# picks them all up without needing to model the rolling window.
DAY_OF_YEAR = 100
WILSON_SCORES = [
    # locality_id, day_of_year, common_name, wilson_lower_bound
    (1, DAY_OF_YEAR, "American Robin", 0.9),
    (1, DAY_OF_YEAR, "Blue Jay", 0.5),
    (2, DAY_OF_YEAR, "American Robin", 0.3),
    (2, DAY_OF_YEAR, "Cedar Waxwing", 0.8),
    (3, DAY_OF_YEAR, "Blue Jay", 0.6),
    (3, DAY_OF_YEAR, "Cedar Waxwing", 0.2),
]


# One non-species category row to verify /api/species filters it out.
SPECIES: list[tuple[str, str, str, str, float, list[str], list[str], list[str]]] = [
    # species_code, common_name, scientific_name, category, taxon_order,
    # banding_codes, com_name_codes, sci_name_codes
    ("amerob", "American Robin", "Turdus migratorius", "species", 1.0, [], [], []),
    ("blujay", "Blue Jay", "Cyanocitta cristata", "species", 2.0, [], [], []),
    ("cedwax", "Cedar Waxwing", "Bombycilla cedrorum", "species", 3.0, [], [], []),
    ("robin/x", "Robin sp.", "Turdus sp.", "spuh", 4.0, [], [], []),
]


def _populate(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE hotspots (
            locality_id BIGINT,
            locality VARCHAR,
            latitude DOUBLE,
            longitude DOUBLE,
            county VARCHAR,
            state VARCHAR,
            country VARCHAR,
            total_checklists BIGINT
        )
    """)
    con.executemany("INSERT INTO hotspots VALUES (?, ?, ?, ?, ?, ?, ?, ?)", HOTSPOTS)

    con.execute("""
        CREATE TABLE rolling_wilson_score (
            locality_id BIGINT,
            day_of_year INTEGER,
            common_name VARCHAR,
            wilson_lower_bound DOUBLE
        )
    """)
    con.executemany(
        "INSERT INTO rolling_wilson_score VALUES (?, ?, ?, ?)", WILSON_SCORES
    )

    con.execute("""
        CREATE TABLE species (
            species_code VARCHAR,
            common_name VARCHAR,
            scientific_name VARCHAR,
            category VARCHAR,
            taxon_order DOUBLE,
            banding_codes VARCHAR[],
            com_name_codes VARCHAR[],
            sci_name_codes VARCHAR[]
        )
    """)
    con.executemany("INSERT INTO species VALUES (?, ?, ?, ?, ?, ?, ?, ?)", SPECIES)


@pytest.fixture
def sample_db_path(tmp_path) -> Iterator[str]:
    """Path to a small on-disk DuckDB file with known hotspots/wilson-score data.

    File-based (not :memory:) because optimize_hotspots opens its own read-only
    connection to db_path, which needs to see data committed by a separate
    writer connection.
    """
    db_path = str(tmp_path / "test.duckdb")
    con = duckdb.connect(db_path)
    try:
        _populate(con)
    finally:
        con.close()
    yield db_path


@pytest.fixture
def sample_con() -> Iterator[duckdb.DuckDBPyConnection]:
    """In-memory DuckDB connection with the same fixture data, for tests that
    call load_probability_matrix directly with a connection they already hold.
    """
    con = duckdb.connect(":memory:")
    try:
        _populate(con)
        yield con
    finally:
        con.close()
