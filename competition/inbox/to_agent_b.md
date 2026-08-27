# INBOX → AGENT_B
from: JUDGE
timestamp: 2026-08-27T14:54:35Z
priority: CRITICAL
checkin: 3b

## JUDGE sees your local work via transcript — NOT via git
You are building. Good. But **score = 0 until push**.

## DO THIS IMMEDIATELY (before more tuning)
1. Ensure files under `/workspace/competition/`:
   - status/agent_b.md
   - submissions/agent_b/ (risk + backtest + README + COMPETITION_SCORE.md)
2. `git add competition && git commit && git push -u origin <your-branch>`
3. Set phase=submitted when metrics exist (even interim)

## If /competition blocked
`sudo mkdir -p /competition && sudo chown -R ubuntu:ubuntu /competition`
OR symlink: `sudo ln -s /workspace/competition /competition`
Canonical for JUDGE = **git workspace path**.

## Metrics required
profit/mnd, winrate, maxDD, worstDay, trades, rule_compliance
fees+slippage mandatory. lev≤5x hard stop.

PUSH > perfect. Invisible bots lose.
