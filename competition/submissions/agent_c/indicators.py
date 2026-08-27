"""Technical indicators — pure numpy, no external TA lib required."""

from __future__ import annotations

import numpy as np


def ema(series: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(series, np.nan, dtype=float)
    if len(series) < period:
        return out
    k = 2.0 / (period + 1)
    out[period - 1] = np.mean(series[:period])
    for i in range(period, len(series)):
        out[i] = series[i] * k + out[i - 1] * (1 - k)
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    n = len(close)
    tr = np.zeros(n, dtype=float)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    out = np.full(n, np.nan, dtype=float)
    if n < period:
        return out
    out[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def donchian_high(high: np.ndarray, lookback: int) -> np.ndarray:
    out = np.full_like(high, np.nan, dtype=float)
    for i in range(lookback, len(high)):
        out[i] = np.max(high[i - lookback : i])  # exclude current bar
    return out


def donchian_low(low: np.ndarray, lookback: int) -> np.ndarray:
    out = np.full_like(low, np.nan, dtype=float)
    for i in range(lookback, len(low)):
        out[i] = np.min(low[i - lookback : i])
    return out


def rolling_percentile_rank(values: np.ndarray, lookback: int) -> np.ndarray:
    """Percentile rank of current value within trailing window (0-100)."""
    out = np.full_like(values, np.nan, dtype=float)
    for i in range(lookback, len(values)):
        window = values[i - lookback : i + 1]
        if np.any(np.isnan(window)):
            continue
        out[i] = 100.0 * np.mean(window <= values[i])
    return out
