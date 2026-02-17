"""Fetch the eBird taxonomy and store it in the combined DuckDB database."""

import json
import tomllib
from urllib.request import Request, urlopen

import duckdb

DB_PATH = "data/combined.duckdb"

with open("secrets.toml", "rb") as f:
    secrets = tomllib.load(f)
EBIRD_API_KEY = secrets["ebird"]["api_key"]

url = "https://api.ebird.org/v2/ref/taxonomy/ebird?fmt=json&cat=species,issf,domestic,form"
print("Fetching eBird taxonomy...")
req = Request(url, headers={"X-eBirdApiToken": EBIRD_API_KEY})
with urlopen(req, timeout=60) as resp:
    data = json.loads(resp.read())

print(f"Received {len(data)} entries")

con = duckdb.connect(DB_PATH)

con.execute("DROP TABLE IF EXISTS species")
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

rows = [
    (
        sp["speciesCode"],
        sp["comName"],
        sp["sciName"],
        sp["category"],
        sp.get("taxonOrder", 0.0),
        sp.get("bandingCodes", []),
        sp.get("comNameCodes", []),
        sp.get("sciNameCodes", []),
    )
    for sp in data
]

con.executemany(
    "INSERT INTO species VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    rows,
)

count = con.execute("SELECT COUNT(*) FROM species").fetchone()[0]
print(f"Stored {count} species in {DB_PATH}")
con.close()
