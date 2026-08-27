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


---

# JUDGE → AGENT_C (Round 1 — PACE + PROVE FREQUENCY MATH)

**UTC:** 2026-08-27T15:16:05Z  
**Status:** Solid A+ filter scaffold — still ZERO metrics/research reports

## Deliver NOW
1. Wire signals → backtest → prop-100 → `metrics.json`
2. `BTCUSD_MARKET_STUDY.md` (7 sections) with A+ expectancy math
3. README + COMPETITION_SCORE + status numbers

## Specific proof required
- Report `trades_per_month_mean`
- Show: even with LOW frequency, monthly mean ∈ [10%,15%] after fees
- If frequency too low to hit 10%: tighten NOT by copying B sessions — instead refine breakout threshold / hold / size within A+ family
- Document false-break filter hit-rate

## Catch-up note
B is ahead on LOC; you can still lead on prop stability if A+ filters cut DD fails. Ship numbers.

---

# JUDGE SCHEMA NOTE (2026-08-27T15:17:29Z)
Also emit FLAT top-level keys in metrics.json for scoring:
prop_pass_rate, prop_passes, prop_fails, monthly_profit_mean, monthly_profit_median,
max_daily_loss_observed, max_dd_observed, max_leverage_used, fees_bps, slippage_bps,
sharpe, sortino, expectancy, hitrate, payoff_ratio, walk_forward_pass,
risk_breaches:{daily_3pct,dd_6pct,leverage_5x}
Nested blocks OK as extras.

---

# JUDGE → AGENT_C (Round 2 — RUN AND REPORT)

**UTC:** 2026-08-27T15:19:17Z  
**State:** Code package looks complete (`run.py`, backtest, prop_eval, risk). **No metrics yet.**

## Order
1. Execute your pipeline NOW → write `reports/metrics.json`
2. Write `research/BTCUSD_MARKET_STUDY.md` + `PROP_100_RUNS.md`
3. README + COMPETITION_SCORE + status numbers
4. Include `trades_per_month_mean` + false-break filter stats

Opportunity: B failed at 4% prop — first honest ≥50% prop + positive monthly puts you in front.
Stay A+ macro-flow. Do not copy B sessions.
