import os
import sys
import duckdb as dk


def run_pipeline(region_code, input_path, combined_db_path,
                 memory_limit="2GB", temp_dir="data/tmp"):
    """Process one eBird TSV into hotspots + rolling_wilson_score tables.

    Staging work happens in a temp DuckDB file. Final results are inserted
    into the combined database via ATTACH.

    Returns {"hotspots": int, "wilson_rows": int} on success.
    """
    os.makedirs(temp_dir, exist_ok=True)
    staging_db_path = os.path.join(temp_dir, f"{region_code}_staging.duckdb")

    # Clean up any leftover staging DB from a previous failed run
    if os.path.exists(staging_db_path):
        os.remove(staging_db_path)

    con = dk.connect(staging_db_path)
    con.execute("PRAGMA enable_print_progress_bar;")
    con.execute("PRAGMA progress_bar_time=500;")
    con.execute(f"SET temp_directory = '{temp_dir}';")
    con.execute(f"SET memory_limit = '{memory_limit}';")
    con.execute("SET preserve_insertion_order = false;")

    try:
        _run_staging(con, input_path)
        stats = _insert_into_combined(con, combined_db_path)
    finally:
        con.close()
        if os.path.exists(staging_db_path):
            os.remove(staging_db_path)

    return stats


def _run_staging(con, input_path):
    """Run the full staging pipeline: read, dedup, filter, build tables."""

    # Step 1: read CSV, filter, and rename columns
    print(f"  Reading {input_path}...")
    con.execute(f"""--sql
    DROP TABLE IF EXISTS sightings_staging;
    CREATE TABLE sightings_staging AS
    WITH sightings_raw AS (
        SELECT
            "CATEGORY",
            "COMMON NAME",
            "OBSERVATION COUNT",
            "COUNTRY",
            "STATE",
            "STATE CODE",
            "COUNTY",
            "LOCALITY",
            "LOCALITY ID",
            "LOCALITY TYPE",
            "LATITUDE",
            "LONGITUDE",
            "OBSERVATION DATE",
            "SAMPLING EVENT IDENTIFIER",
            "ALL SPECIES REPORTED",
            "GROUP IDENTIFIER"
        FROM read_csv_auto('{input_path}',
            delim='\t',
            quote='',
            types={{
                'LATITUDE': 'FLOAT',
                'LONGITUDE': 'FLOAT',
                'OBSERVATION DATE': 'DATE',
                'ALL SPECIES REPORTED': 'BOOLEAN'
            }})
    )
    SELECT
        "COMMON NAME" as common_name,
        "COUNTRY" as country,
        "STATE" as state,
        "STATE CODE" as state_code,
        "COUNTY" as county,
        "LOCALITY" as locality,
        TRY_CAST(REGEXP_EXTRACT("LOCALITY ID", '(\\d+)$') AS BIGINT) AS locality_id,
        "LATITUDE" as latitude,
        "LONGITUDE" as longitude,
        "OBSERVATION DATE" as observation_date,
        TRY_CAST(REGEXP_EXTRACT("SAMPLING EVENT IDENTIFIER", '(\\d+)$') AS BIGINT) AS sampling_id,
        TRY_CAST(REGEXP_EXTRACT("GROUP IDENTIFIER", '(\\d+)$') AS BIGINT) AS group_id
    FROM sightings_raw
    WHERE "LOCALITY TYPE" = 'H' AND
        ("CATEGORY" IN ('species', 'issf', 'form', 'domestic')) AND
        "ALL SPECIES REPORTED" IS TRUE AND
        "OBSERVATION COUNT" != '0' AND
        "COMMON NAME" NOT LIKE '% sp.%'
    ;
    """)

    # Step 2: deduplicate group checklists
    # Uses GROUP BY + JOIN instead of ROW_NUMBER window function because
    # window functions don't support spilling to disk in DuckDB
    print("  Deduplicating group checklists...")
    con.execute("""--sql
    DROP TABLE IF EXISTS sightings_deduped;
    CREATE TABLE sightings_deduped AS
    SELECT common_name, country, state, state_code, county, locality,
        locality_id, latitude, longitude, observation_date, sampling_id, group_id
    FROM sightings_staging WHERE group_id IS NULL

    UNION ALL

    SELECT s.common_name, s.country, s.state, s.state_code, s.county, s.locality,
        s.locality_id, s.latitude, s.longitude, s.observation_date, s.sampling_id, s.group_id
    FROM sightings_staging s
    JOIN (
        SELECT group_id, common_name, MIN(sampling_id) AS min_sid
        FROM sightings_staging
        WHERE group_id IS NOT NULL
        GROUP BY group_id, common_name
    ) g ON s.group_id = g.group_id AND s.common_name = g.common_name AND s.sampling_id = g.min_sid
    ;
    DROP TABLE sightings_staging;
    """)

    # Step 3: filter out one-off vagrants
    print("  Filtering vagrants...")
    con.execute("""--sql
    DROP TABLE IF EXISTS sightings_filtered;
    CREATE TABLE sightings_filtered AS
    WITH hotspot_vagrants AS (
        SELECT
            locality_id,
            common_name,
            COUNT(DISTINCT EXTRACT(YEAR FROM observation_date)) AS years_observed
        FROM sightings_deduped
        GROUP BY common_name, locality_id
    )
    SELECT
        s.common_name,
        s.locality,
        s.locality_id,
        s.latitude,
        s.longitude,
        s.observation_date,
        s.sampling_id,
        s.country,
        s.county,
        s.state,
        s.state_code
    FROM sightings_deduped s
    JOIN hotspot_vagrants h
        ON s.locality_id = h.locality_id
        AND s.common_name = h.common_name
    WHERE h.years_observed > 2
    ;
    DROP TABLE sightings_deduped;
    """)

    # Step 4: build hotspots table
    print("  Building hotspots lookup table...")
    con.execute("""--sql
    DROP TABLE IF EXISTS hotspots;
    CREATE TABLE hotspots AS
    SELECT
        locality_id,
        ANY_VALUE(locality) AS locality,
        AVG(latitude) AS latitude,
        AVG(longitude) AS longitude,
        ANY_VALUE(country) AS country,
        ANY_VALUE(county) AS county,
        ANY_VALUE(state) AS state,
        ANY_VALUE(state_code) AS state_code,
        COUNT(DISTINCT sampling_id) AS total_checklists
    FROM sightings_filtered
    GROUP BY locality_id;
    """)
    hotspot_count = con.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]
    print(f"  {hotspot_count} hotspots")

    # Step 5: calculate wilson scores
    print("  Calculating wilson scores...")
    con.execute("""--sql
    DROP TABLE IF EXISTS rolling_wilson_score;
    CREATE TABLE rolling_wilson_score AS
    WITH checklists AS (
        SELECT
            locality_id,
            DAYOFYEAR(observation_date) AS day_of_year,
            COUNT(DISTINCT sampling_id) AS total_checklists
        FROM sightings_filtered
        GROUP BY locality_id, day_of_year
    ),

    rolling_checklists AS (
        SELECT
            a.locality_id,
            a.day_of_year,
            SUM(b.total_checklists) AS n
        FROM checklists a
        JOIN checklists b
            ON a.locality_id = b.locality_id
            AND (
                (b.day_of_year BETWEEN a.day_of_year - 3 AND a.day_of_year + 3)
                OR (a.day_of_year <= 3 AND b.day_of_year >= 366 + a.day_of_year - 3)
                OR (a.day_of_year >= 363 AND b.day_of_year <= a.day_of_year + 3 - 366)
            )
        GROUP BY a.locality_id, a.day_of_year
    ),

    detections AS (
        SELECT
            locality_id,
            DAYOFYEAR(observation_date) AS day_of_year,
            common_name,
            COUNT(DISTINCT sampling_id) AS total_detections
        FROM sightings_filtered
        GROUP BY locality_id, day_of_year, common_name
    ),

    rolling_detections AS (
        SELECT
            c.locality_id,
            c.day_of_year,
            d.common_name,
            SUM(d.total_detections) AS k
        FROM checklists c
        JOIN detections d
            ON c.locality_id = d.locality_id
            AND (
                (d.day_of_year BETWEEN c.day_of_year - 3 AND c.day_of_year + 3)
                OR (c.day_of_year <= 3 AND d.day_of_year >= 366 + c.day_of_year - 3)
                OR (c.day_of_year >= 363 AND d.day_of_year <= c.day_of_year + 3 - 366)
            )
        GROUP BY c.locality_id, c.day_of_year, d.common_name
    )

    SELECT
        d.locality_id,
        d.day_of_year,
        d.common_name,
        ((d.k::DOUBLE / c.n)
            + (1.64 * 1.64) / (2 * c.n)
            - 1.64 * SQRT(GREATEST(
                ((d.k::DOUBLE / c.n) * (1 - (d.k::DOUBLE / c.n)) / c.n)
                + ((1.64 * 1.64) / (4 * c.n * c.n))
            , 0))
        )
        /
        (1 + (1.64 * 1.64) / c.n) AS wilson_lower_bound
    FROM rolling_detections d
    JOIN rolling_checklists c
        ON d.locality_id = c.locality_id
        AND d.day_of_year = c.day_of_year
    ;
    DROP TABLE sightings_filtered;
    """)


