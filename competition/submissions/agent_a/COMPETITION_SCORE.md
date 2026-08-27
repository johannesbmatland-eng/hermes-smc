# COMPETITION_SCORE — AGENT_A

**UTC:** 2026-08-27  
**Strategy:** markov_regime  
**Claim:** NOT klar-kandidat (targets unmet; metrics honest)

## Score components (self-assessment)

| Component | Weight | Self score | Notes |
|---|---:|---:|---|
| prop_pass | 0.30 | 5.6 | pass_rate 0.05 / 0.90 → min(0.056,1)*100 ≈ 5.6 |
| profit_fit | 0.25 | ~4 | mean monthly 0.61% ≪ [10%,15%] band |
| risk | 0.20 | **100** | 0 daily / 0 DD / lev≤5x breaches in all sims |
| research | 0.15 | **100** | 7/7 BTCUSD_MARKET_STUDY sections |
| code | 0.10 | **100** | runnable `python3 -m agent_a.kode.run_all` + hard risk engine |
| **TOTAL** | | **≈ 36** | 0.30*5.6 + 0.25*4 + 0.20*100 + 0.15*100 + 0.10*100 |

## Checklist
- [x] Markov states ≥4 with emissions
- [x] Transition matrix published
- [x] Edge E[r|s] after costs; trade positive states/transitions
- [x] Bayes posterior implemented
- [x] 100 prop sims randomized starts
- [x] Fees + slippage in all sims
- [x] Hard risk engine 3%/6%/5x
- [ ] Pass rate ≥ 90/100 — **NO (5/100)**
- [ ] Monthly profit 10–15% — **NO (~0.6%)**
- [ ] Walk-forward pass — **NO**

## Iteration note
Improve cycle #1: gap-aware stop fills, lowered leverage to **0.55x**, equity-mapped stops → **zero risk breaches**, pass rate remains low. Structural tension: safe leverage vs +10% in 180d on sparse SHOCK-recovery signals.
