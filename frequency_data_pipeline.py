import duckdb as dk

con = dk.connect('data/arizona.duckdb')
con.execute("PRAGMA enable_print_progress_bar;")
con.execute("PRAGMA progress_bar_time=500;")  # show after 500ms instead of 2s


# read raw data into duckdb
print("Reading eBird data into duckdb...")
con.execute("""--sql
DROP TABLE IF EXISTS sightings_raw;

CREATE TABLE sightings_raw AS
SELECT * 
FROM read_csv_auto('data/ebd_US-AZ_smp_relDec-2025.txt',
    delim='\t', sample_size=-1)
;
""")
print("Read {} rows".format(con.execute("SELECT COUNT(*) FROM sightings_raw").fetchone()[0]))
print("\nStaging data for frequency calculations...")
# rename columns for easier querying
# keep only complete checklists from hotspot locations
con.execute("""--sql
DROP TABLE IF EXISTS sightings_staging;
CREATE TABLE sightings_staging AS
SELECT
    TRY_CAST(REGEXP_EXTRACT("GLOBAL UNIQUE IDENTIFIER", '(\\d+)$') AS BIGINT) AS global_id,
    TRY_CAST("LAST EDITED DATE" AS DATE) AS last_edited_date,
    "TAXONOMIC ORDER" as taxonomic_order,
    "CATEGORY" as species_category,
    "COMMON NAME" as common_name,
    "SCIENTIFIC NAME" as scientific_name,
    TRY_CAST("OBSERVATION COUNT" AS INT) AS observation_count,
    "COUNTRY" as country,
    "COUNTRY CODE" as country_code,
    "STATE" as state,
    "STATE CODE" as state_code,
    "COUNTY" as county,
    "COUNTY CODE" as county_code,
    "LOCALITY" as locality,
    TRY_CAST(REGEXP_EXTRACT("LOCALITY ID", '(\\d+)$') AS BIGINT) AS locality_id,
    "LOCALITY TYPE" as locality_type,
    "LATITUDE"::FLOAT AS latitude,
    "LONGITUDE"::FLOAT AS longitude,
    TRY_CAST("OBSERVATION DATE" AS DATE) AS observation_date,
    TRY_CAST("TIME OBSERVATIONS STARTED" AS TIME) AS time_observations_started,
    TRY_CAST(REGEXP_EXTRACT("OBSERVER ID", '(\\d+)$') AS BIGINT) AS observer_id,
    TRY_CAST(REGEXP_EXTRACT("SAMPLING EVENT IDENTIFIER", '(\\d+)$') AS BIGINT) AS sampling_id,
    "OBSERVATION TYPE" as observation_type,
    "DURATION MINUTES"::INT AS duration_minutes,
    "EFFORT DISTANCE KM"::FLOAT AS effort_distance_km,
    "NUMBER OBSERVERS"::INT AS number_observers,
    "ALL SPECIES REPORTED"::BOOLEAN as all_species_reported,
    TRY_CAST(REGEXP_EXTRACT("GROUP IDENTIFIER", '(\\d+)$') AS BIGINT) AS group_id  
FROM sightings_raw
WHERE locality_type == 'H' AND
    (species_category == 'species' OR species_category == 'issf' OR species_category == 'form' OR species_category == 'domestic') AND
    all_species_reported IS TRUE
;
""")

print("\nDeduplicating observations...")
# drop duplicate observations from group checklists
con.execute("""--sql
DROP TABLE IF EXISTS sightings_clean;
CREATE TABLE sightings_clean AS
SELECT *
FROM (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY group_id, common_name
            ORDER BY sampling_id --figure out something else to order by?
        ) as row_num
    FROM sightings_staging
) t
WHERE group_id IS NULL or row_num = 1
;
""")

print("\nFiltering out one-off vagrants...")
# count number of year each species was observed in each hotspot to identify one-off vagrants
con.execute("""--sql
DROP TABLE IF EXISTS hotspot_vagrants;
CREATE TABLE hotspot_vagrants AS
SELECT
    locality_id,
    common_name,
    COUNT(DISTINCT EXTRACT(YEAR FROM observation_date)) AS years_observed
FROM sightings_clean
GROUP BY common_name, locality_id
;
""")

# filter out one-off vagrants from sightings
con.execute("""--sql
DROP TABLE IF EXISTS sightings_filtered;
CREATE TABLE sightings_filtered AS
SELECT
    s.*
FROM sightings_clean s
JOIN hotspot_vagrants h
    ON s.locality_id = h.locality_id
    AND s.common_name = h.common_name
    WHERE h.years_observed > 2 -- only keep species observed at a hotspot in more than 2 years - eliminates one-off vagrants
;
""")

print("\nCalculating checklist and detection counts...")
# group checklists and sighting counts by locality, date, and species
con.execute("""--sql
DROP TABLE IF EXISTS detection_frequencies;
CREATE TABLE detection_frequencies AS
WITH checklists AS (
    SELECT
        locality,
        locality_id,
        DAYOFYEAR(observation_date) AS day_of_year,
        COUNT(DISTINCT sampling_id) AS total_checklists
    FROM sightings_filtered
    GROUP BY locality, locality_id, day_of_year
), detections AS (
    SELECT
        locality_id,
        DAYOFYEAR(observation_date) AS day_of_year,
        common_name,
        COUNT(DISTINCT sampling_id) AS total_detections
    FROM sightings_filtered
    GROUP BY locality_id, day_of_year, common_name
), species AS (
    SELECT DISTINCT
        locality_id,
        common_name
    FROM sightings_filtered
)

SELECT
    c.locality,
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
ORDER BY c.locality_id, c.day_of_year DESC
;
""")

print("\nCalculating frequencies...")
# calculate rolling average observations, checklists, frequency, and wilson lower bound CI 
con.execute("""--sql
DROP TABLE IF EXISTS rolling_avg_freq;
CREATE TABLE rolling_avg_freq AS
WITH wrapped AS (
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
        locality,
        locality_id,
        day_of_year,
        common_name,
        total_detections,
        SUM(total_detections) OVER w AS k,
        total_checklists,
        SUM(total_checklists) OVER w AS n
    FROM wrapped
    WHERE 4 <= wrapped_day_of_year AND wrapped_day_of_year <= 369
    WINDOW w AS (
        PARTITION BY locality_id, common_name
        ORDER BY wrapped_day_of_year
        RANGE BETWEEN 3 PRECEDING AND 3 FOLLOWING
    )
    ORDER BY day_of_year
)

SELECT
    locality,
    locality_id,
    day_of_year,
    common_name,
    k::INT AS rolling_detections,
    n::INT AS rolling_checklists,
    k::DOUBLE / n AS rolling_freq,
    ((k::DOUBLE / n)
        + (1.64 * 1.64) / (2 * n)
        - 1.64 * SQRT(
            ((k::DOUBLE / n) * (1 - (k::DOUBLE / n)) / n)
            + ((1.64 * 1.64) / (4 * n * n))
        )
    )
    /
    (1 + (1.64 * 1.64) / n) AS wilson_lower_bound
    FROM rolling
;
""")
