"""Feature helpers used by research_scan (hourly diagnostics)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategyParams, DEFAULT_PARAMS


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev = np.roll(close, 1)
    prev[0] = close[0]
    return np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))


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
    return pd.Series(x).rolling(length).median().to_numpy(dtype=np.float64)


def rolling_mean(x: np.ndarray, length: int) -> np.ndarray:
    return pd.Series(x).rolling(length).mean().to_numpy(dtype=np.float64)


def ema(x: np.ndarray, length: int) -> np.ndarray:
    out = pd.Series(x).ewm(span=length, adjust=False).mean().to_numpy(dtype=np.float64)
    out[: length - 1] = np.nan
    return out


def efficiency_ratio(close: np.ndarray, length: int) -> np.ndarray:
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
        up[i] = np.max(high[i - length : i])
        dn[i] = np.min(low[i - length : i])
    return up, dn


def signed_volume_flow(close: np.ndarray, volume: np.ndarray, lookback: int) -> tuple[np.ndarray, np.ndarray]:
    ret = np.zeros_like(close)
    ret[1:] = np.diff(close)
    signed = np.sign(ret) * volume
    flow = pd.Series(signed).rolling(lookback).sum().to_numpy(dtype=np.float64)
    mu = pd.Series(flow).rolling(lookback * 8).mean()
    sd = pd.Series(flow).rolling(lookback * 8).std()
    flow_z = ((pd.Series(flow) - mu) / sd).to_numpy(dtype=np.float64)
    return flow, flow_z


def build_features(df: pd.DataFrame, params: StrategyParams | None = None) -> pd.DataFrame:
    """Hourly research features. Uses getattr for schema drift safety."""
    p = params or DEFAULT_PARAMS
    out = df.copy()
    h = out["high"].to_numpy(dtype=np.float64)
    l = out["low"].to_numpy(dtype=np.float64)
    c = out["close"].to_numpy(dtype=np.float64)
    v = out["volume"].to_numpy(dtype=np.float64)

    atr_len = int(getattr(p, "atr_len", 14))
    atr_base = int(getattr(p, "atr_baseline_len", 48))
    range_lb = int(getattr(p, "range_lookback", getattr(p, "lookbacks", (24,))[0] if getattr(p, "lookbacks", None) else 24))
    vol_sma_len = int(getattr(p, "vol_sma_len", 48))
    flow_lb = int(getattr(p, "flow_lookback", 6))
    er_len = int(getattr(p, "er_len", 24))
    ema_fast = int(getattr(p, "ema_fast", 48))
    ema_slow = int(getattr(p, "ema_slow", 168))

    tr = true_range(h, l, c)
    atr = wilder_atr(tr, atr_len)
    atr_med = rolling_median(atr, atr_base)
    with np.errstate(divide="ignore", invalid="ignore"):
        atr_ratio = atr / atr_med
    up, dn = donchian(h, l, range_lb)
    vol_sma = rolling_mean(v, vol_sma_len)
    with np.errstate(divide="ignore", invalid="ignore"):
        vol_ratio = v / vol_sma
    flow, flow_z = signed_volume_flow(c, v, flow_lb)
    er = efficiency_ratio(c, er_len)
    ema_f = ema(c, ema_fast)
    ema_s = ema(c, ema_slow)
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
    out["dow"] = out["timestamp"].dt.dayofweek
    return out
