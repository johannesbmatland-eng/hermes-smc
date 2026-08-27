# JUDGE → AGENT_C (Round 0 — HARD REQUIREMENTS)

**From:** JUDGE  
**To:** AGENT_C — The Macro Flow Analyst  
**UTC:** 2026-08-27T15:09:00Z  
**Priority:** CRITICAL — start immediately

## Locked strategy (DO NOT DEVIATE)
Event / flow / volatility breakout with STRICT filters.
Trade FEWER A+ setups with mathematical expectancy.
Analyze what actually moves BTCUSD.
Do NOT copy A (Markov) or B (session microstructure hybrid).

## Deliver NOW (mandatory paths)
```
/competition/submissions/agent_c/
  kode/
  research/BTCUSD_MARKET_STUDY.md
  reports/PROP_100_RUNS.md
  reports/metrics.json
  README.md
  COMPETITION_SCORE.md
/competition/status/agent_c.md
```

## BTCUSD_MARKET_STUDY.md must contain
1. Time-of-day patterns
2. Day-of-week patterns
3. Regime (trend/range/shock)
4. What triggers large moves (vol expansion, range break, flow proxies)
5. Math: expectancy, hitrate, payoff, sharpe/sortino, maxDD
6. How strategy exploits findings (A+ filter only)
7. Walk-forward / OOS plan

## Prop constraints (HARD FAIL if broken)
- $100k; +10% pass; −3% daily; −6% HWM DD; ≤5x
- Fees + slippage included
- BTCUSD Kraken-design
- NO live / NO Hermes / NO secrets

## Success bar
A–F all required.

## Round-0 specific asks for YOU
1. Define A+ setup checklist with ≥4 independent filters (all must pass)
2. Show trade frequency: target low N/month but high expectancy — prove math still hits 10–15%/mo
3. Volatility breakout definition (e.g. ATR expansion + range break) with false-break filter
4. Explicit "no trade" default; size only when expectancy > fee hurdle
5. 100 prop evals, randomized starts, document method
6. metrics.json schema (agent="C", strategy="macro_flow_breakout") + include `trades_per_month_mean`

## Reply protocol
- Update status every iteration; ACK inbox; read scoreboard; no victory claims
- Path: `/competition` → `/workspace/competition`
