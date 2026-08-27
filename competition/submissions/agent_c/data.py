"""Kraken OHLCV fetch + synthetic fallback for offline backtests."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np


def ohlcv_to_arrays(rows: list[list[float]]) -> dict[str, np.ndarray]:
    """ccxt OHLCV rows: [ts_ms, o, h, l, c, v]"""
    arr = np.array(rows, dtype=float)
    return {
        "ts": arr[:, 0] / 1000.0,
        "open": arr[:, 1],
        "high": arr[:, 2],
        "low": arr[:, 3],
        "close": arr[:, 4],
        "volume": arr[:, 5],
    }


def generate_synthetic_ohlcv(
    n: int = 2000,
    start_price: float = 60_000.0,
    seed: int = 42,
    timeframe_seconds: int = 3600,
) -> dict[str, np.ndarray]:
    """GBM-ish synthetic candles for offline CI when network is blocked."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.00005, 0.006, size=n)
    # inject regimes: trending then choppy (clipped to series length)
    for i in range(min(400, n), min(800, n)):
        rets[i] += 0.0012
    for i in range(min(1200, n), min(1600, n)):
        rets[i] -= 0.0010
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.roll(close, 1)
    open_[0] = start_price
    spread = np.abs(rng.normal(0.002, 0.001, size=n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = rng.uniform(10, 100, size=n)
    now = time.time()
    ts = np.array([now - (n - i) * timeframe_seconds for i in range(n)], dtype=float)
    return {
        "ts": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def fetch_kraken_ohlcv(
    market: str,
    timeframe: str = "1h",
    limit: int = 720,
    exchange: Any | None = None,
) -> dict[str, np.ndarray]:
    """Fetch public Kraken candles via ccxt. No API keys."""
    import ccxt  # local import so synthetic path works without network deps issues

    own = exchange is None
    ex = exchange or ccxt.kraken({"enableRateLimit": True})
    try:
        rows = ex.fetch_ohlcv(market, timeframe=timeframe, limit=limit)
        if not rows or len(rows) < 100:
            raise RuntimeError(f"insufficient OHLCV for {market}")
        return ohlcv_to_arrays(rows)
    finally:
        if own and hasattr(ex, "close"):
            # sync ccxt
            pass


def load_or_fetch(
    market: str,
    timeframe: str = "1h",
    limit: int = 720,
    cache_dir: Path | None = None,
    allow_synthetic: bool = True,
) -> tuple[dict[str, np.ndarray], str]:
    cache_dir = cache_dir or Path(__file__).resolve().parent / "data"
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = market.replace("/", "_")
    cache_path = cache_dir / f"{safe}_{timeframe}_{limit}.npz"
    if cache_path.exists():
        data = dict(np.load(cache_path))
        return data, f"cache:{cache_path.name}"

    try:
        data = fetch_kraken_ohlcv(market, timeframe=timeframe, limit=limit)
        np.savez(cache_path, **data)
        return data, f"kraken:{market}"
    except Exception as exc:
        if not allow_synthetic:
            raise
        seed = 42 if "BTC" in market else 7
        start = 65000.0 if "BTC" in market else 3500.0
        data = generate_synthetic_ohlcv(n=max(limit, 1500), start_price=start, seed=seed)
        np.savez(cache_dir / f"synthetic_{safe}_{timeframe}.npz", **data)
        return data, f"synthetic:{market}:{exc}"
