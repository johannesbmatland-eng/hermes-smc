# AGENT_C — Macro Flow / Volatility Breakout

Strict **A+ only** BTCUSD prop bot. Default = no trade. Event/flow/vol breakout on **4H** with false-break filter, volume surge, regime gates, and fee hurdle.

## Quick start

```bash
cd /competition/submissions/agent_c/kode
python3 run.py --prop-runs 100 --challenge-days 365 --seed 42
```

Outputs → `../reports/metrics.json`, `prop_100_raw.json`, `walk_forward.json`, `research_tables.json`.

## A+ checklist (≥4 independent filters; all must pass)

1. ATR expansion vs median (vol breakout)  
2. Donchian range break + EMA alignment  
3. False-break filter (close location + wick reclaim)  
4. Volume surge flow proxy  
5. Regime ER + EMA separation  
6. Weekday session gate  
7. Fee hurdle vs Kraken RT cost  

## Risk

- Hard: daily −3%, HWM −6%, leverage ≤5x  
- Soft (pre-fail): daily −1.8%, HWM −4.5%  
- Risk per A+ trade: 2% equity (size-down when DD≥2%)

## Layout

```
kode/run.py                 entrypoint
kode/agent_c/strategy_4h.py A+ event engine
kode/agent_c/backtest.py    fees/slip + exits
kode/agent_c/risk.py        hard/soft prop risk
kode/agent_c/prop_eval.py   WF + prop-100
research/BTCUSD_MARKET_STUDY.md
reports/metrics.json
reports/PROP_100_RUNS.md
```

## Honest metrics (v2)

See `reports/metrics.json`: prop **22/100**, E[R] **+0.36**, risk_ok **true**, trades/mo **~0.57**. Frequency math shows 10–15%/mo would need ~16 A+ trades/mo at this E[R]/risk — not claimed.
