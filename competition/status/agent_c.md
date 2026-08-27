# AGENT_C STATUS

**Role:** AGENT_C — The Macro Flow Analyst  
**Strategy lock:** 4H event/flow/vol breakout (STRICT A+)  
**Updated:** 2026-08-27T15:55:00Z  
**State:** SUBMITTED_V2 — awaiting judge rescore

## ACK
- Rounds 0–6b received. v1 (0/100, E≪0) rejected — fixed.

## Metrics v2 (honest)
- prop_pass_rate: **0.22** (22/100, 365d windows, seed=42)
- monthly_profit_mean: **0.00436** (0.44%/mo geo)
- trades_per_month_mean: **0.57**
- expectancy E[R]: **+0.363**
- hitrate: **0.667**
- risk_ok: **true** (0 daily / 0 DD / 0 lev breaches)
- score estimate: **~53.4**

## Deliverables
- research/BTCUSD_MARKET_STUDY.md (7 sections)
- reports/PROP_100_RUNS.md + metrics.json
- kode/run.py runnable
- README.md + COMPETITION_SCORE.md

## Notes
Frequency math documented: 10–15%/mo needs ~16.5 tpm at E[R]=0.36 & 2% risk — A+ rarity does not claim that. Interim gate (≥20% prop + E>0 + risk_ok) met. No victory claim.
