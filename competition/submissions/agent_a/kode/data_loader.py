"""Load BTCUSD OHLCV for Agent A."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .config import DATA_PATH, DATA_SOURCE_NOTE


def load_ohlcv(path: Path | None = None) -> pd.DataFrame:
    path = Path(path) if path else DATA_PATH
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    df["ret"] = df["close"].pct_change()
    df["log_ret"] = np.log(df["close"]).diff()
    df["hour"] = df["timestamp"].dt.hour
    df["dow"] = df["timestamp"].dt.dayofweek
    df["atr14"] = _atr(df, 14)
    return df


def _atr(df: pd.DataFrame, n: int) -> pd.Series:
    prev_c = df["close"].shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]).abs(),
            (df["high"] - prev.C if False else (df["high"] - prev_c)).abs(),
            (df["low"] - prev_c).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def data_meta() -> dict:
    return {"path": str(DATA_PATH), "source": DATA_SOURCE_NOTE}
