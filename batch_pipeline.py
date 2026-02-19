"""Batch runner for frequency_data_pipeline.py.

Scans a directory of eBird TSV files, checks which region DBs already exist,
and runs the pipeline on the missing ones. For each region, automatically
uses the most recent release file.

Usage:
    python batch_pipeline.py "S:/eBird downloads"
    python batch_pipeline.py "S:/eBird downloads" --dry-run
    python batch_pipeline.py "S:/eBird downloads" --only US-CA,US-TX
    python batch_pipeline.py "S:/eBird downloads" --exclude US-CA,US-TX
    python batch_pipeline.py "S:/eBird downloads" --only US-CA --overwrite

"""

import argparse
import os
import re
import subprocess
import sys
import time

TSV_PATTERN = re.compile(
    r"^ebd_(.+)_smp_rel(.+)\.txt$", re.IGNORECASE
)

# Month abbreviations ordered for comparison
MONTH_ORDER = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def parse_month_year(month_year: str) -> tuple[int, int]:
    """Parse 'Jan-2026' into (2026, 1) for sorting."""
    parts = month_year.split("-")
    if len(parts) == 2 and parts[0] in MONTH_ORDER:
        return int(parts[1]), MONTH_ORDER[parts[0]]
    return 0, 0


def find_tsv_regions(tsv_dir: str) -> dict[str, tuple[str, str]]:
    """Find eBird TSV files, picking the most recent release per region.

    Returns {region_code: (month_year, filepath)}.
    """
    # Collect all matches per region
    by_region: dict[str, list[tuple[str, str]]] = {}
    for name in os.listdir(tsv_dir):
        m = TSV_PATTERN.match(name)
        if m and "_sampling" not in name.lower():
            region = m.group(1)
            month_year = m.group(2)
            by_region.setdefault(region, []).append(
                (month_year, os.path.join(tsv_dir, name))
            )

    if not by_region:
        print(f"No eBird TSV files found in {tsv_dir}")
        sys.exit(1)

    # Pick the most recent release for each region
    result = {}
    for region, files in by_region.items():
        files.sort(key=lambda x: parse_month_year(x[0]), reverse=True)
        result[region] = files[0]  # (month_year, filepath)

    return result


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


def run_pipeline(region_code: str, month_year: str, tsv_dir: str, overwrite: bool = False) -> bool:
    """Run frequency_data_pipeline.py for a single region. Returns True on success."""
    if overwrite:
        db_path = f"data/dbs/{region_code}.duckdb"
        if os.path.exists(db_path):
            os.remove(db_path)
            print(f"Deleted existing {db_path}")
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
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed without running")
    parser.add_argument("--only", help="Comma-separated region codes to process (e.g. US-CA,US-TX)")
    parser.add_argument("--exclude", help="Comma-separated region codes to skip (e.g. US-CA,US-TX)")
    parser.add_argument("--overwrite", action="store_true", help="Re-run pipeline even if DB already exists (deletes existing DB first)")
    args = parser.parse_args()

    db_dir = "data/dbs"
    os.makedirs(db_dir, exist_ok=True)

    tsv_regions = find_tsv_regions(args.tsv_dir)
    existing_dbs = find_existing_dbs(db_dir)

    if args.only:
        only_set = {r.strip().upper() for r in args.only.split(",")}
        tsv_regions = {k: v for k, v in tsv_regions.items() if k.upper() in only_set}

    if args.exclude:
        exclude_set = {r.strip().upper() for r in args.exclude.split(",")}
        tsv_regions = {k: v for k, v in tsv_regions.items() if k.upper() not in exclude_set}

    if args.overwrite:
        missing = dict(sorted(tsv_regions.items()))
    else:
        missing = {
            code: info
            for code, info in sorted(tsv_regions.items())
            if code.upper() not in existing_dbs
        }

    print(f"TSV files found: {len(tsv_regions)}")
    print(f"Existing DBs:    {len(existing_dbs)}")
    print(f"To process:      {len(missing)}")

    if not missing:
        print("\nNothing to do.")
        return

    print("\nRegions to process:")
    for code, (month_year, _) in sorted(missing.items()):
        print(f"  {code} ({month_year})")

    if args.dry_run:
        print("\n(Dry run - no pipelines executed)")
        return

    succeeded = []
    failed = []
    total_start = time.time()

    for i, (code, (month_year, _)) in enumerate(sorted(missing.items()), 1):
        print(f"\n[{i}/{len(missing)}] Processing {code} ({month_year})...")
        if run_pipeline(code, month_year, args.tsv_dir, args.overwrite):
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
    print(f"\nRemember to run merge_databases.py when done.")


if __name__ == "__main__":
    main()