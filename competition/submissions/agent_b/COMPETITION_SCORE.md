# COMPETITION_SCORE — AGENT_B

**UTC:** 2026-08-27T15:58:16Z  
**Strategy:** `microstructure_hybrid`  
**Klar-kandidat:** NO

## Rubric self-score

| Component | Weight | Score | Notes |
|---|---|---|---|
| Prop pass-rate | 30% | 26.7 | 24/100 → min(0.24/0.90,1)*100 = 26.7 |
| Profit fit 10–15%/mo | 25% | 6.3 | mean mo 0.63% → 100*(0.0063/0.10) |
| Risk compliance | 20% | 0 | prop daily breaches 16; HWM 7; full-sample breaches too |
| Research/math | 15% | 100 | BTCUSD_MARKET_STUDY.md 7/7 sections |
| Code/runnable | 10% | 100 | `python3 -m kode.run_all` |
| **Total** | 100% | **~34.6** | |

## Success criteria A–F

| ID | Criterion | Status |
|---|---|---|
| A | 10–15%/mo after costs | FAIL (0.63%) |
| B | ≥90/100 prop | FAIL (24%) |
| C | Zero rule breaks | FAIL |
| D | Fees+slippage included | PASS |
| E | Walk-forward stable | FAIL (stable_risk false; mean E>0 but breaches) |
| F | Hard stops wired | PASS (engine enforces 3%/6%/5×) |

## Gap analysis

- Edge is **positive but small** after causal lag + costs.  
- Timeout dominates prop fails (53/76) — not enough drift to +10% in 90d at lev≤0.9.  
- Raising leverage recovers speed but reintroduces daily/−6% fails (observed in prior iterations).  
- Path to ≥50% interim: lift trade E (filter quality) and/or extend effective challenge edge without breaching soft daily 1.5–2.5%.

## No victory claim

Scoreboard leadership on raw score ≠ klar-kandidat. Waiting for user STOPP via JUDGE.
