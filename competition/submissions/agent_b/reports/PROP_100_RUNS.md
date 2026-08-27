# PROP 100 RUNS — AGENT_B Microstructure Hybrid

**UTC:** 2026-08-27T15:58:16Z  
**Method:** 100 randomized start indices on Coinbase BTC-USD 1h (2023-01 → 2026-08), `seed=42`.  
Each run: $100k start, challenge window **90 days**, pass at **+10%**, hard fail at daily **−3%** or HWM DD **−6%**, leverage ≤5×. Fees+slip (3+3 bps/side) on every entry/exit.

## Summary

| Metric | Value |
|---|---|
| Passes | **24 / 100 (24%)** |
| Fails | 76 |
| Mean PnL (all runs) | +0.57% |
| Median PnL | −2.36% |
| Daily −3% breaches (sum) | 16 |
| HWM −6% breaches (sum) | 7 |
| Leverage >5× | 0 |
| Max lev used (config) | 0.90 |

## Fail taxonomy

| Reason | Count |
|---|---|
| timeout_no_pass | 53 |
| daily_loss | 16 |
| max_dd | 7 |

**Interpretation:** Dominant fail is **timeout** — edge is real but small (~0.6%/mo full-sample), so many 90d windows never reach +10% before time expires. Daily/DD fails are secondary at lev≤0.9.

## Worst 10 failing seeds (by pnl)

| run | start_dt (UTC) | fail_reason | pnl | max_dd |
|---|---|---|---|---|
| 95 | 2023-12-29 | daily_loss | −6.33% | 6.34% |
| 5 | 2025-12-05 | max_dd | −6.08% | 6.08% |
| 36 | 2025-12-04 | max_dd | −6.08% | 6.08% |
| 32 | 2023-04-30 | timeout | −5.95% | 5.95% |
| 28 | 2024-11-11 | daily_loss | −5.65% | 5.65% |
| 27 | 2025-10-21 | timeout | −5.52% | 5.94% |
| 12 | 2025-07-05 | timeout | −5.51% | 5.81% |
| 18 | 2025-11-11 | timeout | −5.44% | 5.82% |
| 44 | 2023-03-31 | timeout | −5.40% | 5.82% |
| 86 | 2023-05-06 | timeout | −5.38% | 5.82% |

Worst-10 taxonomy: timeout 6, daily_loss 2, max_dd 2.

## Stress tests (prop pass rate)

| Stress | Pass rate |
|---|---|
| Baseline (90d) | 24% |
| 2× fees | 23% |
| 2× slippage | 23% |
| Daily soft-stop 1.5% | 21% |
| OOS starts (last 30% timeline) | 28% |

## Math note (why not ≥90%)

Causal Lon/NY continuation after 12 bps RT has E≈+9–28 bps/trade at ~0.9× leverage → ~0.6%/month full-sample.  
P(reach +10% in 90d without −3%/−6%) under BTC hourly vol is structurally <<90% without either (a) much larger edge or (b) leverage that re-introduces daily fails.  
**Interim gate ≥50% not yet met.** Next: raise E (stricter filters / better session gating) before raising size.

## Artifact

Raw runs: `reports/prop_100_runs.csv`
