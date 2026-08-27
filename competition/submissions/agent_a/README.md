# AGENT_A — Markov Regime BTCUSD Prop Bot

Cold probability machine. States. Transitions. Posterior updates. No storytelling — only P(s′|s) and E[r|s].

## Strategy lock
**Markov / regime-switching** with four states: `TREND_UP`, `TREND_DOWN`, `RANGE`, `SHOCK`.

- Transition matrix estimated in-sample
- Hold-horizon edge E[r|s] after fees/slippage
- Bayes posterior update each bar
- Trades only SHOCK-recovery and selected positive transition edges (long-only)

## Prop rules enforced
| Rule | Value |
|---|---|
| Account | $100,000 |
| Pass | +10% |
| Daily loss fail | −3% |
| Max DD from peak | −6% |
| Max leverage | 5x (used ≤ 0.55x) |
| Fees | 8 bps/side |
| Slippage | 3 bps/side |

## Layout
```
agent_a/
  kode/           # runnable Python package
  data/           # BTCUSD hourly OHLCV
  research/       # BTCUSD_MARKET_STUDY.md
  reports/        # metrics.json, PROP_100_RUNS.md
  README.md
  COMPETITION_SCORE.md
```

## Run
```bash
cd /competition/submissions
python3 -m agent_a.kode.run_all
```

Requires: `numpy`, `pandas`.

## Latest honest metrics (iteration 1 + improve)
See `reports/metrics.json`:
- prop_pass_rate ≈ **5%** (5/100)
- monthly_profit_mean ≈ **0.6%**
- risk_breaches: **all zero**
- walk_forward_pass: **false**

Targets (90% pass, 10–15%/mo) **not met**. Risk integrity held. Further iterations required.
