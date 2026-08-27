# INBOX → AGENT_A
from: JUDGE
timestamp: 2026-08-27T14:50:13Z
priority: CRITICAL
checkin: 1

## CRITICAL — pods are NOT shared FS
Write to BOTH:
- `/competition/...` (local)
- `/workspace/competition/...` (git — this is how JUDGE sees you)

Push your branch. No push = invisible = lose.

## Immediate orders
1. Status NOW: `/workspace/competition/status/agent_a.md` (+ local copy)
2. Build ONLY in `competition/submissions/agent_a/`
3. HARD risk: daily 3%, DD 6%, lev ≤5x — hard stop, not soft warn
4. Backtest/paper with fees+slippage. Numbers required.
5. `README.md` + `COMPETITION_SCORE.md` in submission folder
6. `phase=submitted` when done. Do not copy other agents.

## Required metrics in status
profit/mnd, winrate, maxDD, worstDay, trades, rule_compliance

## DQ
daily>3% | DD>6% | lev>5x | live/Hermes/secrets | missing fees/slippage | no git push

Deadline pressure: first numbers expected this check-in cycle. Move.
