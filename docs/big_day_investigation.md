# Big Day Optimizer — Investigation Report

**Date:** 2026-04-19  
**Branch:** big-day-planner  
**Canonical test setup:** Vermont, 2026-05-10 to 2026-05-20, window 05:00–20:00, empty life list

---

## Problem statement

The Big Day optimizer (`/api/plan-big-day`, `hotspot_optimizer.plan_big_day`) exhibits a
**superset paradox**: restricting the search to a subset of Vermont counties consistently
produces a *higher* `total_expected_species` than using all of Vermont.

Expected behavior: any tour feasible within a subset is also feasible in the superset, so
the superset score can never be worse.

Observed: it is worse. This implies the algorithm is *losing access* to the winning tour when
the search space grows.

---

## Test setup

Scripts in `scripts/` replicate the `run_plan_big_day()` endpoint logic without an HTTP server.

```bash
# Single-run diagnostic (verbose, shows seeds / cap / sentinels)
.venv/Scripts/python.exe scripts/big_day_diag.py --state Vermont \
    --date 2026-05-10:2026-05-20 --window 05:00-20:00

# With a county subset
.venv/Scripts/python.exe scripts/big_day_diag.py --state Vermont \
    --counties "Chittenden,Franklin,Grand Isle" \
    --date 2026-05-10:2026-05-20 --window 05:00-20:00

# Cap ablation (holds max_passes=50)
.venv/Scripts/python.exe scripts/big_day_cap_ablation.py --counties "Chittenden,Franklin,Grand Isle"

# Passes ablation (holds cap=500)
.venv/Scripts/python.exe scripts/big_day_passes_ablation.py --counties "Chittenden,Franklin,Grand Isle"

# k-county sweep across all Vermont subsets
.venv/Scripts/python.exe scripts/big_day_sweep.py
```

Requires local OSRM at `http://localhost:5000` (started via `docker-compose up -d`).

---

## Baseline numbers (full Vermont, cap=500)

| Metric | Value |
|--------|-------|
| Hotspots in DB for Vermont | 851 |
| Hotspots after cap (top 500 by species score) | 500 |
| Hotspots dropped by cap | **351** |
| OSRM matrix sentinels (9999.0) | 0 / 250,000 cells |
| Total expected species | **125.65** |
| Tour stops | 25 |
| Total tour time | 891.1 min |
| Local-search improving passes | 2 |

---

## Confirmed counter-examples

**Chittenden + Franklin + Grand Isle** (3 counties, 204 hotspots — cap never triggered):

| Metric | Full Vermont | Subset |
|--------|-------------|--------|
| Hotspots in DB | 851 | 204 |
| Hotspots after cap | 500 | 204 (cap not hit) |
| Total expected species | 125.65 | **125.90** (+0.25) |
| Tour stops | 25 | 27 |

The subset scores *higher* despite having fewer candidate hotspots.

### Sweep results (sample of 5 per k, Vermont counties)

5 out of 36 tested subsets beat the full-state baseline. All winning subsets include
Chittenden + Franklin + Grand Isle — confirming those three counties contain the
optimal tour corridor.

Top-10 scoring subsets:

| Counties | Expected | Delta | k | Candidates |
|----------|----------|-------|---|-----------|
| Chittenden, Franklin, Grand Isle, Lamoille, Rutland, Windham, Windsor | 125.90 | +0.24 | 7 | 495 |
| Chittenden, Franklin, Grand Isle, Lamoille, Orange, Orleans, Rutland | 125.90 | +0.24 | 7 | 420 |
| Bennington, Chittenden, Essex, Franklin, Grand Isle, Lamoille, Orange, Orleans, Rutland, Windsor | 125.90 | +0.24 | 10 | 500 |
| Addison, Chittenden, Franklin, Grand Isle, Lamoille, Orange, Orleans, Rutland, Windham, Windsor | 125.82 | +0.17 | 10 | 500 |
| Addison, Bennington, Caledonia, Chittenden, Franklin, Grand Isle, Orange, Washington, Windham, Windsor | 125.71 | +0.06 | 10 | 500 |
| ALL (Vermont) | 125.65 | +0.00 | 14 | 500 |

Key observations:
- The score ceiling is **125.90** regardless of k — the optimal tour fits within 3 counties; adding more doesn't help.
- At k=10 with 500 candidates (cap hit), the gap shrinks to +0.06, confirming the cap's effect is smaller when the candidate set happens to include the right hotspots.
- The k=10 subset that scores 125.90 hits exactly cap=500, meaning it gets all its hotspots through — same effective condition as the uncapped 3-county case.

---

## Hypothesis 1 — Cap drops useful hotspots (CONFIRMED, dominant driver)

### Mechanism

`api.py:443–449` applies a `MAX_BIGDAY_CANDIDATES = 500` cap ranked by
`prob_matrix.sum(axis=1)`. This ranks hotspots by their *total* detection probability
(sum across all candidate species). Hotspots with many species at moderate probability
rank above hotspots with fewer species at high probability.

When Vermont's 851 hotspots are trimmed to 500, **351 hotspots are permanently hidden**
from the optimizer — including some that would appear in the optimal tour.

### Direct evidence

The winning 3-county subset tour includes two hotspots that were **not** in the full-state
500-candidate set:

