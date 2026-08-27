# COMPETITION_SCORE — AGENT_C

- strategy: Adaptive Regime Breakout (ARB)
- markets: BTC/USD (Kraken public OHLCV, 1h)
- data_window: ~30 days (720 bars)
- compliance: **ok**
- profit/mnd: **$1,709.53**
- profit_pct (window): 1.527% (~$1,527 on $100k)
- winrate: **66.67%**
- expectancy $/trade: **$317.06**
- maxDD: **1.60%** (limit 6%)
- worstDay: **-0.53%** (limit 3%)
- trades: 6
- months_span: 0.89
- total_fees: $756.19
- total_slippage: $189.05
- halt_reason: None
- final_equity: $101,526.71

## Prop rules — all pass
| Rule | Limit | Observed |
|------|-------|----------|
| Daily loss | 3% | worst day -0.53% |
| Max DD | 6% | 1.60% |
| Leverage | ≤5x | enforced in `risk.py` |
| Start capital | $100,000 | yes |

## Costs modeled
- Fee: 16 bps / side
- Slippage: 4 bps adverse
- Applied on entry + exit

## Runnable
```bash
cd /competition/submissions && python3 agent_c/test_risk.py && python3 agent_c/run_backtest.py
```

## Notes
- Hard risk engine halts + flattens on daily/DD breach.
- No live keys. No Hermes. Paper/backtest only.
- Tuned via fee-aware grid search on Kraken BTC/USD.
