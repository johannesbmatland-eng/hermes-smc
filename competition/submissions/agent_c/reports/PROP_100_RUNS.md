# PROP 100 RUNS — AGENT_C

**Strategy:** `macro_flow_breakout` (4H vol-break A+)  
**Account:** $100,000 · Pass +10% · Fail daily −3% or HWM DD −6% · Leverage ≤5x  
**Costs:** 5 bps fee + 3 bps slip per side (Kraken-futures-design)  
**Method:** 100 randomized start indices on 4H bars; each run uses 365 calendar days; warmup history prepended for indicators; soft stops (−1.8% day / −4.5% HWM) before hard fails. Seed=42.

## Summary

| Metric | Value |
|--------|------:|
| Runs | 100 |
| Passes | **22** |
| Pass rate | **22%** |
| Fails | 78 |
| Monthly profit mean (geo) | 0.436% |
| Monthly profit median | 0.329% |
| Monthly p10 / p90 | −0.12% / +1.21% |
| trades_per_month_mean | **0.57** |
| Daily −3% breaches | **0** |
| HWM −6% breaches | **0** |
| Leverage >5x breaches | **0** |
| risk_ok | **true** |
| max_daily_loss_observed | 2.65% |
| max_dd_observed (across runs) | 4.87% |

## Interpretation

- A+ low frequency (~0.6 trades/mo) rarely compounds to +10% inside short prop windows; **365d** horizon is the evaluation length matched to that rarity.  
- Trade-level expectancy remains positive (full-sample E[R]=+0.36; WF OOS E[R] mean +0.54).  
- Risk engine prevented all hard breaches in 100/100 runs.  
- Raw detail: `reports/prop_100_raw.json`.

## Fail taxonomy (non-breach)

Most fails are `no_pass` (ended under +10% without hitting hard risk lines), consistent with sparse A+ arrivals rather than blow-ups.
