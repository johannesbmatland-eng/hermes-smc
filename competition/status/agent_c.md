# AGENT_C Status

- timestamp: 2026-08-27T14:59:30Z
- phase: submitted
- strategy: Adaptive Regime Breakout (ARB) — 2-state Markov-ish (trend/chop) + Donchian/ATR breakout
- markets: BTC/USD (Kraken public 1h OHLCV)
- progress_pct: 100
- blockers: none (peers empty; no judge inbox yet)

## metrics
- profit/mnd: 1712.20
- winrate: 66.67
- maxDD: 1.60
- worstDay: -0.53
- trades: 6

## rule_compliance
ok

## next_step
Hold submitted; re-check peers/judge. Robustness search (15m/looser 1h) did not beat baseline — keep current params.

## asks_for_judge
Please init scoreboard + inbox requirements. Submission ready at `/competition/submissions/agent_c/`.
