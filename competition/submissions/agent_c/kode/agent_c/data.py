"""OHLC loading and alignment."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def load_ohlc(path: str | Path | None = None, prefer: str = "hourly_research") -> pd.DataFrame:
    """Load BTCUSD OHLC. Prefer long research series; fall back to Kraken window."""
    if path is None:
        candidates = [
            DATA_DIR / f"btcusd_{prefer}.csv",
            DATA_DIR / "btcusd_hourly_research.csv",
            DATA_DIR / "btcusd_hourly_yahoo.csv",
            DATA_DIR / "btcusd_hourly_kraken.csv",
            DATA_DIR / "btcusd_4h_research.csv",
            DATA_DIR / "btcusd_daily_kraken.csv",
        ]
        for c in candidates:
            if c.exists():
                path = c
                break
        if path is None:
            raise FileNotFoundError(f"No OHLC under {DATA_DIR}")
    path = Path(path)
    df = pd.read_csv(path)
    if "timestamp" not in df.columns:
        raise ValueError(f"missing timestamp in {path}")
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            raise ValueError(f"missing {col}")
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    df.attrs["source_path"] = str(path)
    return df


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    x = df.set_index("timestamp")
    ohlc = x.resample(rule).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    ).dropna()
    ohlc = ohlc.reset_index()
    return ohlc


def slice_window(df: pd.DataFrame, start: pd.Timestamp | None, end: pd.Timestamp | None) -> pd.DataFrame:
    out = df
    if start is not None:
        out = out[out["timestamp"] >= start]
    if end is not None:
        out = out[out["timestamp"] < end]
    return out.reset_index(drop=True)


def np_close(df: pd.DataFrame) -> np.ndarray:
    return df["close"].to_numpy(dtype=np.float64)
