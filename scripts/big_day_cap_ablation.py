"""
Cap ablation: hold max_passes=50, vary MAX_BIGDAY_CANDIDATES.

For both the full-state baseline and the best-scoring subset from the sweep,
test cap in {500, 1000, 2000, uncapped (0)}.

Usage:
    python scripts/big_day_cap_ablation.py
    python scripts/big_day_cap_ablation.py --counties "Chittenden,Addison,Franklin"
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.big_day_diag import run_diagnostic, _parse_hhmm

STATE = "Vermont"
START_DATE = date(2026, 5, 10)
END_DATE = date(2026, 5, 20)
WINDOW_START = _parse_hhmm("05:00")
WINDOW_END = _parse_hhmm("20:00")
MAX_PASSES = 50
LIFE_LIST: list[str] = []

CAPS = [500, 1000, 2000, 0]  # 0 = uncapped


def ablate_cap(counties: list[str] | None = None) -> None:
    if counties:
        state_counties = [(STATE, c) for c in counties]
        states = None
        region_label = ", ".join(counties)
    else:
        state_counties = None
        states = [STATE]
        region_label = "US-VT (all)"

    print(f"\n{'='*65}")
    print(f"Cap ablation — region: {region_label}")
    print(f"{'='*65}")
    print(f"{'Cap':>8}  {'Pre-cap':>8}  {'Post-cap':>9}  {'Expected':>10}  {'Tour stops':>11}  {'Passes':>7}")
    print(f"{'-'*8}  {'-'*8}  {'-'*9}  {'-'*10}  {'-'*11}  {'-'*7}")

    for cap in CAPS:
        cap_label = "uncapped" if cap == 0 else str(cap)
        try:
            result = run_diagnostic(
                state_counties=state_counties,
                states=states,
                start_date=START_DATE,
                end_date=END_DATE,
                window_start_min=WINDOW_START,
                window_end_min=WINDOW_END,
                life_list=LIFE_LIST,
                cap=cap,
                max_passes=MAX_PASSES,
                verbose=False,
            )
            print(f"{cap_label:>8}  "
                  f"{result['pre_cap_count']:>8}  "
                  f"{result['post_cap_count']:>9}  "
                  f"{result['total_expected_species']:>10.4f}  "
                  f"{result['best_tour_len']:>11}  "
                  f"{result['best_improving_passes']:>7}")
        except Exception as e:
            print(f"{cap_label:>8}  ERROR: {e}")


def main():
    parser = argparse.ArgumentParser(description="Cap ablation for Big Day optimizer")
    parser.add_argument("--counties", help="Comma-separated county names for subset run")
    args = parser.parse_args()

    # Full-state run
    ablate_cap(counties=None)

    # Subset run
    if args.counties:
        county_list = [c.strip() for c in args.counties.split(",")]
        ablate_cap(counties=county_list)
    else:
        print("\n(Tip: pass --counties 'Chittenden,Addison,Franklin' to also test a specific subset)")


if __name__ == "__main__":
    main()
