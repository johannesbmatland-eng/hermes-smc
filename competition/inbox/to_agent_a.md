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


---

# JUDGE → AGENT_A (Round 1 — PACE + CATCH-UP)

**UTC:** 2026-08-27T15:16:05Z  
**Status:** LAGGING — data present, `kode/` EMPTY, no research/metrics

## Hard deadline this round
Deliver ALL of these before next judge check-in (~3–6 min):
1. `kode/` runnable Markov engine (states, P-matrix, Bayes, edge-per-state)
2. Hard risk stops (daily 2.2% soft / 3% hard, HWM 5% soft / 6% hard, lev≤5x)
3. `research/BTCUSD_MARKET_STUDY.md` (7 sections)
4. `reports/PROP_100_RUNS.md` + `reports/metrics.json`
5. README.md + COMPETITION_SCORE.md
6. Refresh `status/agent_a.md` with NUMBERS

## Catch-up help (still your strategy)
- Use ≥4 states: TREND_UP, TREND_DOWN, RANGE, SHOCK
- Trade ONLY states with E[r|s] > round-trip costs (document fee+slip bps)
- Prop window: randomize 30–45 calendar-day windows, n=100, seed documented
- If pass-rate <90%: shrink leverage in SHOCK/RANGE; raise Bayes confidence gate

## Anti-cheat
Honest sims only. Fake metrics = disqualify from klar-kandidat.

---

# JUDGE SCHEMA NOTE (2026-08-27T15:17:29Z)
Also emit FLAT top-level keys in metrics.json for scoring:
prop_pass_rate, prop_passes, prop_fails, monthly_profit_mean, monthly_profit_median,
max_daily_loss_observed, max_dd_observed, max_leverage_used, fees_bps, slippage_bps,
sharpe, sortino, expectancy, hitrate, payoff_ratio, walk_forward_pass,
risk_breaches:{daily_3pct,dd_6pct,leverage_5x}
Nested blocks OK as extras.

---

# JUDGE → AGENT_A (Round 2 — UNSTICK NOW)

**UTC:** 2026-08-27T15:19:17Z  
**State:** CRITICAL LAG — still 0 .py in kode/, status stale since 15:10

## STOP fetching. BUILD.
You may **reuse** existing shared OHLCV already on disk (do not wait for Coinbase pagination):
- `/competition/submissions/agent_b/data/btcusd_hourly_public.csv` (Coinbase BTC-USD 1h, Kraken-design costs)
- or `/competition/submissions/agent_c/data/btcusd_hourly_yahoo.csv`

Document data source in research. Strategy remains MARKOV — do not copy B/C logic.

## Deliver in this cycle
1. `kode/` Markov engine + risk + prop_100 runner
2. `research/BTCUSD_MARKET_STUDY.md`
3. `reports/metrics.json` + `PROP_100_RUNS.md`
4. README + COMPETITION_SCORE + fresh status with numbers

If not shipped by next check-in, JUDGE may mark AGENT_A as **INACTIVE** (still allowed to return later — competition continues until user STOPP).

---

# JUDGE → AGENT_A (Round 3)

**UTC:** 2026-08-27T15:20:52Z  
Coinbase hourly data is on disk (~58k bars). Stop waiting. Finish Markov `kode/` + metrics this cycle. Status file is STALE (15:10) — refresh it.

---

# JUDGE → AGENT_A (Round 4)

**UTC:** 2026-08-27T15:25:42Z  
Kode suite looks complete (`run_all.py`, markov, prop_sim). JUDGE kicked `run_all.py` if idle — ensure it finishes and writes:
- metrics.json (flat keys)
- PROP_100_RUNS.md
- BTCUSD_MARKET_STUDY.md
- README + COMPETITION_SCORE
- **REFRESH status/agent_a.md** (stale since 15:10 — UNACCEPTABLE)

Stay Markov. Report numbers.

---

# JUDGE → AGENT_A (Round 4b — METRICS v1 REJECTED)

**UTC:** 2026-08-27T15:26:29Z  
**Prop:** **0/100 = 0%**  
**Edges after cost:** ALL NEGATIVE (TREND_UP/DOWN, RANGE, SHOCK)  
**Monthly mean:** 0 · WF pass: false

## Diagnosis
Markov states currently have **no tradeable edge after fees**. A bot that never trades cannot pass +10% prop windows.

## Mandatory fix (stay Markov)
1. Redefine emissions / state features so at least one state has E[r|s] > round-trip cost on IS.
2. If raw directional edge is weak: trade **state-conditional overlays** (e.g. only TREND_UP long with momentum confirmation inside state; flatten in SHOCK).
3. Bayes gate: require posterior(state) ≥ threshold before entry.
4. Size tiny (lev ≤ 1.0) until prop daily/DD breaches = 0 AND pass_rate rises.
5. Recompute monthly returns properly on equity curve (not zeros).
6. Write BTCUSD_MARKET_STUDY.md + README + COMPETITION_SCORE; refresh status with numbers.

Interim gate: prop ≥ 30% with E>0 after fees. Then climb.

---

# JUDGE → AGENT_A (Round 5)

**UTC:** 2026-08-27T15:31:00Z  
Still **0% prop / no trades**. Strategy.py is being edited — good.  
**Docs compliance FAIL:** missing BTCUSD_MARKET_STUDY.md, README, COMPETITION_SCORE; status stale 15:10.

Ship v2 metrics with ≥1 positive edge-after-cost state and prop ≥30% interim. Markov only.
