# JUDGE STATUS
timestamp: 2026-08-27T14:50:13Z
phase: checkin_1
round: 1

## State
- Structure live at `/competition` AND mirrored to `/workspace/competition` (git sync)
- Sibling agents RUNNING but on separate pods — local `/competition` is NOT shared FS
- Coordination channel: **git** (`competition/` in repo) + agent status files agents must push
- Scoreboard initialized; first orders issued
- Timer `judge-checkin` every 120s

## Sibling map
| Role | Agent name | bcId | Status |
|------|------------|------|--------|
| JUDGE | Konkurranse dommar rolle | bc-01a043b0-b6ef-7f09-bf8c-9f25d4ea7690 | RUNNING (self) |
| A | Agent A bot innsending | bc-01a043b0-fc8b-7db6-b65a-7269e191a000 | RUNNING |
| B | Agent b trading bot | bc-01a043b1-3a93-7cf0-94a2-7e8a27178b52 | RUNNING |
| C | Trading bot konkurranse | bc-01a043b1-70a9-74ee-b579-112e180456bf | RUNNING |

## Standings
| Agent | Phase | Compliance | Score | Notes |
|-------|-------|------------|-------|-------|
| A | missing | — | 0 | no status on disk / no push yet |
| B | missing | — | 0 | no status on disk / no push yet |
| C | missing | — | 0 | no status on disk / no push yet |

## Fail flags
- COORD: separate pods — agents MUST dual-write `/competition` + `/workspace/competition` and push

## Next
1. Push judge scaffold to git
2. Pressure A/B/C via inbox: status + risk + backtest numbers + git push
3. Pull remote branches / PR diffs for submissions
4. Verdict only after all submitted or forfeit
