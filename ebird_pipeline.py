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
import gzip
import logging
import os
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

DEFAULT_MEMORY_LIMIT = "2GB"
LARGE_COUNTRY_THRESHOLD = 7 * 1024 * 1024 * 1024  # 7 GB

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


def split_world_file(world_file, split_dir=None):
    """Split a world TSV into per-country gzip-compressed files.

    Writes compressed .txt.gz files so that the split output (~110 GB) fits
    alongside the world file (~765 GB) on a 1 TB volume. DuckDB can read
    .gz files natively via read_csv_auto.

    Tracks uncompressed bytes per country so the caller can decide which
    countries need subnational subdivision.
    """
    world_file = Path(world_file)
    if split_dir is None:
        split_dir = RAW_DIR / "split"
    split_dir = Path(split_dir)
    split_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"Splitting {world_file.name} by country (streaming, gzip)...")
    file_handles = {}
    country_bytes = {}  # uncompressed bytes per country
    line_count = 0
    file_size = world_file.stat().st_size
    bytes_read = 0
    last_progress = 0

    try:
        with open(world_file, "r", encoding="utf-8") as f:
            header = f.readline()
            bytes_read += len(header.encode("utf-8"))
            columns = header.strip().split("\t")
            try:
                country_idx = columns.index("COUNTRY CODE")
            except ValueError:
                raise RuntimeError(
                    f"COUNTRY CODE column not found. Columns: {columns[:10]}..."
                )

            header_bytes = header.encode("utf-8")

            for line in f:
                line_bytes = line.encode("utf-8")
                bytes_read += len(line_bytes)
                line_count += 1
                fields = line.split("\t")
                if len(fields) <= country_idx:
                    continue
                country = fields[country_idx]

                if country not in file_handles:
                    out_path = split_dir / f"{country}.txt.gz"
                    file_handles[country] = gzip.open(out_path, "wb")
                    file_handles[country].write(header_bytes)
                    country_bytes[country] = len(header_bytes)
                    log.info(f"  New country: {country}")

                file_handles[country].write(line_bytes)
                country_bytes[country] += len(line_bytes)

                # Log progress every 5%
                pct = int(bytes_read / file_size * 100)
                if pct >= last_progress + 5:
                    last_progress = pct
                    log.info(
                        f"  {pct}% ({line_count:,} lines, "
                        f"{len(file_handles)} countries)"
                    )
    finally:
        for fh in file_handles.values():
            fh.close()

    log.info(f"Split complete: {line_count:,} lines into {len(file_handles)} countries")

    # Delete the world file to reclaim disk space
    world_file.unlink()
    log.info("Deleted world file to reclaim disk space")

    # Collect per-country files with their uncompressed sizes
    regions = []
    for gz_file in sorted(split_dir.glob("*.txt.gz")):
        code = gz_file.name.replace(".txt.gz", "")
        regions.append((code, gz_file, country_bytes.get(code, 0)))

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

