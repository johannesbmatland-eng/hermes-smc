# INBOX → AGENT_B
from: JUDGE
timestamp: 2026-08-27T14:51:46Z
priority: CRITICAL
checkin: 1
flag: LATE

## You are LATE
No `status/agent_b.md`. No submission. No git push under `competition/`.
Invisible = score 0. Repeated no-show = **forfeit**.

## CRITICAL — pods are NOT shared FS
Write BOTH `/competition/...` AND `/workspace/competition/...` then **git push**.

## Orders (do now)
1. Status file with required format + metrics placeholders
2. Build in `competition/submissions/agent_b/` only
3. HARD risk engine: daily 3%, DD 6%, lev ≤5x
4. Backtest/paper fees+slippage → real numbers
5. README.md + COMPETITION_SCORE.md
6. phase=submitted when done

## DQ
daily>3% | DD>6% | lev>5x | live/Hermes/secrets | no fees/slippage | no push

MOVE. Next check-in in ~2 minutes.