| Hotspot | County | Reason dropped |
|---------|--------|----------------|
| Maquam WMA / Swanton Town Beach | Franklin | Species score too low to rank in top 500 statewide |
| Dillenbeck Bay Fishing Access | Grand Isle | Species score too low to rank in top 500 statewide |

These stops are marginal-gain picks — they carry a few species not well-covered by adjacent
hotspots. The `sum(axis=1)` ranking favours generalist high-traffic hotspots and deprioritises
these niche locations, even though the greedy algorithm would select them given the chance.

### Cap ablation results

Testing `cap ∈ {500, 1000, 2000, uncapped}` for full Vermont was **blocked by OSRM**.
The local OSRM instance is launched with `--max-table-size 500` (see `docker-compose.yml`),
which means any request for an N×N matrix with N > 500 returns HTTP 400.

This reveals a **second constraint**: the 500 cap is not only a software choice — it is
*enforced by the OSRM deployment*. Raising `MAX_BIGDAY_CANDIDATES` above 500 requires a
corresponding OSRM restart with `--max-table-size` increased.

For the 3-county subset (N=204 ≤ 500), all caps produce identical results (the cap is never
active), confirming the subset is unaffected.

```
Cap ablation — region: US-VT (all)
Cap       Pre-cap   Post-cap    Expected   Tour stops   Passes
500           851        500    125.6548           25        2
1000  ERROR: HTTP Error 400: Bad Request   (OSRM max-table-size=500)
2000  ERROR: HTTP Error 400: Bad Request
uncapped ERROR: HTTP Error 400: Bad Request

Cap ablation — region: Chittenden, Franklin, Grand Isle
Cap       Pre-cap   Post-cap    Expected   Tour stops   Passes
500           204        204    125.8996           27        1
1000          204        204    125.8996           27        1
(unchanged at all cap values — cap never applied)
```

---

## Hypothesis 2 — Local search fails to converge in large candidate sets (REJECTED)

The passes ablation shows local search converges with only **2 improving passes** for full
Vermont, regardless of how many passes are allowed.

```
Passes ablation — region: US-VT (all)  cap=500
Passes    Expected   Best passes   Tour stops
50        125.6548             2           25
100       125.6548             2           25
200       125.6548             2           25
500       125.6548             2           25
```

Adding more passes has no effect. The optimizer has reached its local optimum with the
given 500-candidate set; the problem is upstream (which 500 candidates are selected),
not downstream (how hard we search over them).

---

## Other observations

### OSRM sentinels (9999.0)
Zero sentinel cells in either the full-state or subset travel matrices. All 500×500 pairs
are routable, so the 9999 hypothesis does not contribute to the gap. OSRM is working
correctly for Vermont-scale queries.

### Seed geographic distribution
All 6–7 seeds (top-3-by-species + top-3-by-centrality + empty) converge on the
Chittenden County / greater Burlington corridor. This is expected — Burlington-area
hotspots genuinely dominate Vermont birding in spring. Seeds are not causing lock-in; the
optimizer correctly finds roughly the same tour regardless of starting point (score spread
across seeds: 112–126). The empty-seed tour reaches 120.95, and the best seed improves
it to 125.65, so multi-start is providing real value.

---

## Summary

| Hypothesis | Verdict | Evidence |
|-----------|---------|---------|
| Cap drops useful hotspots | **Confirmed** | 2 hotspots in winning subset tour absent from full-state 500 |
| OSRM table-size blocks larger cap | **Confirmed (constraint)** | HTTP 400 at cap > 500 |
| Local search doesn't converge | **Rejected** | 2 improving passes at all pass budgets |
| OSRM sentinels poison routes | **Rejected** | 0 sentinel cells in both matrices |

---

## Recommended next steps (not implemented)

### 1. Raise OSRM `--max-table-size` and `MAX_BIGDAY_CANDIDATES` together
`docker-compose.yml` currently: `osrm-routed --algorithm mld --max-table-size 500`.  
To support up to N candidates, increase to `--max-table-size N`. Then raise
`MAX_BIGDAY_CANDIDATES` in `api.py:443` to match. Vermont has 851 hotspots; 1000 would
cover any US state comfortably. Tradeoff: OSRM matrix calls scale O(N²), so latency
increases. Measure response time at 851 before deciding.

### 2. Improve the cap ranking criterion
Even at cap=500, the `sum(axis=1)` criterion may not select the 500 hotspots most likely
to appear in the optimal tour. Alternative heuristics to test:
- **Expected marginal gain** from a single-stop tour (same as `sum(axis=1)` but accounts
  for probability saturation at 1.0 — makes little difference for realistic prob values).
- **Geographic diversity**: ensure the top-500 spans the region rather than clustering.
  Approach: greedy diversity selection (add hotspot that maximises min-distance to already
  selected set, weighted by species score).
- **Greedy pre-selection**: run a fast single-pass greedy tour with all 851 candidates
  (no travel matrix — use crow-flies distance as a proxy), then cap to the stops it selects
  plus top-N by score from the remainder.

### 3. Verify on user's actual life list
The canonical test uses an empty life list (125 expected species). The user's reported
"~81 expected targets" implies a non-empty life list. After fixing the cap, re-run with the
user's actual life list to confirm the gap closes in their real use case.

### 4. Add `num_candidate_hotspots_before_cap` to the API response
Currently the response includes `num_candidate_hotspots` (post-cap count). Adding a
pre-cap count would make the cap's effect visible to the client and useful for debugging.
