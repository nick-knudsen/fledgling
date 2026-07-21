"""
Passes ablation: hold cap=500, vary max_passes for local search.

Tests whether the full-state optimizer simply needs more iterations to
converge on the solution it's missing.

Usage:
    python scripts/big_day_passes_ablation.py
    python scripts/big_day_passes_ablation.py --counties "Chittenden,Addison,Franklin"
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
CAP = 500
LIFE_LIST: list[str] = []

PASS_VALUES = [50, 100, 200, 500]


def ablate_passes(counties: list[str] | None = None) -> None:
    if counties:
        state_counties = [(STATE, c) for c in counties]
        states = None
        region_label = ", ".join(counties)
    else:
        state_counties = None
        states = [STATE]
        region_label = "US-VT (all)"

    print(f"\n{'='*65}")
    print(f"Passes ablation — region: {region_label}  cap={CAP}")
    print(f"{'='*65}")
    print(f"{'Passes':>8}  {'Expected':>10}  {'Best passes':>12}  {'Tour stops':>11}")
    print(f"{'-'*8}  {'-'*10}  {'-'*12}  {'-'*11}")

    for max_passes in PASS_VALUES:
        try:
            result = run_diagnostic(
                state_counties=state_counties,
                states=states,
                start_date=START_DATE,
                end_date=END_DATE,
                window_start_min=WINDOW_START,
                window_end_min=WINDOW_END,
                life_list=LIFE_LIST,
                cap=CAP,
                max_passes=max_passes,
                verbose=False,
            )
            print(f"{max_passes:>8}  "
                  f"{result['total_expected_species']:>10.4f}  "
                  f"{result['best_improving_passes']:>12}  "
                  f"{result['best_tour_len']:>11}")
        except Exception as e:
            print(f"{max_passes:>8}  ERROR: {e}")


def main():
    parser = argparse.ArgumentParser(description="Passes ablation for Big Day optimizer")
    parser.add_argument("--counties", help="Comma-separated county names for subset run")
    args = parser.parse_args()

    ablate_passes(counties=None)

    if args.counties:
        county_list = [c.strip() for c in args.counties.split(",")]
        ablate_passes(counties=county_list)
    else:
        print("\n(Tip: pass --counties 'Chittenden,Addison,Franklin' to also test a specific subset)")


if __name__ == "__main__":
    main()
