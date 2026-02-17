import glob
import os
import duckdb

OUTPUT_DB = "data/combined.duckdb"
TABLES = ["hotspots", "rolling_avg_freq"]

state_dbs = sorted(
    f for f in glob.glob("data/*.duckdb")
    if os.path.basename(f) != "combined.duckdb"
)

if not state_dbs:
    print("No state databases found in data/")
    raise SystemExit(1)

print(f"Found {len(state_dbs)} state database(s): {', '.join(os.path.basename(f) for f in state_dbs)}")

if os.path.exists(OUTPUT_DB):
    os.remove(OUTPUT_DB)

con = duckdb.connect(OUTPUT_DB)
con.execute("PRAGMA enable_print_progress_bar;")
con.execute("PRAGMA progress_bar_time=500;")

for i, db_path in enumerate(state_dbs):
    alias = f"state_{i}"
    print(f"\nAttaching {os.path.basename(db_path)}...")
    con.execute(f"ATTACH '{db_path}' AS {alias} (READ_ONLY)")

    for table in TABLES:
        if i == 0:
            print(f"  Creating {table}...")
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM {alias}.{table}")
        else:
            print(f"  Appending {table}...")
            con.execute(f"INSERT INTO {table} SELECT * FROM {alias}.{table}")

    con.execute(f"DETACH {alias}")

print("\n--- Combined database summary ---")
for table in TABLES:
    cnt = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"  {table}: {cnt:,} rows")

con.close()
print(f"\nDone. Output: {OUTPUT_DB}")