def process_region(region_code, data_path, memory_limit):
    """Run the frequency pipeline for one region. Returns stats or None."""
    try:
        stats = fdp.run_pipeline(
            region_code=region_code,
            input_path=str(data_path),
            combined_db_path=str(COMBINED_NEW),
            memory_limit=memory_limit,
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


def _split_by_column(data_path, column_name, split_dir):
    """Split a data file by a column using line-by-line streaming.

    Works with both plain .txt and .gz compressed files.
    Returns list of (code, path, uncompressed_bytes) tuples.
    """
    split_dir = Path(split_dir)
    split_dir.mkdir(parents=True, exist_ok=True)

    data_path = Path(data_path)
    opener = gzip.open if data_path.suffix == ".gz" else open
    open_kwargs = {"mode": "rt", "encoding": "utf-8"} if data_path.suffix == ".gz" else {"mode": "r", "encoding": "utf-8"}

    file_handles = {}
    region_bytes = {}
    try:
        with opener(data_path, **open_kwargs) as f:
            header = f.readline()
            header_bytes = len(header.encode("utf-8"))
            columns = header.strip().split("\t")
            try:
                col_idx = columns.index(column_name)
            except ValueError:
                raise RuntimeError(
                    f"{column_name} column not found. Columns: {columns[:10]}..."
                )

            for line in f:
                fields = line.split("\t")
                if len(fields) <= col_idx:
                    continue
                value = fields[col_idx]

                if value not in file_handles:
                    out_path = split_dir / f"{value}.txt"
                    file_handles[value] = open(out_path, "w", encoding="utf-8")
                    file_handles[value].write(header)
                    region_bytes[value] = header_bytes

                file_handles[value].write(line)
                region_bytes[value] += len(line.encode("utf-8"))
    finally:
        for fh in file_handles.values():
            fh.close()

    regions = []
    for txt_file in sorted(split_dir.glob("*.txt")):
        code = txt_file.stem
        regions.append((code, txt_file, region_bytes.get(code, 0)))

    return regions


def _process_subregions(sub_regions, memory_limit, split_column=None):
    """Process a list of (code, path, size) sub-regions. If any sub-region
    exceeds LARGE_COUNTRY_THRESHOLD and a split_column is provided, split it
    further before processing. Skips already-processed regions for resume.
    """
    already_done = get_processed_regions(COMBINED_NEW)
    all_stats = {"hotspots": 0, "wilson_rows": 0}
    all_ok = True

    for sub_code, sub_path, sub_size in sub_regions:
        if sub_code in already_done:
            log.info(f"    {sub_code}: already processed, skipping")
            Path(sub_path).unlink(missing_ok=True)
            continue

        if sub_size > LARGE_COUNTRY_THRESHOLD and split_column:
            log.info(f"    {sub_code} exceeds "
                     f"{LARGE_COUNTRY_THRESHOLD / (1024**3):.0f} GB, "
                     f"splitting by {split_column}...")
            try:
                subsub_dir = TEMP_DIR / f"{sub_code}_sub"
                subsub_regions = _split_by_column(sub_path, split_column,
                                                  subsub_dir)
            except Exception as e:
                log.error(f"    Failed to split {sub_code}: {e}")
                all_ok = False
                log_failure(sub_code, f"split_by_{split_column}_failed")
                continue

            Path(sub_path).unlink(missing_ok=True)
            # Recurse with no further splitting
            child_stats = _process_subregions(
                subsub_regions, memory_limit, split_column=None,
            )
            if subsub_dir.exists():
                shutil.rmtree(subsub_dir)
            if child_stats:
                all_stats["hotspots"] += child_stats["hotspots"]
                all_stats["wilson_rows"] += child_stats["wilson_rows"]
            else:
                all_ok = False
        else:
            log.info(f"    Processing: {sub_code} "
                     f"({sub_size / (1024**3):.1f} GB)")
            stats = process_region(sub_code, sub_path, memory_limit)
            if stats:
                record_success(COMBINED_NEW, sub_code, stats)
                all_stats["hotspots"] += stats["hotspots"]
                all_stats["wilson_rows"] += stats["wilson_rows"]
                log.info(f"      {sub_code}: {stats['hotspots']} hotspots, "
                         f"{stats['wilson_rows']:,} wilson rows")
            else:
                all_ok = False
                log_failure(sub_code, "pipeline_failed")
            Path(sub_path).unlink(missing_ok=True)

    return all_stats if all_ok else None


def process_large_country(code, data_path, memory_limit):
    """Split a large country by STATE CODE, then further by COUNTY CODE
    if any state still exceeds the size threshold."""
    log.info(f"  {code} exceeds {LARGE_COUNTRY_THRESHOLD / (1024**3):.0f} GB, "
             f"splitting by STATE CODE...")
    split_dir = TEMP_DIR / f"{code}_sub"
    try:
        sub_regions = _split_by_column(data_path, "STATE CODE", split_dir)
    except Exception as e:
        log.error(f"  Failed to split {code}: {e}")
        return None

    result = _process_subregions(
        sub_regions, memory_limit, split_column="COUNTY CODE",
    )

    if split_dir.exists():
        shutil.rmtree(split_dir)

    return result


# ---------------------------------------------------------------------------
# Final sort + swap
# ---------------------------------------------------------------------------

def final_sort(memory_limit):
    """Sort rolling_wilson_score by (day_of_year, locality_id) for query performance."""
    log.info("Sorting rolling_wilson_score (this may take a while)...")
    con = duckdb.connect(str(COMBINED_NEW))
    con.execute(f"SET temp_directory = '{TEMP_DIR}';")
    con.execute(f"SET memory_limit = '{memory_limit}';")
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
    parser = argparse.ArgumentParser(description="Automated eBird world data pipeline")
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip download, but still extract and process")
    parser.add_argument("--skip-extract", action="store_true",
                        help="Skip download and extract, resume processing from data/raw/")
    parser.add_argument("--swap-only", action="store_true",
                        help="Only swap combined_new.duckdb -> combined.duckdb")
    parser.add_argument("--sort-only", action="store_true",
                        help="Only run the final sort on combined_new.duckdb")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what regions would be processed, then exit")
    parser.add_argument("--memory", default=DEFAULT_MEMORY_LIMIT,
                        help=f"DuckDB memory limit (default: {DEFAULT_MEMORY_LIMIT})")
    args = parser.parse_args()

    memory_limit = args.memory

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(str(PIPELINE_LOG)),
            logging.StreamHandler(),
        ],
    )

    if args.swap_only:
        swap_databases()
        return

    if args.sort_only:
        final_sort(memory_limit)
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

    # Use existing split files if available, otherwise split the world file
    split_dir = RAW_DIR / "split"
    world_file = RAW_DIR / f"ebd_{RELEASE}.txt"
    existing_splits = sorted(split_dir.glob("*.txt.gz")) if split_dir.exists() else []

    if existing_splits:
        log.info(f"Found {len(existing_splits)} existing split files in {split_dir}")
        regions = []
        for gz_file in existing_splits:
            code = gz_file.name.replace(".txt.gz", "")
            # Estimate uncompressed size from compressed size * 7
            estimated_size = gz_file.stat().st_size * 7
            regions.append((code, gz_file, estimated_size))
    elif world_file.exists():
        log.info("Splitting world file into per-country files...")
        regions = split_world_file(world_file)
    else:
        log.error(f"No world file ({world_file}) or split files ({split_dir}) found.")
        log.error("Run without --skip-extract to download and extract first.")
        sys.exit(1)

    # Initialize the new combined DB
    init_combined_db(COMBINED_NEW)
    already_done = get_processed_regions(COMBINED_NEW)
    to_process = [(code, path, size) for code, path, size in regions
                  if code not in already_done]

    log.info(f"Already processed: {len(already_done)}")
    log.info(f"Remaining: {len(to_process)}")

    if args.dry_run:
        for code, path, size in to_process:
            label = "SPLIT" if size > LARGE_COUNTRY_THRESHOLD else "process"
            log.info(f"  [{label}] {code} ({size / (1024**3):.1f} GB)")
        log.info(f"\n{len(to_process)} regions would be processed.")
        return

    if to_process:
        succeeded = []
        failed_set = set()
        total_start = time.time()

        for i, (code, data_path, uncompressed_size) in enumerate(to_process, 1):
            log.info(f"\n[{i}/{len(to_process)}] Processing {code} "
                     f"({uncompressed_size / (1024**3):.1f} GB uncompressed)...")
            start = time.time()

            if uncompressed_size > LARGE_COUNTRY_THRESHOLD:
                stats = process_large_country(code, data_path, memory_limit)
            else:
                stats = process_region(code, data_path, memory_limit)

            elapsed = time.time() - start
            minutes = int(elapsed // 60)
            seconds = int(elapsed % 60)

            if stats:
                if uncompressed_size <= LARGE_COUNTRY_THRESHOLD:
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
    final_sort(memory_limit)
    copy_species_table()

    log.info(f"\nNew database ready at {COMBINED_NEW}")
    log.info("To swap it in:")
    log.info("  1. docker compose stop")
    log.info("  2. python ebird_pipeline.py --swap-only")
    log.info("  3. docker compose start")
    log.info(f"  4. Once verified, delete {COMBINED_OLD}")


if __name__ == "__main__":
    main()
