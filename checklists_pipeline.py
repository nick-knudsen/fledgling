import duckdb as dk

con = dk.connect("data/combined.duckdb")


FILENAME = "Feb-2026"


con.execute(f"""--sql
DROP TABLE IF EXISTS sampling;
CREATE TABLE sampling AS
WITH sampling_staging AS (
    SELECT
        "GROUP IDENTIFIER",
        TRY_CAST(REGEXP_EXTRACT("LOCALITY ID", '(\\d+)$') AS BIGINT) AS locality_id,
        DAYOFYEAR("OBSERVATION DATE") AS day_of_year,
        "TIME OBSERVATIONS STARTED" as time_observations_started,
        "DURATION MINUTES" AS duration_minutes,
        "EFFORT DISTANCE KM" AS effort_distance_km,
        ROW_NUMBER() OVER (PARTITION BY "GROUP IDENTIFIER" ORDER BY "SAMPLING EVENT IDENTIFIER") AS rn
    FROM read_csv_auto("ebd_sampling_rel{FILENAME}.txt")
    WHERE "LOCALITY TYPE" = 'H' AND "PROTOCOL NAME" IN ('Stationary', 'Traveling')
)
SELECT
    locality_id,
    day_of_year,
    time_observations_started,
    duration_minutes,
    effort_distance_km
FROM sampling_staging
WHERE rn = 1
ORDER BY locality_id, day_of_year
;
""")

con.close()