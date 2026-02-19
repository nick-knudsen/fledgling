import sys
import duckdb as dk

if len(sys.argv) < 3:
    print("Usage: python frequency_data_pipeline.py <region_code> <update_month-year> [input_dir]")
    print("Example: python frequency_data_pipeline.py US-AZ Dec-2025")
    print("Example: python frequency_data_pipeline.py US-AZ Dec-2025 'S:/eBird downloads'")
    sys.exit(1)

region_code = sys.argv[1]
update_month_year = sys.argv[2]
input_dir = sys.argv[3] if len(sys.argv) > 3 else "data/raw"

input_tsv = f"ebd_{region_code}_smp_rel{update_month_year}.txt"
input_path = f"{input_dir}/{input_tsv}"
output_db = f"{region_code}.duckdb"

con = dk.connect("data/dbs/" + output_db)
con.execute("PRAGMA enable_print_progress_bar;")
con.execute("PRAGMA progress_bar_time=500;")  # show after 500ms instead of 2s
con.execute("SET temp_directory = 'data/dbs/tmp';")  # spill to disk instead of OOM
con.execute("SET memory_limit = '12GB';")
con.execute("SET preserve_insertion_order = false;")

# Step 1: read CSV, filter, and rename columns (materialized to let DuckDB free memory)
print(f"Reading {input_path} into {output_db}...")
con.execute(f"""--sql
DROP TABLE IF EXISTS sightings_staging;
CREATE TABLE sightings_staging AS
-- Commenting out currently unneeded columns to speed up processing - can always add them back later
-- Make sure to comment/uncomment the corrsponding columns in the SELECT statement below as well
WITH sightings_raw AS (
    SELECT
        --"GLOBAL UNIQUE IDENTIFIER",
        --"LAST EDITED DATE",
        --"TAXONOMIC ORDER",
        "CATEGORY",
        "COMMON NAME",
        --"SCIENTIFIC NAME",
        "OBSERVATION COUNT",
        "COUNTRY",
        --"COUNTRY CODE",
        "STATE",
        "STATE CODE",
        "COUNTY",
        --"COUNTY CODE",
        "LOCALITY",
        "LOCALITY ID",
        "LOCALITY TYPE",
        "LATITUDE",
        "LONGITUDE",
        "OBSERVATION DATE",
        --"TIME OBSERVATIONS STARTED",
        --"OBSERVER ID",
        "SAMPLING EVENT IDENTIFIER",
        --"OBSERVATION TYPE",
        --"DURATION MINUTES",
        --"EFFORT DISTANCE KM",
        --"NUMBER OBSERVERS",
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
    --TRY_CAST(REGEXP_EXTRACT("GLOBAL UNIQUE IDENTIFIER", '(\\d+)$') AS BIGINT) AS global_id,
    --TRY_CAST("LAST EDITED DATE" AS DATE) AS last_edited_date,
    --"TAXONOMIC ORDER" as taxonomic_order,
    --"CATEGORY" as species_category,
    "COMMON NAME" as common_name,
    --"SCIENTIFIC NAME" as scientific_name,
    --TRY_CAST("OBSERVATION COUNT" AS INT) AS observation_count,
    "COUNTRY" as country,
    --"COUNTRY CODE" as country_code,
    "STATE" as state,
    "STATE CODE" as state_code,
    "COUNTY" as county,
    --"COUNTY CODE" as county_code,
    "LOCALITY" as locality,
    TRY_CAST(REGEXP_EXTRACT("LOCALITY ID", '(\\d+)$') AS BIGINT) AS locality_id,
    --"LOCALITY TYPE" as locality_type,
    "LATITUDE" as latitude,
    "LONGITUDE" as longitude,
    "OBSERVATION DATE" as observation_date,
    --TRY_CAST("TIME OBSERVATIONS STARTED" AS TIME) AS time_observations_started,
    --TRY_CAST(REGEXP_EXTRACT("OBSERVER ID", '(\\d+)$') AS BIGINT) AS observer_id,
    TRY_CAST(REGEXP_EXTRACT("SAMPLING EVENT IDENTIFIER", '(\\d+)$') AS BIGINT) AS sampling_id,
    --"OBSERVATION TYPE" as observation_type,
    --"DURATION MINUTES"::INT AS duration_minutes,
    --"EFFORT DISTANCE KM"::FLOAT AS effort_distance_km,
    --"NUMBER OBSERVERS"::INT AS number_observers,
    --"ALL SPECIES REPORTED" as all_species_reported,
    TRY_CAST(REGEXP_EXTRACT("GROUP IDENTIFIER", '(\\d+)$') AS BIGINT) AS group_id
FROM sightings_raw
WHERE "LOCALITY TYPE" = 'H' AND
    ("CATEGORY" IN ('species', 'issf', 'form', 'domestic')) AND
    "ALL SPECIES REPORTED" IS TRUE AND
    "OBSERVATION COUNT" != '0' AND
    "COMMON NAME" NOT LIKE '% sp.%' -- this is needed to filter out Domestic Goose sp. and Domestic Lovebird sp. which for some reason are in the species category?
;
""")

# Step 2: deduplicate group checklists
# Uses GROUP BY + JOIN instead of ROW_NUMBER window function because
# window functions don't support spilling to disk in DuckDB
print("Deduplicating group checklists...")
con.execute("""--sql
DROP TABLE IF EXISTS sightings_deduped;
CREATE TABLE sightings_deduped AS
-- Non-group checklists: keep all rows
SELECT common_name, country, state, state_code, county, locality,
    locality_id, latitude, longitude, observation_date, sampling_id, group_id
FROM sightings_staging WHERE group_id IS NULL

UNION ALL

-- Group checklists: keep one row per (group_id, common_name) via MIN(sampling_id)
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
print("Filtering vagrants...")
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

print("\nBuilding hotspots lookup table...")
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
print("{} hotspots".format(con.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]))

print("\nCalculating wilson scores...")
con.execute("""--sql
DROP TABLE IF EXISTS rolling_wilson_score;
CREATE TABLE rolling_wilson_score AS
-- Rolling checklists: species-independent, computed once per (locality, day)
WITH checklists AS (
    SELECT
        locality_id,
        DAYOFYEAR(observation_date) AS day_of_year,
        COUNT(DISTINCT sampling_id) AS total_checklists
    FROM sightings_filtered
    GROUP BY locality_id, day_of_year
),

-- Self-join to compute 7-day rolling checklist totals with year-boundary wrapping
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

-- Detections: sparse, only rows where species was actually detected
detections AS (
    SELECT
        locality_id,
        DAYOFYEAR(observation_date) AS day_of_year,
        common_name,
        COUNT(DISTINCT sampling_id) AS total_detections
    FROM sightings_filtered
    GROUP BY locality_id, day_of_year, common_name
),

-- Join checklist days with nearby detections to get rolling detection totals
-- Only produces rows where k > 0 (species detected within ±3 day window)
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

-- Join rolling detections with rolling checklists to compute Wilson lower bound
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

print("\nDone.")
