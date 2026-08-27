# BTCUSD Market Study — AGENT_B Microstructure Hybrid

**UTC:** 2026-08-27  
**Data:** Coinbase Exchange public BTC-USD 1h candles, 2023-01-01 → 2026-08-27 (~32,019 bars).  
**Costs (Kraken-design):** 3 bps fee + 3 bps slip per side (6 bps/side, 12 bps RT). Futures-tier blended (maker/taker mix), not raw Kraken spot taker 26 bps (which kills all hourly edges).  
**Signal rule:** all features lagged 1 bar (causal; no same-bar look-ahead).

---

## 1. Time-of-day / session buckets (UTC)

| Session | Hours (UTC) | Mean ret (bps/h) | Ann. hour-sharpe (approx) | Notes |
|---|---|---|---|---|
| Asia | 00–07 | +0.26 | ~0.56 | Mild positive drift; **negative lag-1 autocorr** → MR-friendly |
| London | 07–12 | +0.89 | ~2.14 | Best session drift |
| Overlap | 12–16 | +0.09 | ~0.13 | Highest realized vol |
| NY | 16–21 | +0.58 | ~0.98 | Continuation of Western impulse |
| Quiet | 21–00 | +1.77 | ~3.55 | Thin liquidity; strategy mostly flat |

**Hour expectancy (mean return, bps)** — strongest: 22, 17, 21, 8, 11. Weakest: 13, 23, 19, 16, 1.  
Strategy **skips** hours `{1, 13, 19, 23}`.

**Session permission matrix**

| Session | Allowed mode | Rationale |
|---|---|---|
| Asia | MR | Negative autocorr; fade bursts when vol contracts |
| London | MOM | Causal 12h thrust continuation (core edge) |
| Overlap | MOM | Optional; weaker after lag fix |
| NY | MOM | Causal 12h continuation at 16–17 |
| Quiet | NONE | Avoid thin-book noise |

---

## 2. Day-of-week

| Dow (Mon=0) | Mean ret (bps/h) |
|---|---|
| Mon | +1.70 |
| Tue | +0.13 |
| Wed | +1.81 |
| Thu | −1.25 |
| Fri | +0.94 |
| Sat | +0.14 |
| Sun | +0.84 |

Thursday is the weakest day historically. Current book does **not** hard-skip Thursday (edge is session-conditional, not calendar-primary), but Wednesday Overlap showed elevated continuation in research grids.

---

## 3. Regime (trend / range / shock)

Regime from 24h vol z-score vs 90d rolling baseline:

| Regime | Behavior |
|---|---|
| **Range** (vol z < −0.45) | Asia MR preferred; Western MOM down-weighted / blocked on Overlap |
| **Trend** (mid vol) | London/NY continuation strongest |
| **Shock** (vol z > 1.2) | Size ×0.55; prefer not to chase Asia fades |

Session × regime (mean ret, bps): shock×London/Overlap/Quiet positive; Asia mild; NY mixed. Strategy uses regime as a **size and permission modifier**, not a Markov state machine (distinct from Agent A).

---

## 4. Large-move triggers (session open bursts)

| Open | Hour | Open |absret| / session avg |
|---|---|---|
| Asia | 00 | ~1.15× |
| London | 07 | ~1.02× |
| Overlap | 12 | ~0.70× (vol peaks mid-overlap, not exactly at open) |
| NY | 16 | ~1.02× |

**Causal edges after fees (12 bps RT, lagged mom12):**
- London hours 8–9, |mom12|≥1.0%, hold 30h: **hit ~53.7%, E ≈ +28.3 bps**, total +1.11 over sample
- NY hours 16–17, |mom12|≥1.6%, hold 14h: **E ≈ +13–20 bps**
- Same-bar (non-causal) signals inflated Overlap; **lagged Overlap is weak/negative** → disabled in production book

Asia burst fade is economically weak after costs once lagged; kept optional (`use_asia_mr=False` by default) for hybrid completeness.

---

## 5. Math (strategy, full sample, after costs)

Config: Lon/NY only, lev≈1.15–1.25, soft daily 2.2%, soft HWM 5.2%, no micro trade-stop.

| Metric | Value (illustrative from latest locked run) |
|---|---|
| Trades | ~680 |
| Hitrate | ~0.47 |
| Expectancy / trade | ~+10 bps (account, after costs) |
| Payoff | ~1.0–1.2 |
| Sharpe (hourly→ann) | ~0.6 |
| Sortino | reported in metrics.json |
| Max DD (path) | elevated vs prop 6% — soft stops + ratchet in research mode |
| Monthly mean | ~+1.0% (honest; **below** 10–15% target) |

**Prop economics:** at ~1%/mo and ≤1.25× leverage, P(+10% before −3% daily/−6% DD) over 60–90 days is structurally low. Hitting ≥90/100 requires either a much larger edge or leverage that violates daily loss with BTC hourly vol.

---

## 6. How the strategy exploits findings (MOM vs MR switch)

```
IF session ∈ {London, NY} AND lagged |mom12| ≥ thr AND hour in permission set:
    MODE = MOMENTUM; direction = sign(mom12); hold = 14–30h
ELIF session == Asia AND lagged |z| ≥ asia_z AND vol_ratio ≤ 0.95:
    MODE = MEAN_REVERSION; direction = −sign(z); hold = 8h   # optional
ELSE:
    flat
```

- **MOM** rides Western session continuation / open bursts.  
- **MR** fades Asia microstructure bursts only when vol is contracting (range).  
- **Shock** cuts size. **Skip hours** remove known negative expectancy buckets.  
- Overnight: Lon holds **can** span into NY (edge is multi-hour continuation). Flat in Quiet. Documented choice: overnight allowed when MOM hold requires it; Asia MR does not hold into London open.

---

## 7. Walk-forward / OOS plan

1. **Expanding / rolling WF:** train 180d → test 60d, step 60d across 2023–2026.  
2. **OOS holdout:** last 20% of timeline scored separately.  
3. **Prop-100:** randomized start indices, seed=42, challenge window 90d (also stress 60/120). Pass = +10% before daily −3% or HWM −6%.  
4. **Stress:** 2× fees, 2× slip, daily soft-stop 1.5%, OOS-only starts (last 30%).  
5. **Promotion rule:** no parameter change without WF mean expectancy ≥ 0 and prop interim ≥ 50%.

---

## Honest limitation

Causal microstructure edges after realistic costs are **real but small** (~ tens of bps per trade). They do **not** currently support 10–15%/month and ≥90% prop pass simultaneously under 3%/6%/5× constraints. Iteration continues: raise E, cut DD breaches, then scale size.
