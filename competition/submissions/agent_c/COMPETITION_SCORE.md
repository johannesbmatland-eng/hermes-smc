# COMPETITION_SCORE — AGENT_C

**Updated:** 2026-08-27T15:55:00Z  
**Claim:** None (no victory declaration)

## Scoreboard inputs (from `reports/metrics.json`)

| Component | Input | Component score |
|-----------|------:|----------------:|
| prop_pass | 22/100 = 0.22 → min(0.22/0.90,1)×100 | **24.44** |
| profit_fit | monthly mean 0.436% (target 10–15%) | **4.36** |
| risk | risk_ok=true (0/0/0 breaches) | **100** |
| research | 7/7 study sections | **100** |
| code | runnable `run.py` + risk engine | **100** |

```
score = 0.30*24.44 + 0.25*4.36 + 0.20*100 + 0.15*100 + 0.10*100
      ≈ 7.33 + 1.09 + 20 + 15 + 10
      ≈ 53.42
```

## vs Round-6b reject

| | v1 (rejected) | v2 (this) |
|--|--------------:|----------:|
| prop | 0% | **22%** |
| E[R] | −0.57 | **+0.36** |
| risk_ok | contested | **true** |
| tpm | ~0.08 | ~0.57 |

## Gaps (honest)

- Monthly mean ≪ 10–15% because A+ tpm ≪ ~16.5 required by frequency math.  
- Not klar-kandidat; competition continues until user STOPP.
