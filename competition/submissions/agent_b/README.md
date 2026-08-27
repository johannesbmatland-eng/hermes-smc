# AGENT_B — Microstructure Hybrid (BTCUSD Prop Bot)

Intraday **momentum + mean-reversion hybrid** driven by Asia / London / NY session microstructure. Distinct from Agent A (Markov) and Agent C (macro flow).

## Quick start

```bash
cd /competition/submissions/agent_b
python3 -m kode.run_all
```

Outputs:
- `reports/metrics.json` (flat judge schema + extras)
- `reports/prop_100_runs.csv`
- `reports/walk_forward.csv`

## Strategy (locked)

1. **Causal features** (lag 1h): `mom12`, burst `z`, vol regime.  
2. **Session permission matrix:** London/NY → MOM; Asia → optional MR; Quiet → flat.  
3. **MOM:** London 08–09 and NY 16–17 continuation when lagged `|mom12|` exceeds threshold; hold 14–30h.  
4. **MR:** Asia fade of lagged bursts when vol contracts (disabled in current production params — weak after costs).  
5. **Risk:** soft daily stop, no new entries after −1% day, soft HWM flatten, hard fail −3%/−6%, lev ≤ 0.9 (≪ 5×).  
6. **Costs:** 3 bps fee + 3 bps slip / side (Kraken futures-tier design).

## Data

- Primary: Coinbase public BTC-USD 1h (`data/btcusd_hourly_public.csv`, 2023-01 → 2026-08)  
- Cross-check: Kraken daily OHLC retained under `data/`

## Latest headline metrics (see metrics.json)

- Prop pass: **24/100**
- Monthly mean: **~0.63%**
- Expectancy: **+8.7 bps/trade** after costs
- Max lev: **0.9**

## Docs

- `research/BTCUSD_MARKET_STUDY.md` — 7 required sections  
- `reports/PROP_100_RUNS.md` — prop method + fails + stress  
- `COMPETITION_SCORE.md` — self score vs rubric
