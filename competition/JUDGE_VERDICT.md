# JUDGE_VERDICT
timestamp: 2026-08-27T14:58:59Z
competition: Trading Bot (Kraken design, paper/backtest only)

## WINNER: AGENT_C

## Final standings
| Rank | Agent | Outcome | Score | profit/mnd | winrate | maxDD | worstDay | trades | compliance |
|------|-------|---------|-------|------------|---------|-------|----------|--------|------------|
| **1** | **C** | **WIN** | **80.0** | +$1712.20 | 66.67% | 1.60% | -0.53% | 6 | ok |
| 2 | A | **FORFEIT** | 0.00 | — | — | — | — | — | — |
| 3 | B | **FORFEIT** | 0.00 | — | — | — | — | — | — |

## Why C wins
- Sole agent to `phase=submitted` with git-visible `competition/` artifacts (PR #11, `cursor/agent-c-arb-competition-56bf`).
- Prop-compliant: daily -0.53% ≤3%, maxDD 1.60% ≤6%, lev≤5x enforced in `risk.py`.
- Positive expectancy with fees (16bps) + slippage (4bps) modeled.
- Highest scored package under published weights.

## Score breakdown — AGENT_C
| Criterion | Weight | Award | Evidence |
|-----------|--------|-------|----------|
| compliance | 30 | 28 | Hard halt on daily/DD; lev gate; observed ok |
| profit/mnd | 25 | 20 | +$1712.20 (0.89m window annualized) |
| winrate/expectancy | 20 | 17 | 66.67% WR · +$317.06/trade |
| robustness | 15 | 6 | Thin sample n=6 / 0.89 months |
| code/runnable | 10 | 9 | README, tests, results JSON, runnable entrypoints |
| **TOTAL** | **100** | **80** | |

## Forfeits
- **AGENT_A**: no git push of `competition/status` or `submissions` after check-ins #1–#6 + final warning at #5. Local work existed (transcript) but unscorable.
- **AGENT_B**: same — promised branch never appeared on remote; repeated no-show of scoreable artifacts.

## Disqualifications
none (forfeit ≠ DQ; no live/Hermes/secrets observed on scored submission)

## Disposition
- Winner **AGENT_C** advances (Hermes/live only after this competition, per rules — JUDGE does not enable Hermes here).
- AGENT_A and AGENT_B eliminated.

## Judge
Strict scoring on git-visible evidence only. Verdict final at check-in #6.
