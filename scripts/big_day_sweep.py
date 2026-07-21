"""
Sweep all k-county subsets of Vermont to find which subsets beat the
full-state baseline expected species.

Results written to scripts/big_day_sweep_results.csv and summarized to stdout.

Usage:
    python scripts/big_day_sweep.py
    python scripts/big_day_sweep.py --max-sample 20   # random sample per k
"""
from __future__ import annotations

import argparse
import csv
import itertools
import random
import sys
import time
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.big_day_diag import run_diagnostic, VT_ALL_COUNTIES, _parse_hhmm

STATE = "Vermont"
START_DATE = date(2026, 5, 10)
END_DATE = date(2026, 5, 20)
WINDOW_START = _parse_hhmm("05:00")
WINDOW_END = _parse_hhmm("20:00")
CAP = 500
MAX_PASSES = 50
LIFE_LIST: list[str] = []

# k values to sweep; for large k the number of combos is huge so we sample
K_VALUES = [1, 2, 3, 4, 5, 7, 10, 14]
# Max subsets to sample per k (0 = enumerate all)
DEFAULT_MAX_SAMPLE = 40


def run_sweep(max_sample: int = DEFAULT_MAX_SAMPLE) -> None:
    out_path = REPO_ROOT / "scripts" / "big_day_sweep_results.csv"

    rows = []

    # Full-state baseline
    print("Running full-state baseline (US-VT) ...")
    t0 = time.time()
    baseline = run_diagnostic(
        state_counties=None,
        states=[STATE],
        start_date=START_DATE,
        end_date=END_DATE,
        window_start_min=WINDOW_START,
        window_end_min=WINDOW_END,
        life_list=LIFE_LIST,
        cap=CAP,
        max_passes=MAX_PASSES,
        verbose=False,
    )
    baseline_score = baseline["total_expected_species"]
    baseline_candidates = baseline["post_cap_count"]
    elapsed = time.time() - t0
    print(f"  Baseline: {baseline_score} expected species, "
          f"{baseline_candidates} candidates, {elapsed:.1f}s")
    rows.append({
        "k": 14,
        "counties": "ALL",
        "expected_species": baseline_score,
        "num_candidates": baseline_candidates,
        "tour_stops": baseline["best_tour_len"],
        "travel_minutes": baseline["total_tour_minutes"],
        "beats_baseline": False,
        "delta": 0.0,
    })

    for k in K_VALUES:
        all_combos = list(itertools.combinations(VT_ALL_COUNTIES, k))
        if max_sample > 0 and len(all_combos) > max_sample:
            sample = random.sample(all_combos, max_sample)
        else:
            sample = all_combos

        print(f"\nk={k}: testing {len(sample)} of {len(all_combos)} subsets ...")
        k_scores = []
        for combo in sample:
            county_str = ", ".join(combo)
            state_counties = [(STATE, c) for c in combo]
            try:
                result = run_diagnostic(
                    state_counties=state_counties,
                    states=None,
                    start_date=START_DATE,
                    end_date=END_DATE,
                    window_start_min=WINDOW_START,
                    window_end_min=WINDOW_END,
                    life_list=LIFE_LIST,
                    cap=CAP,
                    max_passes=MAX_PASSES,
                    verbose=False,
                )
                score = result["total_expected_species"]
                beats = score > baseline_score + 0.01
                delta = round(score - baseline_score, 4)
                k_scores.append(score)
                rows.append({
                    "k": k,
                    "counties": county_str,
                    "expected_species": score,
                    "num_candidates": result["post_cap_count"],
                    "tour_stops": result["best_tour_len"],
                    "travel_minutes": result["total_tour_minutes"],
                    "beats_baseline": beats,
                    "delta": delta,
                })
                status = "BEATS" if beats else "     "
                print(f"  {status} {county_str}: {score} ({delta:+.2f})")
            except Exception as e:
                print(f"  ERROR {county_str}: {e}")
                rows.append({
                    "k": k,
                    "counties": county_str,
                    "expected_species": None,
                    "num_candidates": None,
                    "tour_stops": None,
                    "travel_minutes": None,
                    "beats_baseline": False,
                    "delta": None,
                })

        if k_scores:
            print(f"  k={k} summary: max={max(k_scores):.4f}  "
                  f"median={sorted(k_scores)[len(k_scores)//2]:.4f}  "
                  f"min={min(k_scores):.4f}  "
                  f"beats_baseline={sum(1 for s in k_scores if s > baseline_score + 0.01)}/{len(k_scores)}")

    # Write CSV
    fieldnames = ["k", "counties", "expected_species", "num_candidates",
                  "tour_stops", "travel_minutes", "beats_baseline", "delta"]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nResults written to {out_path}")

    # Top-10 summary
    scored = [r for r in rows if r["expected_species"] is not None]
    scored.sort(key=lambda r: r["expected_species"], reverse=True)
    print("\nTop-10 highest-scoring subsets:")
    print(f"  {'Counties':<60} {'Expected':>10}  {'Delta':>8}  {'k':>4}  {'Cands':>6}")
    print(f"  {'-'*60} {'-'*10}  {'-'*8}  {'-'*4}  {'-'*6}")
    for r in scored[:10]:
        print(f"  {r['counties']:<60} {r['expected_species']:>10.4f}  "
              f"{r['delta']:>+8.4f}  {r['k']:>4}  {r['num_candidates'] or 0:>6}")

    beats_all = [r for r in scored if r.get("beats_baseline")]
    print(f"\nSubsets that beat the full-state baseline ({baseline_score}): "
          f"{len(beats_all)} out of {len(scored)-1} tested")


def main():
    parser = argparse.ArgumentParser(description="Vermont county subset sweep")
    parser.add_argument("--max-sample", type=int, default=DEFAULT_MAX_SAMPLE,
                        help="Max subsets to sample per k (0=all)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for sampling")
    args = parser.parse_args()
    random.seed(args.seed)
    run_sweep(max_sample=args.max_sample)


if __name__ == "__main__":
    main()
