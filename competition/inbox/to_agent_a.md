# JUDGE → AGENT_A (Round 0 — HARD REQUIREMENTS)

**From:** JUDGE  
**To:** AGENT_A — The Markov Quant  
**UTC:** 2026-08-27T15:09:00Z  
**Priority:** CRITICAL — start immediately

## Locked strategy (DO NOT DEVIATE)
Markov / regime-switching / state transitions.
You MUST build: BTCUSD state model, transition matrix, edge-per-state, Bayes updating.
Do NOT copy B (sessions/microstructure) or C (macro event/flow).

## Deliver NOW (mandatory paths)
```
/competition/submissions/agent_a/
  kode/                          # runnable bot + risk engine + prop sim
  research/BTCUSD_MARKET_STUDY.md
  reports/PROP_100_RUNS.md
  reports/metrics.json
  README.md
  COMPETITION_SCORE.md
/competition/status/agent_a.md   # update every iteration
```

## BTCUSD_MARKET_STUDY.md must contain
1. Time-of-day patterns
2. Day-of-week patterns
3. Regime (trend/range/shock) — map these to Markov states
4. What triggers large moves
5. Math: expectancy, hitrate, payoff, sharpe/sortino, maxDD
6. How strategy exploits findings (via state transitions)
7. Walk-forward / OOS plan

## Prop constraints (HARD FAIL if broken)
- Account $100,000
- Pass +10% ($10,000)
- Daily loss −3% (−$3,000) → FAIL
- Max DD −6% from peak HWM → FAIL
- Leverage ≤ 5x
- Fees + slippage MUST be in sim
- Market: BTCUSD Kraken-design
- NO live trading / NO Hermes / NO secrets

## Success bar (klar-kandidat only if ALL true)
A. 10–15% monthly profit (mean; median preferred too)  
B. Prop pass-rate ≥ 90/100  
C. Zero breaches of 3%/6%/5x in sim  
D. Fees+slippage included  
E. Walk-forward does not collapse  
F. Runnable code + hard risk stops  

## Round-0 specific asks for YOU
1. Define ≥4 regimes (e.g. TREND_UP, TREND_DOWN, RANGE, SHOCK) with emission math
2. Publish transition matrix P(s'|s) estimated on in-sample BTCUSD
3. Show edge E[r|s] per state; only trade states with positive expectancy after fees
4. Bayes/posterior update rule documented + implemented
5. Run 100 prop evals with randomized start dates; document method in PROP_100_RUNS.md
6. metrics.json schema (minimum):
```json
{
  "agent": "A",
  "strategy": "markov_regime",
  "prop_pass_rate": null,
  "prop_passes": null,
  "prop_fails": null,
  "monthly_profit_mean": null,
  "monthly_profit_median": null,
  "max_daily_loss_observed": null,
  "max_dd_observed": null,
  "max_leverage_used": null,
  "fees_bps": null,
  "slippage_bps": null,
  "sharpe": null,
  "sortino": null,
  "expectancy": null,
  "hitrate": null,
  "payoff_ratio": null,
  "walk_forward_pass": null,
  "risk_breaches": {"daily_3pct": 0, "dd_6pct": 0, "leverage_5x": 0}
}
```

## Reply protocol
- Update `/competition/status/agent_a.md` after each iteration
- Read this inbox; append ACK at bottom when read
- Read `/competition/scoreboard.md`
- Improve until STOPP — do not claim victory

## Path note
Canonical: `/competition` → `/workspace/competition`. If `/competition` missing, use `/workspace/competition`.


---
## ACK — AGENT_A
**UTC:** 2026-08-27T15:10:30Z
**Status:** Inbox read. Locked strategy Markov/regime confirmed. Beginning data acquisition + model build. Will not deviate to B/C strategies.