def _insert_into_combined(con, combined_db_path):
    """ATTACH combined DB and INSERT INTO hotspots + rolling_wilson_score."""
    print(f"  Inserting into {combined_db_path}...")
    con.execute(f"ATTACH '{combined_db_path}' AS combined")

    con.execute("""
        CREATE TABLE IF NOT EXISTS combined.hotspots (
            locality_id BIGINT,
            locality VARCHAR,
            latitude FLOAT,
            longitude FLOAT,
            country VARCHAR,
            county VARCHAR,
            state VARCHAR,
            state_code VARCHAR,
            total_checklists BIGINT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS combined.rolling_wilson_score (
            locality_id BIGINT,
            day_of_year INTEGER,
            common_name VARCHAR,
            wilson_lower_bound DOUBLE
        )
    """)

    con.execute("INSERT INTO combined.hotspots SELECT * FROM hotspots")
    hotspot_count = con.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]

    con.execute("INSERT INTO combined.rolling_wilson_score SELECT * FROM rolling_wilson_score")
    wilson_count = con.execute("SELECT COUNT(*) FROM rolling_wilson_score").fetchone()[0]

    con.execute("DETACH combined")

    return {"hotspots": hotspot_count, "wilson_rows": wilson_count}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python frequency_data_pipeline.py <region_code> <update_month-year> [input_dir]")
        print("Example: python frequency_data_pipeline.py US-AZ Dec-2025")
        print("Example: python frequency_data_pipeline.py US-AZ Dec-2025 'S:/eBird downloads'")
        sys.exit(1)

    region_code = sys.argv[1]
    update_month_year = sys.argv[2]
    input_dir = sys.argv[3] if len(sys.argv) > 3 else "data/raw"

    input_path = f"{input_dir}/ebd_{region_code}_smp_rel{update_month_year}.txt"
    combined_db_path = "data/combined.duckdb"

    print(f"Processing {region_code}...")
    stats = run_pipeline(region_code, input_path, combined_db_path)
    print(f"\nDone. {stats['hotspots']} hotspots, {stats['wilson_rows']:,} wilson rows.")
