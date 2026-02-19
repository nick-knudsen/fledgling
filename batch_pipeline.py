"""Batch runner for frequency_data_pipeline.py.

Scans a directory of eBird TSV files, checks which region DBs already exist,
and runs the pipeline on the missing ones.

Usage:
    python batch_pipeline.py "S:/eBird downloads" Jan-2026
    python batch_pipeline.py "S:/eBird downloads" Jan-2026 --dry-run
"""

import argparse
import os
import re
import subprocess
import sys
import time


def find_tsv_regions(tsv_dir: str, month_year: str) -> dict[str, str]:
    """Find all ebd_*_smp_rel{month_year}.txt files and return {region_code: filepath}."""
    pattern = re.compile(
        rf"^ebd_(.+)_smp_rel{re.escape(month_year)}\.txt$", re.IGNORECASE
    )
    regions = {}
    for name in os.listdir(tsv_dir):
        m = pattern.match(name)
        if m and "_sampling" not in name.lower():
            region_code = m.group(1)
            regions[region_code] = os.path.join(tsv_dir, name)
    return regions


def find_existing_dbs(db_dir: str) -> set[str]:
    """Return set of region codes that already have a .duckdb file (case-insensitive)."""
    existing = set()
    if not os.path.isdir(db_dir):
        return existing
    for name in os.listdir(db_dir):
        if name.endswith(".duckdb"):
            region = name.removesuffix(".duckdb")
            existing.add(region.upper())
    return existing


def run_pipeline(region_code: str, month_year: str, tsv_dir: str) -> bool:
    """Run frequency_data_pipeline.py for a single region. Returns True on success."""
    cmd = [
        sys.executable,
        "frequency_data_pipeline.py",
        region_code,
        month_year,
        tsv_dir,
    ]
    print(f"\n{'='*60}")
    print(f"Running: {' '.join(cmd)}")
    print(f"{'='*60}")
    start = time.time()
    result = subprocess.run(cmd)
    elapsed = time.time() - start
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)
    if result.returncode == 0:
        print(f"Completed {region_code} in {minutes}m {seconds}s")
        return True
    else:
        print(f"FAILED {region_code} (exit code {result.returncode}) after {minutes}m {seconds}s")
        return False


def main():
    parser = argparse.ArgumentParser(description="Batch run frequency data pipeline")
    parser.add_argument("tsv_dir", help="Directory containing eBird TSV files")
    parser.add_argument("month_year", help="Release month-year, e.g. Jan-2026")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed without running")
    parser.add_argument("--only", help="Comma-separated region codes to process (e.g. US-CA,US-TX)")
    args = parser.parse_args()

    db_dir = "data/dbs"
    os.makedirs(db_dir, exist_ok=True)

    tsv_regions = find_tsv_regions(args.tsv_dir, args.month_year)
    existing_dbs = find_existing_dbs(db_dir)

    if args.only:
        only_set = {r.strip().upper() for r in args.only.split(",")}
        tsv_regions = {k: v for k, v in tsv_regions.items() if k.upper() in only_set}

    missing = {
        code: path
        for code, path in sorted(tsv_regions.items())
        if code.upper() not in existing_dbs
    }

    print(f"TSV files found: {len(tsv_regions)}")
    print(f"Existing DBs:    {len(existing_dbs)}")
    print(f"To process:      {len(missing)}")

    if not missing:
        print("\nNothing to do.")
        return

    print("\nRegions to process:")
    for code in sorted(missing):
        print(f"  {code}")

    if args.dry_run:
        print("\n(Dry run - no pipelines executed)")
        return

    succeeded = []
    failed = []
    total_start = time.time()

    for i, (code, path) in enumerate(sorted(missing.items()), 1):
        print(f"\n[{i}/{len(missing)}] Processing {code}...")
        if run_pipeline(code, args.month_year, args.tsv_dir):
            succeeded.append(code)
        else:
            failed.append(code)

    total_elapsed = time.time() - total_start
    total_min = int(total_elapsed // 60)
    total_sec = int(total_elapsed % 60)

    print(f"\n{'='*60}")
    print(f"Batch complete in {total_min}m {total_sec}s")
    print(f"  Succeeded: {len(succeeded)}")
    print(f"  Failed:    {len(failed)}")
    if failed:
        print(f"  Failed regions: {', '.join(failed)}")
    print(f"\nRemember to run merge_databases.py and fetch_taxonomy.py when done.")


if __name__ == "__main__":
    main()
