# SCOREBOARD — BTCUSD PROP-BOT COMPETITION

**Updated:** 2026-08-27T15:09:00Z (UTC)  
**Round:** 0 — INIT  
**Leader:** NONE  
**Klar-kandidat:** NONE  
**Final winner:** NOT DECLARED (user has not said STOPP)

## Live standings

| Rank | Agent | Strategy lock | Prop pass | Mo profit mean | Mo profit med | Risk OK | Research | Code | Score | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| — | AGENT_A | Markov / regime | — | — | — | — | — | — | 0.0 | AWAITING_SUBMISSION |
| — | AGENT_B | Microstructure hybrid | — | — | — | — | — | — | 0.0 | AWAITING_SUBMISSION |
| — | AGENT_C | Macro flow / breakout | — | — | — | — | — | — | 0.0 | AWAITING_SUBMISSION |

## Scoring formula
```
score = 0.30*prop_pass + 0.25*profit_fit + 0.20*risk + 0.15*research + 0.10*code
```
- `prop_pass`: min(pass_rate/0.90, 1.0) then scale to 100
- `profit_fit`: 100 if mean monthly ∈ [10%,15%]; taper outside; 0 if ≤0 or >25% (overfit flag)
- `risk`: 100 only if 0 daily-loss breaches AND 0 maxDD breaches AND leverage≤5x in ALL sims
- `research`: checklist completeness of BTCUSD_MARKET_STUDY.md (7 required sections)
- `code`: runnable entrypoint + hard risk engine + documented run

## Round-0 gate (must deliver before ranked)
1. `/competition/submissions/agent_x/research/BTCUSD_MARKET_STUDY.md`
2. `/competition/submissions/agent_x/reports/PROP_100_RUNS.md`
3. `/competition/submissions/agent_x/reports/metrics.json`
4. `/competition/submissions/agent_x/kode/` (runnable)
5. `/competition/submissions/agent_x/README.md`
6. `/competition/submissions/agent_x/COMPETITION_SCORE.md`
7. `/competition/status/agent_x.md` updated

## Judge ruling this round
- No agent has submitted. Score = 0 across board.
- Premature winner claims will be rejected.
- Competition continues until user STOPP to JUDGE.
