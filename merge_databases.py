import glob
import os
import duckdb

OUTPUT_DB = "data/combined.duckdb"
TABLES = ["hotspots", "rolling_wilson_score"]

state_dbs = sorted(
    f for f in glob.glob("data/dbs/*.duckdb")
    if os.path.basename(f) != "combined.duckdb"
)

if not state_dbs:
    print("No state databases found in data/dbs/")
    raise SystemExit(1)

print(f"Found {len(state_dbs)} state database(s): {', '.join(os.path.basename(f) for f in state_dbs)}")

is_new = not os.path.exists(OUTPUT_DB)
con = duckdb.connect(OUTPUT_DB)
con.execute("PRAGMA enable_print_progress_bar;")
con.execute("PRAGMA progress_bar_time=500;")

# Create metadata table to track when each source DB was last merged
con.execute("""
    CREATE TABLE IF NOT EXISTS merge_metadata (
        db_name TEXT PRIMARY KEY,
        last_modified DOUBLE
    )
""")

# Determine which DBs need updating
to_update = []
for db_path in state_dbs:
    db_name = os.path.basename(db_path)
    current_mtime = os.path.getmtime(db_path)
    row = con.execute(
        "SELECT last_modified FROM merge_metadata WHERE db_name = ?", [db_name]
    ).fetchone()
    if row is None or row[0] < current_mtime:
        to_update.append((db_path, db_name, current_mtime))

if not to_update:
    print("\nAll databases are up to date. Nothing to do.")
    con.close()
    raise SystemExit(0)

print(f"\n{len(to_update)} database(s) to merge: {', '.join(name for _, name, _ in to_update)}")

for i, (db_path, db_name, mtime) in enumerate(to_update):
    alias = f"state_{i}"
    print(f"\nMerging {db_name}...")
    con.execute(f"ATTACH '{db_path}' AS {alias} (READ_ONLY)")

    # Get locality_ids from this state DB to identify its rows
    locality_ids = [
        row[0] for row in
        con.execute(f"SELECT locality_id FROM {alias}.hotspots").fetchall()
    ]

    if not is_new:
        # Check if data tables exist (they won't on first run)
        existing_tables = {
            row[0] for row in
            con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()
        }

        if locality_ids and "hotspots" in existing_tables:
            # Delete old rows for these localities
            id_list = ",".join(str(lid) for lid in locality_ids)
            for table in TABLES:
                if table in existing_tables:
                    deleted = con.execute(
                        f"DELETE FROM {table} WHERE locality_id IN ({id_list})"
                    ).fetchone()[0]
                    if deleted > 0:
                        print(f"  Removed {deleted:,} old rows from {table}")

    for table in TABLES:
        existing_tables = {
            row[0] for row in
            con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()
        }
        if table not in existing_tables:
            print(f"  Creating {table}...")
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM {alias}.{table}")
        else:
            print(f"  Inserting into {table}...")
            con.execute(f"INSERT INTO {table} SELECT * FROM {alias}.{table}")

    # Update metadata
    con.execute("""
        INSERT OR REPLACE INTO merge_metadata (db_name, last_modified) VALUES (?, ?)
    """, [db_name, mtime])

    con.execute(f"DETACH {alias}")
    is_new = False

print("\nSorting rolling_wilson_score by (day_of_year, locality_id) for query performance...")
con.execute("""
    CREATE TABLE rolling_wilson_score_sorted AS
        SELECT * FROM rolling_wilson_score
        ORDER BY day_of_year, locality_id;
    DROP TABLE rolling_wilson_score;
    ALTER TABLE rolling_wilson_score_sorted RENAME TO rolling_wilson_score;
""")

print("\n--- Combined database summary ---")
for table in TABLES:
    cnt = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"  {table}: {cnt:,} rows")

con.close()
print(f"\nDone. Output: {OUTPUT_DB}")