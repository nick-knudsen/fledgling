"""Automated eBird world data pipeline.

Downloads the eBird world dataset, extracts and processes each country's data,
and builds a new combined.duckdb. The live database is untouched until a final
atomic swap at the end.

Usage:
    python ebird_pipeline.py                    # full run
    python ebird_pipeline.py --skip-download    # skip download, extract tar + process
    python ebird_pipeline.py --skip-extract     # skip download + extract, process from data/raw/
    python ebird_pipeline.py --swap-only        # just swap combined_new -> combined
"""

import argparse
import logging
import os
import re
import shutil
import sys
import tarfile
import time
from datetime import datetime
from getpass import getpass
from pathlib import Path

import duckdb
import httpx
from playwright.sync_api import sync_playwright

import frequency_data_pipeline as fdp

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RELEASE = "relJan-2026"
WORLD_DOWNLOAD_URL = f"https://download.ebird.org/ebd/prepackaged/ebd_{RELEASE}.tar"

DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw"
SAMPLING_DIR = DATA_DIR / "sampling"
TEMP_DIR = DATA_DIR / "tmp"
COMBINED_NEW = DATA_DIR / "combined_new.duckdb"
COMBINED_LIVE = DATA_DIR / "combined.duckdb"
COMBINED_OLD = DATA_DIR / "combined_old.duckdb"
FAILED_LOG = DATA_DIR / "failed_regions.txt"
PIPELINE_LOG = DATA_DIR / "pipeline.log"

MEMORY_LIMIT = "2GB"

log = logging.getLogger("ebird_pipeline")

# ---------------------------------------------------------------------------
# Login + download
# ---------------------------------------------------------------------------

def get_session_cookies(username, password):
    """Log into eBird via Playwright and return session cookies."""
    log.info("Launching browser for eBird login...")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        page.goto("https://ebird.org/home?forceLogin=true")
        page.wait_for_selector("#input-user-name", timeout=15_000)
        page.fill("#input-user-name", username)
        page.fill("#input-password", password)
        page.click("#form-submit")
        # Wait for navigation after login — may redirect through several URLs
        page.wait_for_load_state("networkidle", timeout=30_000)

        # Check if login succeeded by looking for the session cookie
        cookies = {c["name"]: c["value"] for c in context.cookies()}
        if "EBIRD_SESSIONID" not in cookies:
            # Login may have failed — check if we're still on the login page
            if "cassso" in page.url or "forceLogin" in page.url:
                raise RuntimeError(
                    "Login failed. Check your credentials. "
                    f"Current URL: {page.url}"
                )
        log.info(f"Login successful. Current URL: {page.url}")
        browser.close()

    return cookies


def download_world_dataset(cookies, dest_path):
    """Stream-download the world dataset tar using session cookies."""
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    headers = {"Cookie": cookie_header}
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    log.info(f"Downloading {WORLD_DOWNLOAD_URL}...")
    log.info(f"  Destination: {dest_path}")
    start = time.time()
    size = 0

    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(600.0)) as client:
        with client.stream("GET", WORLD_DOWNLOAD_URL, headers=headers) as resp:
            if resp.status_code == 302 or resp.status_code == 401:
                raise RuntimeError(
                    f"Authentication failed (HTTP {resp.status_code}). "
                    "Session cookies may be invalid."
                )
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)
                    size += len(chunk)
                    # Log progress every 1 GB
                    if size % (1024 ** 3) < 1024 * 1024:
                        log.info(f"  {size / (1024 ** 3):.1f} GB downloaded...")

    elapsed = time.time() - start
    log.info(f"Download complete: {size / (1024 ** 3):.1f} GB in {elapsed / 60:.0f} minutes")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_tar(archive_path, extract_dir):
    """Extract tar archive, flattening directory structure. Deletes tar when done."""
    archive_path = Path(archive_path)
    extract_dir = Path(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"Extracting {archive_path}...")
    with tarfile.open(archive_path) as tf:
        members = tf.getmembers()
        log.info(f"  Archive contains {len(members)} entries")
        for member in members:
            if member.isfile():
                member.name = os.path.basename(member.name)
                tf.extract(member, extract_dir)
                log.info(f"  Extracted: {member.name}")

    archive_path.unlink()
    log.info("Deleted tar to reclaim disk space")


