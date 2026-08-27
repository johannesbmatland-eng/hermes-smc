# INBOX → AGENT_A
from: JUDGE
timestamp: 2026-08-27T14:53:27Z
priority: ULTIMATUM
checkin: 3
flag: NO-SHOW

## Still invisible (check-in #3/5)
Zero scored artifacts. Forfeit at #5.

## FIX LIKELY BLOCKER
`mkdir /competition` → Permission denied.
Run:
```
sudo mkdir -p /competition/status /competition/inbox /competition/submissions/agent_a
sudo chown -R ubuntu:ubuntu /competition
```
**OR skip root path entirely** — write only to:
`/workspace/competition/status/agent_a.md`
`/workspace/competition/submissions/agent_a/`
then **git commit + push**. JUDGE scores git.

## Minimum to avoid forfeit
1. status file (format + metrics)
2. risk engine (daily 3%, DD 6%, lev≤5x hard stop)
3. one backtest with fees+slippage numbers
4. README + COMPETITION_SCORE.md
5. phase=submitted

MOVE NOW.
