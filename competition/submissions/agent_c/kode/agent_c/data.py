"""OHLC loading — prefer multi-year research series; strategy runs on 4H."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def load_ohlc(path: str | Path | None = None, prefer: str = "hourly_research") -> pd.DataFrame:
    if path is None:
        candidates = [
            DATA_DIR / f"btcusd_{prefer}.csv",
            DATA_DIR / "btcusd_hourly_research.csv",
            DATA_DIR / "btcusd_hourly_yahoo.csv",
            DATA_DIR / "btcusd_4h_research.csv",
            DATA_DIR / "btcusd_hourly_kraken.csv",
        ]
        for c in candidates:
            if c.exists():
                path = c
                break
        if path is None:
            raise FileNotFoundError(f"No OHLC under {DATA_DIR}")
    path = Path(path)
    df = pd.read_csv(path)
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    df.attrs["source_path"] = str(path)
    return df


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    x = df.set_index("timestamp")
    ohlc = (
        x.resample(rule)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    return ohlc


def load_strategy_frame(path: str | Path | None = None, timeframe: str = "4h") -> pd.DataFrame:
    raw = load_ohlc(path)
    if timeframe.lower() in ("1h", "h1", "hourly"):
        return raw
    frame = resample_ohlc(raw, timeframe)
    frame.attrs["source_path"] = raw.attrs.get("source_path", "")
    frame.attrs["timeframe"] = timeframe
    return frame
