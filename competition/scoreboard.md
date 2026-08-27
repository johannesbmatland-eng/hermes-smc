# SCOREBOARD — Trading Bot Competition
updated: 2026-08-27T14:57:20Z
judge: JUDGE
status: OPEN — CHECK-IN #4c — **AGENT_C OFFICIALLY SUBMITTED**

## Prop rules
$100k · +10% · daily≤3% · DD≤6% · lev≤5x · Kraken · no live/Hermes

## Scoring weights
compliance 30% · profit/mnd 25% · winrate/expectancy 20% · robustness 15% · code/runnable 10%

## Official standings (git-scored)
| Rank | Agent | Phase | compliance | profit/mnd | winrate | maxDD | worstDay | trades | Score | Why |
|------|-------|-------|------------|------------|---------|-------|----------|--------|-------|-----|
| **1** | **C** | **submitted** | ok | **+$1709.53** | **66.67%** | **1.60%** | **-0.53%** | 6 | **80.0** | only official push; fees+slip; hard risk |
| — | A | building* | — | — | — | — | — | — | **0.00** | no git push |
| — | B | building* | — | — | — | — | — | — | **0.00** | no git push |

\*Transcript-only (not scored): A had early DD fail then later ok/neg PnL; B mixed DD risk / neg edge.

## Score breakdown — AGENT_C (official)
| Criterion | /max | Award | Note |
|-----------|------|-------|------|
| compliance | 30 | 28 | hard daily/DD/lev in risk.py; observed ok |
| profit/mnd | 25 | 20 | +$1709; annualized from 0.89m window |
| winrate/exp | 20 | 17 | 66.67% / +$317 exp |
| robustness | 15 | 6 | **n=6 / 0.89m — thin** |
| code/runnable | 10 | 9 | README, tests, results JSON, PR #11 |
| **TOTAL** | **100** | **80** | |

## Leader
**AGENT_C** — sole scored submission. Branch `cursor/agent-c-arb-competition-56bf` PR #11.

## Fail flags
- A/B: still unscored — push or forfeit by check-in #6
- C caveat: robustness haircut until longer window; not DQ

## Disqualifications
none
