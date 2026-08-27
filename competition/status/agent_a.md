# AGENT_A STATUS

**Role:** AGENT_A — The Markov Quant  
**Updated by:** AGENT_A  
**UTC:** 2026-08-27T16:05:00Z  
**State:** SUBMISSION_V1 — awaiting judge

## Locked strategy
Markov regime-switching / state transitions / Bayes posterior. States: TREND_UP, TREND_DOWN, RANGE, SHOCK.

## Deliverables
- [x] kode/ (bot + risk + backtest + prop 100)
- [x] research/BTCUSD_MARKET_STUDY.md (7 sections)
- [x] reports/PROP_100_RUNS.md
- [x] reports/metrics.json
- [x] README.md
- [x] COMPETITION_SCORE.md

## Key metrics (honest, post improve-cycle)
| Metric | Value |
|---|---|
| prop_pass_rate | **0.05** (5/100) |
| monthly_profit_mean | **0.00605** (~0.61%) |
| monthly_profit_median | ~0 |
| risk_breaches | **{daily_3pct:0, dd_6pct:0, leverage_5x:0}** |
| max_leverage_used | 0.55x |
| max_dd_observed | 4.7% |
| max_daily_loss_observed | −2.2% |
| sharpe (OOS) | −0.53 |
| walk_forward_pass | false |
| fees_bps / slippage_bps | 8 / 3 |

## Iteration log
1. Bootstrap: Coinbase hourly BTCUSD 2020–2026; Markov fit; first prop run ~10% pass, some daily breaches.
2. Improve: gap-aware fills, equity stops, lev≤0.55 → **0 breaches**, pass 5/100, monthly ~0.6%.

## Self-score
≈ **36 / 100** (risk+research+code strong; pass/profit weak).

## ACK
Inbox Round-0 read and ACK'd. Not claiming victory. Awaiting judge.
