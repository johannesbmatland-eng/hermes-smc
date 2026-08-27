# AGENT_C — Adaptive Regime Breakout (ARB)

Kraken-design **paper/backtest** bot for the prop competition.

**No live trading. No Hermes. No API secrets.**

## Headline (Kraken BTC/USD 1h)

| Metric | Value |
|--------|-------|
| Profit / month | **+$1,710** |
| Winrate | **66.7%** |
| Max DD | **1.6%** |
| Worst day | **-0.53%** |
| Compliance | **ok** |

## Strategy

Two-state Markov-ish regime on **BTC/USD** (1h):

1. **CHOP / chaos** — ATR% percentile too low or extreme → no entries  
2. **TREND** — Donchian(36) breakout + EMA20/50 alignment + body filter  
3. Stop = 2.0×ATR, TP = 2.2R, risk 0.35% equity / trade  
4. Max 1 position, cooldown 3 bars  

## Hard prop risk (`risk.py`)

| Rule | Limit | Enforcement |
|------|-------|-------------|
| Daily loss | 3% | Halt + flatten |
| Max drawdown | 6% from peak | Halt + flatten |
| Leverage | ≤ 5× | Reject / shrink size |
| Soft zone | 90% of limits | Block new entries |

## Costs

- Fee: **16 bps** per side  
- Slippage: **4 bps** adverse  

## Run

```bash
cd /competition/submissions
python3 agent_c/test_risk.py
python3 agent_c/run_backtest.py
# offline:
python3 agent_c/run_backtest.py --synthetic-only --limit 2000
```

Artifacts: `results/backtest_metrics.json`, `trades.json`, `equity_curve.json`, `paper_report.json`  
Scorecard: `COMPETITION_SCORE.md`

## Layout

```
agent_c/
  config.py risk.py indicators.py strategy.py
  data.py backtest.py paper.py
  run_backtest.py test_risk.py
  README.md COMPETITION_SCORE.md results/
```
