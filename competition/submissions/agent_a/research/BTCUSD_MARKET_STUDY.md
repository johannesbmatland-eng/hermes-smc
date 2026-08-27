# BTCUSD Market Study — AGENT_A (Markov Quant)

**Data source:** Coinbase Exchange public BTC-USD hourly OHLCV, 2020-01-01 → 2026-08-27 (58,305 bars).  
Kraken public OHLC is capped at ~720 bars; Coinbase used as BTCUSD proxy. Costs modeled Kraken-design: **8 bps fee + 3 bps slippage per side** (22 bps round-trip).

---

## 1. Time-of-day patterns

Hourly mean returns (UTC) show mild structure:

| Session (UTC) | Observation |
|---|---|
| 15–22 | Slightly positive mean hour returns (EU afternoon / US cash overlap) |
| 23–04 | Weak / negative mean returns (Asia early / late US) |
| 12–20 | Preferred entry window for SHOCK-recovery trades (liquidity + mean-reversion after dumps) |

Large absolute moves (≥99.5th percentile \|r\|) cluster near **12–15 UTC** and **20 UTC** (overlap / US open / evening). Strategy therefore prefers `PREFERRED_HOURS = {12…20}` for capitulation entries.

---

## 2. Day-of-week patterns

| Dow (Mon=0) | Mean hourly ret | Note |
|---|---|---|
| Mon | +1.8e-4 | Mild positive open bias |
| Tue | ~flat | |
| Wed | +1.9e-4 | |
| Thu | −8.7e-5 | Weakest |
| Fri–Sun | mild positive | Lower weekend volume, noisier |

Monday / mid-week drifts are **second-order**. Primary edge is regime/transition-based, not calendar alpha. Calendar used only as a soft filter (avoid off-hours shock entries unless dump is deep).

---

## 3. Regime (trend / range / shock) → Markov states

Four discrete states (hard labels from causal features, then Bayes posterior):

| State | Emission features | Economic meaning |
|---|---|---|
| `TREND_UP` | 24h return ≥ +1.2%, vol not extreme | Persistent upside drift |
| `TREND_DOWN` | 24h return ≤ −1.2%, vol not extreme | Persistent downside |
| `RANGE` | \|24h ret\| ≤ 0.6%, moderate vol | Mean-reverting chop |
| `SHOCK` | vol z ≥ 2.0 **or** \|1h ret\| ≥ 1.8% **or** \|3h cum\| ≥ 2.8% | Dislocation / capitulation |

**Empirical frequencies (full sample):** TREND_UP 33.7%, TREND_DOWN 29.4%, RANGE 24.9%, SHOCK 12.0%.

**Estimated transition matrix P(s′\|s)** (full sample, Laplace-smoothed):

| from \ to | TREND_UP | TREND_DOWN | RANGE | SHOCK |
|---|---:|---:|---:|---:|
| TREND_UP | 0.862 | 0.009 | 0.104 | 0.025 |
| TREND_DOWN | 0.009 | 0.849 | 0.113 | 0.028 |
| RANGE | 0.141 | 0.131 | 0.718 | 0.010 |
| SHOCK | 0.073 | 0.071 | 0.015 | 0.840 |

States are **sticky** (diag ≫ 0.7). Shocks persist (~84%) then bleed into trends — the recovery path the strategy harvests.

---

## 4. What triggers large moves

1. **Vol spikes / SHOCK labels** — simultaneous with 1h jumps and cascading liquidations.
2. **US/EU overlap hours** — denser large-move counts.
3. **Trend exhaustion into SHOCK** — `TREND_DOWN → SHOCK` often marks capitulation; forward 24–48h mean return positive after deep 3h dumps (c3 ≤ −3.5%).
4. **Macro/news** (not modeled explicitly; absorbed into SHOCK emissions).

---

## 5. Math: expectancy, hitrate, payoff, sharpe/sortino, maxDD

Hold-horizon (36h) **long** edge after 22 bps costs (IS/full fit):

| State | E[r_hold\|s] − cost |
|---|---:|
| TREND_UP | ~+4 bps |
| TREND_DOWN | ~−5 bps (long) |
| RANGE | ~−3 bps |
| **SHOCK** (esp. dump-conditioned) | **~+76 bps** |

**Transition-conditional edges** (after costs): entering `SHOCK` from `TREND_DOWN` / `RANGE` carries the strongest long expectancy.

**Prop-eval (100 randomized OOS starts, 180d windows, fees+slippage, equity-mapped stops):**

| Metric | Value |
|---|---:|
| Prop pass rate | **5/100 (5%)** |
| Monthly profit mean (window-scaled) | **+0.61%** |
| Monthly profit median | ~0% |
| Hitrate (OOS trades) | 0.375 |
| Payoff ratio | 0.71 |
| Expectancy (OOS $/trade) | −399 |
| Sharpe (OOS hourly ann.) | −0.53 |
| Sortino | −0.05 |
| Max DD observed (prop) | 4.7% |
| Max daily loss observed | −2.2% |
| Max leverage | 0.55x |
| Risk breaches 3%/6%/5x | **0 / 0 / 0** |

Walk-forward (5 expanding folds): **FAIL** (mean OOS monthly ≈ −0.2%; 3/5 folds positive expectancy but average sharpe negative).

---

## 6. How the strategy exploits findings (via state transitions)

1. **Bayes posterior** `π_t` over states; update with Gaussian emissions after each bar; predict via `π_t P`.
2. **Trade only positive-edge transitions:**
   - Deep SHOCK recovery when `cum3 ≤ −3.5%` (and TOD filter or deeper dump).
   - `TREND_DOWN → SHOCK` mild recovery.
   - `TREND_DOWN → TREND_UP` transition long.
3. **Never short** — data shows post-shock upside asymmetry; shorting pumps fails after costs.
4. **Equity-mapped stops** (≈1.5% equity stop / ≈5.5% equity TP) + gap-aware fills so a single trade cannot breach daily −3% under normal gaps; leverage capped ≤ 0.55x for risk integrity.
5. Soft daily / DD halts flatten before hard prop fails.

---

## 7. Walk-forward / OOS plan

1. **IS fit:** first ≥40% of sample (and ≥1y) — estimate `P`, emissions, transition edges.
2. **Expanding WF:** 5 folds; train on `[0, train_end)`, test next block; require non-collapsed mean OOS expectancy/sharpe.
3. **Prop OOS:** 100 uniform random starts in post-fit region; each run 180 calendar days or until pass/fail.
4. **No peeking:** labels/features causal; costs applied every fill.
5. **Pass criterion for WF:** mean OOS sharpe > −0.25, mean OOS monthly > −2%, ≥50% folds with positive expectancy, no fold maxDD > 25%. Current run: **does not pass** — reported honestly.

### Structural note (probability)

With BTC hourly gap risk, leverage high enough to hit +10% in ≤180d **materially raises** P(daily −3% breach). The submitted config prioritizes **zero hard risk breaches** over pass-rate, accepting low pass-rate as the honest tradeoff pending further iteration.
