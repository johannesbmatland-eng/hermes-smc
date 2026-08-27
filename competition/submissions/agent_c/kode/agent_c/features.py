"""Feature engineering: ATR, vol expansion, flow proxies, regime, TOD."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategyParams, DEFAULT_PARAMS


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev = np.roll(close, 1)
    prev[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    return tr


def wilder_atr(tr: np.ndarray, length: int) -> np.ndarray:
    atr = np.full_like(tr, np.nan, dtype=np.float64)
    if len(tr) < length:
        return atr
    atr[length - 1] = np.mean(tr[:length])
    alpha = 1.0 / length
    for i in range(length, len(tr)):
        atr[i] = atr[i - 1] * (1 - alpha) + tr[i] * alpha
    return atr


def rolling_median(x: np.ndarray, length: int) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=np.float64)
    for i in range(length - 1, len(x)):
        out[i] = np.median(x[i - length + 1 : i + 1])
    return out


def rolling_mean(x: np.ndarray, length: int) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=np.float64)
    csum = np.cumsum(np.insert(x, 0, 0.0))
    for i in range(length - 1, len(x)):
        out[i] = (csum[i + 1] - csum[i + 1 - length]) / length
    return out


def rolling_std(x: np.ndarray, length: int) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=np.float64)
    for i in range(length - 1, len(x)):
        out[i] = np.std(x[i - length + 1 : i + 1], ddof=0)
    return out


def ema(x: np.ndarray, length: int) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=np.float64)
    if len(x) == 0:
        return out
    alpha = 2.0 / (length + 1.0)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    # warm-up: treat early as nan until length
    out[: length - 1] = np.nan
    return out


def efficiency_ratio(close: np.ndarray, length: int) -> np.ndarray:
    """Kaufman ER: |net change| / sum(|changes|) — high = trend, low = chop."""
    out = np.full_like(close, np.nan, dtype=np.float64)
    for i in range(length, len(close)):
        net = abs(close[i] - close[i - length])
        path = np.sum(np.abs(np.diff(close[i - length : i + 1])))
        out[i] = net / path if path > 1e-12 else 0.0
    return out


def donchian(high: np.ndarray, low: np.ndarray, length: int) -> tuple[np.ndarray, np.ndarray]:
    up = np.full_like(high, np.nan, dtype=np.float64)
    dn = np.full_like(low, np.nan, dtype=np.float64)
    for i in range(length, len(high)):
        # prior window excluding current bar (no lookahead)
        up[i] = np.max(high[i - length : i])
        dn[i] = np.min(low[i - length : i])
    return up, dn


def signed_volume_flow(close: np.ndarray, volume: np.ndarray, lookback: int) -> tuple[np.ndarray, np.ndarray]:
    """Proxy for aggressive flow: sum(sign(ret) * volume) over lookback, plus z-score."""
    ret = np.zeros_like(close)
    ret[1:] = np.diff(close)
    signed = np.sign(ret) * volume
    flow = np.full_like(close, np.nan, dtype=np.float64)
    flow_z = np.full_like(close, np.nan, dtype=np.float64)
    # baseline for z: longer window
    base = max(lookback * 8, 48)
    for i in range(base, len(close)):
        window = signed[i - lookback + 1 : i + 1]
        flow[i] = np.sum(window)
        hist = []
        for j in range(i - base + lookback, i + 1):
            hist.append(np.sum(signed[j - lookback + 1 : j + 1]))
        hist_a = np.asarray(hist, dtype=np.float64)
        mu, sd = np.mean(hist_a), np.std(hist_a)
        flow_z[i] = (flow[i] - mu) / sd if sd > 1e-12 else 0.0
    return flow, flow_z


def build_features(df: pd.DataFrame, params: StrategyParams | None = None) -> pd.DataFrame:
    p = params or DEFAULT_PARAMS
    out = df.copy()
    h = out["high"].to_numpy(dtype=np.float64)
    l = out["low"].to_numpy(dtype=np.float64)
    c = out["close"].to_numpy(dtype=np.float64)
    v = out["volume"].to_numpy(dtype=np.float64)

    tr = true_range(h, l, c)
    atr = wilder_atr(tr, p.atr_len)
    atr_med = rolling_median(atr, p.atr_baseline_len)
    with np.errstate(divide="ignore", invalid="ignore"):
        atr_ratio = atr / atr_med

    up, dn = donchian(h, l, p.range_lookback)
    vol_sma = rolling_mean(v, p.vol_sma_len)
    with np.errstate(divide="ignore", invalid="ignore"):
        vol_ratio = v / vol_sma

    flow, flow_z = signed_volume_flow(c, v, p.flow_lookback)
    er = efficiency_ratio(c, p.er_len)
    ema_f = ema(c, p.ema_fast)
    ema_s = ema(c, p.ema_slow)

    ret1 = np.zeros_like(c)
    ret1[1:] = np.diff(c) / c[:-1]

    out["atr"] = atr
    out["atr_med"] = atr_med
    out["atr_ratio"] = atr_ratio
    out["donch_up"] = up
    out["donch_dn"] = dn
    out["vol_ratio"] = vol_ratio
    out["flow"] = flow
    out["flow_z"] = flow_z
    out["er"] = er
    out["ema_fast"] = ema_f
    out["ema_slow"] = ema_s
    out["ret1"] = ret1
    out["hour"] = out["timestamp"].dt.hour
    out["dow"] = out["timestamp"].dt.dayofweek  # Mon=0
    return out
