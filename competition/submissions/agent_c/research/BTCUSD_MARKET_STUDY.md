# BTCUSD Market Study — AGENT_C (Macro Flow / Vol Breakout)

**Agent:** C  
**Strategy:** `macro_flow_breakout`  
**Data:** BTCUSDT/BTC-USD hourly research series (2023-01 → 2026-08), resampled to **4H** for signals; Kraken-futures-design costs (5 bps fee + 3 bps slip / side).  
**Updated:** 2026-08-27

---

## 1. Time-of-day patterns

Hourly |fwd 24h abs return| is relatively flat across UTC hours (means ≈ 1.64–1.71%). Slightly elevated overnight (00–04 UTC) vs mid-London. Macro breakouts are not hour-alpha driven on this sample; session gating is used only as a **weekend / thin-flow** control (weekdays only), not a microstructure clock like Agent B.

Implication for A+: do **not** overfit TOD buckets; require vol/flow/regime confirmation instead.

---

## 2. Day-of-week patterns

Weekend liquidity is thinner and false breaks more common. A+ checklist **blocks Sat/Sun** entries. Weekday flow (Mon–Fri) carries the measured expansion moves used by the 4H engine.

---

## 3. Regime (trend / range / shock)

Kaufman efficiency ratio (ER) on the break lookback separates chop vs trend:

| ER bucket | Behavior | Strategy stance |
|-----------|----------|-----------------|
| chop (low ER) | mean-revert noise | **no trade** |
| mid | mixed | trade only if ATR expand + volume surge |
| trend (high ER) | directional persistence | preferred for break continuation |

Additional regime filters: EMA(20) vs EMA(50) alignment with break side, and minimum EMA separation (`ema_sep_min=0.002`) to avoid flat ribbon chop. Shock gate skips pathological 4H gaps.

---

## 4. What triggers large moves (vol expansion, range break, flow)

Empirical hourly trigger table (24h forward abs return uplift vs baseline):

- **ATR expansion** (atr/median ≥ ~1.25): elevated forward range  
- **Volume surge**: co-occurs with expansion days  
- **Near Donchian edge**: range breaks cluster ahead of large moves  
- Combined **atr+vol+edge**: strongest unconditional association

**Volatility breakout definition (locked):**  
`ATR(14)/median_ATR(40) ≥ 1.10` **AND** close clears Donchian(12 or 20) in EMA-trend direction.

**False-break filter:**  
On 4H Donchian(20), raw breaks ≈ 711; reclaim within 3 bars ≈ 42.5% (**hold_rate ≈ 57.5%**). A+ requires close-location ≥ 0.55 (long) / ≤ 0.45 (short) and wick not reclaiming beyond `0.50·ATR` inside the broken range.

**Flow proxy:** volume / SMA(30) ≥ 1.30 at the break bar.

---

## 5. Math: expectancy, hitrate, payoff, sharpe/sortino, maxDD

Full-sample 4H sequential backtest (fees+slippage included, one position):

| Metric | Value |
|--------|------:|
| Trades | 33 |
| Hit rate | 66.7% |
| Payoff (avg win / avg loss) | 1.05 |
| Expectancy E[R] | **+0.363** |
| Expectancy USD / trade | +$598 |
| Sharpe (4H ann.) | 0.86 |
| Sortino | 0.34 |
| Max DD | 7.1% (research path; prop soft-stops earlier) |
| Trades / month | 0.75 |
| Monthly geo return | ~0.41% |

Walk-forward (6m train / 2m test, step 2m, 18 folds): OOS mean E[R] ≈ **+0.54**, OOS monthly geo mean ≈ **+0.68%**, walk_forward_pass=true.

### Frequency math (10–15%/mo proof)

```
monthly ≈ trades_per_month × risk_frac × E[R]
```

With E[R]=0.363 and risk_frac=0.02:  
`required_tpm for 12%/mo = 0.12 / (0.02×0.363) ≈ 16.5 trades/month`.

Empirical A+ tpm ≈ **0.57–0.75**. Expected monthly ≈ `0.75×0.02×0.363 ≈ 0.54%` — **below** the 10–15% target. Hitting 10–15% under ≤2% risk/trade and daily −3% / HWM −6% would require either (a) ~16 A+/month with the same E[R] (destroys A+ rarity) or (b) risk sizes incompatible with prop hard fails. Agent C prioritizes **positive expectancy + risk_ok** over forcing frequency.

---

## 6. How strategy exploits findings (A+ only)

**Default = no trade.** All of the following must pass:

1. ATR expansion ≥ 1.10 (vol breakout regime)  
2. Donchian range break with EMA trend alignment  
3. False-break filter (close location + wick reclaim limit)  
4. Volume surge flow proxy ≥ 1.30  
5. Regime: ER ≥ 0.15 and EMA separation  
6. Weekday-only session gate  
7. Fee hurdle: E[R]·stop_frac ≥ 1.0 × Kraken RT cost  

Entry: next 4H open after signal. Exit: 2·ATR stop, trail 1·ATR after +1R, time stop 30 bars. Soft prop stops at −1.8% day / −4.5% HWM before hard −3% / −6%.

---

## 7. Walk-forward / OOS plan

1. **Anchored rolling WF:** 6-month train → fit hit-rate / R priors for fee hurdle only (structure frozen) → 2-month OOS test; step 2 months.  
2. **Prop Monte Carlo:** 100 randomized starts, **365 calendar-day** windows (matched to A+ low frequency), seed=42, soft then hard risk.  
3. **Pass criteria:** +10% equity, zero daily −3%, zero HWM −6%, leverage ≤5x.  
4. **Promotion rule:** freeze params only if OOS mean E[R] > 0 and prop risk_ok; do not chase 10–15% monthly by loosening A+ into negative expectancy.
