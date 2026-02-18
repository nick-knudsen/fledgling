import sys
import duckdb as dk

if len(sys.argv) != 3:
    print("Usage: python frequency_data_pipeline.py <region_code> <update_month-year>")
    print("Example: python frequency_data_pipeline.py US-AZ Dec-2025")
    sys.exit(1)

region_code = sys.argv[1]
update_month_year = sys.argv[2]

input_tsv = f"ebd_{region_code}_smp_rel{update_month_year}.txt"
output_db = f"{region_code}.duckdb"

con = dk.connect("data/dbs/" + output_db)
con.execute("PRAGMA enable_print_progress_bar;")
con.execute("PRAGMA progress_bar_time=500;")  # show after 500ms instead of 2s

# read raw data, stage, deduplicate, and filter vagrants
print(f"Reading {input_tsv} into {output_db}...")
con.execute(f"""--sql
DROP TABLE IF EXISTS sightings_filtered;
CREATE TABLE sightings_filtered AS
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
    FROM read_csv_auto('data/raw/{input_tsv}',
        delim='\t',
        types={{
            'LATITUDE': 'FLOAT',
            'LONGITUDE': 'FLOAT',
            'OBSERVATION DATE': 'DATE',
            'ALL SPECIES REPORTED': 'BOOLEAN'
        }})
),

sightings_staging AS (
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
        "OBSERVATION COUNT" != '0'
),

-- deduplicate group checklists
sightings_clean AS (
    SELECT *
    FROM (
        SELECT *,
            ROW_NUMBER() OVER (
                PARTITION BY group_id, common_name
                ORDER BY sampling_id
            ) as row_num
        FROM sightings_staging
    ) t
    WHERE group_id IS NULL or row_num = 1
),

-- count years each species observed at each hotspot to identify one-off vagrants
hotspot_vagrants AS (
    SELECT
        locality_id,
        common_name,
        COUNT(DISTINCT EXTRACT(YEAR FROM observation_date)) AS years_observed
    FROM sightings_clean
    GROUP BY common_name, locality_id
)

-- filter out one-off vagrants, keep only fields used downstream
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
FROM sightings_clean s
JOIN hotspot_vagrants h
    ON s.locality_id = h.locality_id
    AND s.common_name = h.common_name
WHERE h.years_observed > 2
;
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
WITH checklists AS (
    SELECT
        locality_id,
        DAYOFYEAR(observation_date) AS day_of_year,
        COUNT(DISTINCT sampling_id) AS total_checklists
    FROM sightings_filtered
    GROUP BY locality_id, day_of_year
),

detections AS (
    SELECT
        locality_id,
        DAYOFYEAR(observation_date) AS day_of_year,
        common_name,
        COUNT(sampling_id) AS total_detections -- DISTINCT not needed since we are grouping by common_name
    FROM sightings_filtered
    GROUP BY locality_id, day_of_year, common_name
),

species AS (
    SELECT DISTINCT
        locality_id,
        common_name
    FROM sightings_filtered
),

detection_frequencies AS (
    SELECT
        c.locality_id,
        c.day_of_year,
        s.common_name,
        COALESCE(d.total_detections, 0) AS total_detections,
        c.total_checklists
    FROM checklists c
    JOIN species s
        ON s.locality_id = c.locality_id
    LEFT JOIN detections d
        ON d.locality_id = c.locality_id AND d.day_of_year = c.day_of_year AND d.common_name = s.common_name
),

wrapped AS (
    SELECT
        *,
        day_of_year AS wrapped_day_of_year
    FROM detection_frequencies

    UNION ALL

    SELECT
        *,
        day_of_year + 366 AS wrapped_day_of_year
    FROM detection_frequencies
    WHERE day_of_year <= 6
),

rolling AS (
    SELECT
        locality_id,
        day_of_year,
        common_name,
        SUM(total_detections) OVER w AS k,
        SUM(total_checklists) OVER w AS n
    FROM wrapped
    WHERE 4 <= wrapped_day_of_year AND wrapped_day_of_year <= 369
    WINDOW w AS (
        PARTITION BY locality_id, common_name
        ORDER BY wrapped_day_of_year
        RANGE BETWEEN 3 PRECEDING AND 3 FOLLOWING
    )
)

SELECT
    locality_id,
    day_of_year,
    common_name,
    ((k::DOUBLE / n)
        + (1.64 * 1.64) / (2 * n)
        - 1.64 * SQRT(GREATEST(
            ((k::DOUBLE / n) * (1 - (k::DOUBLE / n)) / n)
            + ((1.64 * 1.64) / (4 * n * n))
        , 0))
    )
    /
    (1 + (1.64 * 1.64) / n) AS wilson_lower_bound
    FROM rolling
;
DROP TABLE sightings_filtered;
""")

print("\nDone.")
