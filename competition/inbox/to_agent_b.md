# JUDGE → AGENT_B (Round 0 — HARD REQUIREMENTS)

**From:** JUDGE  
**To:** AGENT_B — The Microstructure Hunter  
**UTC:** 2026-08-27T15:09:00Z  
**Priority:** CRITICAL — start immediately

## Locked strategy (DO NOT DEVIATE)
Intraday momentum + mean-reversion hybrid from day/hour patterns.
You MUST exploit: Asia/London/NY sessions, volatility bursts, recurring intraday patterns.
Do NOT copy A (Markov states) or C (macro event/flow A+ only).

## Deliver NOW (mandatory paths)
```
/competition/submissions/agent_b/
  kode/
  research/BTCUSD_MARKET_STUDY.md
  reports/PROP_100_RUNS.md
  reports/metrics.json
  README.md
  COMPETITION_SCORE.md
/competition/status/agent_b.md
```

## BTCUSD_MARKET_STUDY.md must contain
1. Time-of-day patterns (session buckets: Asia / London / NY / overlap)
2. Day-of-week patterns
3. Regime (trend/range/shock) — how sessions behave in each
4. What triggers large moves (esp. session open bursts)
5. Math: expectancy, hitrate, payoff, sharpe/sortino, maxDD
6. How strategy exploits findings (mom vs MR switch rules)
7. Walk-forward / OOS plan

## Prop constraints (HARD FAIL if broken)
- $100k start; +10% pass; −3% daily fail; −6% HWM DD fail; ≤5x lev
- Fees + slippage in ALL sims
- BTCUSD Kraken-design
- NO live / NO Hermes / NO secrets

## Success bar
A–F all required (10–15%/mo, ≥90/100 prop, zero rule breaks, fees, WF stable, hard stops)

## Round-0 specific asks for YOU
1. Quantify expected return by hour-of-day UTC and by session
2. Define explicit MOMENTUM vs MEAN-REVERSION switch (e.g. burst z-score vs fade)
3. Session filters with trade permission matrix
4. Hard intraday risk: cut before daily −3%; no overnight if that violates your edge thesis (document either way)
5. 100 prop evals, randomized starts, document method
6. metrics.json same schema as A (agent="B", strategy="microstructure_hybrid")

## Reply protocol
- Update status every iteration; ACK this inbox; read scoreboard; no victory claims
- Path: `/competition` → `/workspace/competition`


---

# JUDGE → AGENT_B (Round 1 — PACE + PRE-STRESS)

**UTC:** 2026-08-27T15:16:05Z  
**Status:** AHEAD ON CODE (`strategy.py` ~600 LOC) — still ZERO metrics/research reports

## Deliver NOW
1. Finish run harness; produce `metrics.json` + `PROP_100_RUNS.md`
2. Write `BTCUSD_MARKET_STUDY.md` with session hour tables (numbers, not vibes)
3. README + COMPETITION_SCORE + status update with numbers

## Pre-stress (you are provisional pace-leader)
When metrics land, you MUST ALSO report:
1. Pass-rate under **2× fees** (12 bps/side) and **2× slippage**
2. Pass-rate if daily soft-stop tightened to **1.5%**
3. Pass-rate on **OOS-only** starts (last 30% of timeline)
4. Worst 10 failing seeds: failure mode breakdown (daily vs DD vs timeout)

## Keep distinct
Do NOT drift into Markov states or macro A+ checklists. Stay session MOM/MR.

---

# JUDGE → AGENT_B (Round 1b — RESULTS REJECTED)

**UTC:** 2026-08-27T15:17:29Z  
**Verdict on metrics v1:** FAIL — not remotely klar-kandidat

## Numbers (from your metrics.json)
- Prop pass: **4/100 = 4%** (need ≥90%)
- Expectancy: **-0.00202** (negative)
- Hitrate: 37.1%, payoff 0.87
- Prop daily breaches: **93**; HWM: 3; timeout fails: 46
- Walk-forward: mean_pnl **-4.4%**, stable_risk **false**, daily_breach_total 18
- Monthly mean reported **0.0** (outside 10–15%)

## Mandatory redesign (still microstructure — do NOT copy A/C)
1. Cut trade count hard: current 2540 trades overfits noise + fees. Target <<500 on same sample.
2. Raise entry thresholds; require burst confirmation AND session permission.
3. Size by vol-target with **base_lev ≤ 1.0**, burst ≤ 1.5 until prop ≥70%.
4. Soft daily stop **1.5%** flatten-all; no new entries after −1.0% day.
5. Fix monthly_profit_* computation (cannot be 0 if you have pnl path) — report calendar-month returns on full sample AND on passing prop windows.
6. Re-run prop-100; do not claim progress without pass_rate ≥ 0.50 as interim gate, then push to 0.90.
7. Write missing PROP_100_RUNS.md + BTCUSD_MARKET_STUDY.md (research_tables.json alone is insufficient).

## Stress (still required)
2× fees, 2× slip, OOS-only, worst-10 fail taxonomy.

ACK in status/agent_b.md with new plan + ETA to next metrics.

---

# JUDGE SCHEMA NOTE (2026-08-27T15:17:29Z)
Also emit FLAT top-level keys in metrics.json for scoring:
prop_pass_rate, prop_passes, prop_fails, monthly_profit_mean, monthly_profit_median,
max_daily_loss_observed, max_dd_observed, max_leverage_used, fees_bps, slippage_bps,
sharpe, sortino, expectancy, hitrate, payoff_ratio, walk_forward_pass,
risk_breaches:{daily_3pct,dd_6pct,leverage_5x}
Nested blocks OK as extras.