def decompress_gz_files(directory):
    """Decompress any .gz files in directory. Deletes .gz files when done."""
    import gzip
    directory = Path(directory)
    gz_files = sorted(directory.glob("*.gz"))
    if not gz_files:
        return

    log.info(f"Decompressing {len(gz_files)} .gz files...")
    for gz_file in gz_files:
        out_path = directory / gz_file.stem  # strips .gz
        # Skip if already decompressed
        if out_path.exists():
            log.info(f"  {out_path.name} already exists, skipping")
            continue
        log.info(f"  Decompressing {gz_file.name} (this may take a while)...")
        with gzip.open(gz_file, "rb") as src, open(out_path, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        gz_file.unlink()
        size_gb = out_path.stat().st_size / (1024 ** 3)
        log.info(f"  Decompressed: {out_path.name} ({size_gb:.1f} GB)")

    extracted = sorted(directory.iterdir())
    log.info(f"{len(extracted)} files in {directory}:")
    for f in extracted:
        size_mb = f.stat().st_size / (1024 ** 2)
        log.info(f"  {f.name} ({size_mb:,.0f} MB)")
        

def split_world_file(world_file):
    """Split a world TSV into per-country files using DuckDB COPY PARTITION_BY."""
    world_file = Path(world_file)
    split_dir = RAW_DIR / "split"
    split_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    con.execute(f"SET temp_directory = '{TEMP_DIR}';")
    con.execute(f"SET memory_limit = '{MEMORY_LIMIT}';")
    con.execute("SET preserve_insertion_order = false;")

    con.execute(f"""
        COPY (
            SELECT *, "COUNTRY CODE" AS _country
            FROM read_csv_auto('{world_file}',
                delim='\t',
                quote='',
                types={{
                    'LATITUDE': 'FLOAT',
                    'LONGITUDE': 'FLOAT',
                    'OBSERVATION DATE': 'DATE',
                    'ALL SPECIES REPORTED': 'BOOLEAN'
                }})
        )
        TO '{split_dir}'
        (FORMAT CSV, DELIMITER '\t', HEADER, PARTITION_BY (_country))
    """)
    con.close()

    # Delete the world file to reclaim disk space
    world_file.unlink()
    log.info("Deleted world file to reclaim disk space")

    # Collect per-country files from partition directories
    regions = []
    for country_dir in sorted(split_dir.iterdir()):
        if country_dir.is_dir():
            # DuckDB creates dirs like _country=US
            code = country_dir.name.split("=")[-1]
            csv_files = list(country_dir.glob("*.csv"))
            if csv_files:
                regions.append((code, csv_files[0]))

    log.info(f"Split into {len(regions)} countries")
    return regions


# ---------------------------------------------------------------------------
# Combined DB management
# ---------------------------------------------------------------------------

def init_combined_db(db_path):
    """Create tables in the new combined database if they don't exist."""
    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE IF NOT EXISTS hotspots (
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
        CREATE TABLE IF NOT EXISTS rolling_wilson_score (
            locality_id BIGINT,
            day_of_year INTEGER,
            common_name VARCHAR,
            wilson_lower_bound DOUBLE
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS processed_regions (
            region_code TEXT PRIMARY KEY,
            processed_at TIMESTAMP,
            hotspot_count BIGINT,
            wilson_row_count BIGINT
        )
    """)
    con.close()


def get_processed_regions(db_path):
    """Return set of region codes already processed in the new combined DB."""
    if not os.path.exists(db_path):
        return set()
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        tables = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()}
        if "processed_regions" not in tables:
            return set()
        rows = con.execute("SELECT region_code FROM processed_regions").fetchall()
        return {r[0] for r in rows}
    finally:
        con.close()


def record_success(db_path, region_code, stats):
    """Record a successfully processed region in the metadata table."""
    con = duckdb.connect(str(db_path))
    con.execute("""
        INSERT OR REPLACE INTO processed_regions
        (region_code, processed_at, hotspot_count, wilson_row_count)
        VALUES (?, current_timestamp, ?, ?)
    """, [region_code, stats.get("hotspots", 0), stats.get("wilson_rows", 0)])
    con.close()


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def process_region(region_code, data_path):
    """Run the frequency pipeline for one region. Returns stats or None."""
    try:
        stats = fdp.run_pipeline(
            region_code=region_code,
            input_path=str(data_path),
            combined_db_path=str(COMBINED_NEW),
            memory_limit=MEMORY_LIMIT,
            temp_dir=str(TEMP_DIR),
        )
        return stats
    except Exception as e:
        log.error(f"Pipeline failed for {region_code}: {e}")
        return None


def log_failure(region_code, reason):
    """Append a failed region to the failure log."""
    with open(FAILED_LOG, "a") as f:
        f.write(f"{region_code}\t{datetime.now().isoformat()}\t{reason}\n")


def retry_failed_as_subnational(failed_regions, all_regions_data):
    """For failed countries, attempt to process at subnational level by
    splitting the country's data by STATE CODE into smaller pieces."""
    if not failed_regions:
        return

    log.info(f"\nRetrying {len(failed_regions)} failed regions at subnational level...")

    for region_code in list(failed_regions):
        data_path = None
        for code, path in all_regions_data:
            if code == region_code:
                data_path = path
                break

        if data_path is None or not os.path.exists(data_path):
            log.warning(f"  {region_code}: data file no longer exists, skipping")
            continue

        log.info(f"  Splitting {region_code} by STATE CODE...")
        try:
            sub_regions = _split_by_state_code(data_path, region_code)
        except Exception as e:
            log.error(f"  Failed to split {region_code}: {e}")
            continue

        all_ok = True
        for sub_code, sub_path in sub_regions:
            log.info(f"  Processing subnational: {sub_code}")
            stats = process_region(sub_code, sub_path)
            if stats:
                record_success(COMBINED_NEW, sub_code, stats)
                log.info(f"    {sub_code}: {stats['hotspots']} hotspots, {stats['wilson_rows']:,} wilson rows")
            else:
                all_ok = False
                log_failure(sub_code, "subnational_pipeline_failed")
            Path(sub_path).unlink(missing_ok=True)

        if all_ok:
            failed_regions.discard(region_code)


def _split_by_state_code(data_path, country_code):
    """Split a country's data file by STATE CODE into per-subnational files."""
    split_dir = TEMP_DIR / f"{country_code}_sub"
    split_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    con.execute(f"SET temp_directory = '{TEMP_DIR}';")
    con.execute(f"SET memory_limit = '{MEMORY_LIMIT}';")

    con.execute(f"""
        COPY (
            SELECT *, "STATE CODE" AS _state
            FROM read_csv_auto('{data_path}',
                delim='\t',
                quote='',
                types={{
                    'LATITUDE': 'FLOAT',
                    'LONGITUDE': 'FLOAT',
                    'OBSERVATION DATE': 'DATE',
                    'ALL SPECIES REPORTED': 'BOOLEAN'
                }})
        )
        TO '{split_dir}'
        (FORMAT CSV, DELIMITER '\t', HEADER, PARTITION_BY (_state))
    """)
    con.close()

    regions = []
    for state_dir in sorted(split_dir.iterdir()):
        if state_dir.is_dir():
            code = state_dir.name.split("=")[-1]
            files = list(state_dir.glob("*.csv"))
            if files:
                regions.append((code, files[0]))

    return regions


# ---------------------------------------------------------------------------
# Final sort + swap
# ---------------------------------------------------------------------------

def final_sort():
    """Sort rolling_wilson_score by (day_of_year, locality_id) for query performance."""
    log.info("Sorting rolling_wilson_score (this may take a while)...")
    con = duckdb.connect(str(COMBINED_NEW))
    con.execute(f"SET temp_directory = '{TEMP_DIR}';")
    con.execute(f"SET memory_limit = '{MEMORY_LIMIT}';")
    con.execute("PRAGMA enable_print_progress_bar;")
    con.execute("PRAGMA progress_bar_time=500;")
    con.execute("""
        CREATE TABLE rolling_wilson_score_sorted AS
            SELECT * FROM rolling_wilson_score
            ORDER BY day_of_year, locality_id;
        DROP TABLE rolling_wilson_score;
        ALTER TABLE rolling_wilson_score_sorted RENAME TO rolling_wilson_score;
    """)

    hotspots = con.execute("SELECT COUNT(*) FROM hotspots").fetchone()[0]
    wilson = con.execute("SELECT COUNT(*) FROM rolling_wilson_score").fetchone()[0]
    regions = con.execute("SELECT COUNT(*) FROM processed_regions").fetchone()[0]
    log.info(f"Final database: {hotspots:,} hotspots, {wilson:,} wilson rows, {regions} regions")
    con.close()


def copy_species_table():
    """Copy the species table from the live DB to the new DB if it exists."""
    if not COMBINED_LIVE.exists():
        return
    live = duckdb.connect(str(COMBINED_LIVE), read_only=True)
    tables = {r[0] for r in live.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()}
    if "species" not in tables:
        live.close()
        return

    log.info("Copying species table from live DB...")
    live.close()

    con = duckdb.connect(str(COMBINED_NEW))
    con.execute(f"ATTACH '{COMBINED_LIVE}' AS live (READ_ONLY)")
    con.execute("DROP TABLE IF EXISTS species")
    con.execute("CREATE TABLE species AS SELECT * FROM live.species")
    con.execute("DETACH live")
    cnt = con.execute("SELECT COUNT(*) FROM species").fetchone()[0]
    con.close()
    log.info(f"  Copied {cnt:,} species")


def swap_databases():
    """Swap combined_new.duckdb -> combined.duckdb, keeping the old as backup."""
    if not COMBINED_NEW.exists():
        log.error(f"{COMBINED_NEW} does not exist, nothing to swap.")
        sys.exit(1)

    log.info("Swapping databases...")
    if COMBINED_LIVE.exists():
        if COMBINED_OLD.exists():
            COMBINED_OLD.unlink()
        COMBINED_LIVE.rename(COMBINED_OLD)
        log.info(f"  {COMBINED_LIVE.name} -> {COMBINED_OLD.name}")

    COMBINED_NEW.rename(COMBINED_LIVE)
    log.info(f"  {COMBINED_NEW.name} -> {COMBINED_LIVE.name}")
    log.info("Swap complete. Restart the Docker container to pick up the new DB.")
    log.info(f"Once verified, delete {COMBINED_OLD} to reclaim disk space.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global MEMORY_LIMIT

    parser = argparse.ArgumentParser(description="Automated eBird world data pipeline")
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip download, but still extract and process")
    parser.add_argument("--skip-extract", action="store_true",
                        help="Skip download and extract, resume processing from data/raw/")
    parser.add_argument("--swap-only", action="store_true",
                        help="Only swap combined_new.duckdb -> combined.duckdb")
    parser.add_argument("--sort-only", action="store_true",
                        help="Only run the final sort on combined_new.duckdb")
    parser.add_argument("--memory", default=MEMORY_LIMIT,
                        help=f"DuckDB memory limit (default: {MEMORY_LIMIT})")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(str(PIPELINE_LOG)),
            logging.StreamHandler(),
        ],
    )

    MEMORY_LIMIT = args.memory

    if args.swap_only:
        swap_databases()
        return tmux 

    if args.sort_only:
        final_sort()
        copy_species_table()
        return

    # Setup directories
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    SAMPLING_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    if not args.skip_download and not args.skip_extract:
        print("eBird credentials (not stored):")
        username = input("  Username: ").strip()
        password = getpass("  Password: ")

        cookies = get_session_cookies(username, password)

        archive_path = RAW_DIR / f"ebd_{RELEASE}.tar"
        download_world_dataset(cookies, archive_path)
        extract_tar(archive_path, RAW_DIR)
        decompress_gz_files(RAW_DIR)
    elif args.skip_download and not args.skip_extract:
        archive_path = RAW_DIR / f"ebd_{RELEASE}.tar"
        if archive_path.exists():
            extract_tar(archive_path, RAW_DIR)
        decompress_gz_files(RAW_DIR)

    regions = split_world_file(RAW_DIR / f"ebd_{RELEASE}.txt")

    # Initialize the new combined DB
    init_combined_db(COMBINED_NEW)
    already_done = get_processed_regions(COMBINED_NEW)
    to_process = [(code, path) for code, path in regions if code not in already_done]

    log.info(f"Already processed: {len(already_done)}")
    log.info(f"Remaining: {len(to_process)}")

    if to_process:
        succeeded = []
        failed_set = set()
        total_start = time.time()

        for i, (code, data_path) in enumerate(to_process, 1):
            log.info(f"\n[{i}/{len(to_process)}] Processing {code}...")
            start = time.time()

            stats = process_region(code, data_path)
            elapsed = time.time() - start
            minutes = int(elapsed // 60)
            seconds = int(elapsed % 60)

            if stats:
                record_success(COMBINED_NEW, code, stats)
                succeeded.append(code)
                log.info(
                    f"  {code}: {stats['hotspots']} hotspots, "
                    f"{stats['wilson_rows']:,} wilson rows ({minutes}m {seconds}s)"
                )
                # Delete raw data file after successful processing
                Path(data_path).unlink(missing_ok=True)
            else:
                failed_set.add(code)
                log_failure(code, "pipeline_failed")
                log.error(f"  {code}: FAILED ({minutes}m {seconds}s)")

        # Retry failed countries at subnational level
        retry_failed_as_subnational(failed_set, regions)

        total_elapsed = time.time() - total_start
        log.info(f"\nProcessing complete in {int(total_elapsed // 60)}m {int(total_elapsed % 60)}s")
        log.info(f"  Succeeded: {len(succeeded)}")
        log.info(f"  Failed: {len(failed_set)}")
        if failed_set:
            log.info(f"  Failed regions: {', '.join(sorted(failed_set))}")
            log.info(f"  See {FAILED_LOG} for details")
    else:
        log.info("All regions already processed.")

    # Final sort and species table
    final_sort()
    copy_species_table()

    log.info(f"\nNew database ready at {COMBINED_NEW}")
    log.info("To swap it in:")
    log.info("  1. docker compose stop")
    log.info("  2. python ebird_pipeline.py --swap-only")
    log.info("  3. docker compose start")
    log.info(f"  4. Once verified, delete {COMBINED_OLD}")


if __name__ == "__main__":
    main()
