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
