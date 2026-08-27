"""Engine core: data fetching, market structure detection, trade logic."""

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import ccxt.async_support as ccxt
import numpy as np

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Configuration (will be loaded from YAML in production)
# ----------------------------------------------------------------------

DEFAULT_CONFIG = {
    "market": "BTC/USD",
    "timeframes": {
        "main": "5m",
        "trend_1h": "1h",
        "trend_15m": "15m",
    },
    "trend_filter": {
        "enabled": True,
        "method": "ema_and_structure",  # 'ema' or 'ema_and_structure'
        "ema_period": 50,
        "min_bos_since_start": 1,
    },
    "fvq_detection": {
        "min_candles_since_fvg": 50,  # don't trade very old FVGs
        "fvg_buffer_pct": 0.001,  # 0.1% buffer for entry
    },
    "entry": {
        "confirmation": "engulfing_or_ifvg",  # 'engulfing' | 'ifvg' | 'both'
        "pullback_depth_pct": 0.5,  # enter when price pulls back to 50% of move
        "max_open_positions": 1,
        "cooldown_seconds": 300,
    },
    "risk": {
        "risk_pct_per_trade": 0.5,
        "rr_target": 0.5,  # 1/2 RR default
        "rr_alternative": 0.333,  # 1/3 RR
        "sl_buffer_pct": 0.002,  # 0.2% SL buffer below FVG
    },
    "paper_trading": {
        "initial_capital": 100000,
        "currency": "USD",
        "exchange": "kraken",  # paper mode uses kraken API but simulated
    },
}


class MarketData:
    """Fetch and cache market data from Kraken."""

    def __init__(self, exchange_id: str = "kraken"):
        self.exchange_id = exchange_id
        self._cache: dict[str, Any] = {}
        self._cache_ts: dict[str, float] = {}
        self._cache_duration = 30  # seconds

    async def fetch_candles(self, symbol: str, timeframe: str, limit: int = 500) -> list[dict]:
        """Fetch OHLCV candles, with caching."""
        cache_key = f"{symbol}:{timeframe}:{limit}"
        now = time.time()
        if cache_key in self._cache and now - self._cache_ts.get(cache_key, 0) < self._cache_duration:
            return self._cache[cache_key]

        try:
            exchange = getattr(ccxt, self.exchange_id)({"enableRateLimit": True})
            ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            await exchange.close()

            candles = [
                {
                    "timestamp": int(c[0] / 1000),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                }
                for c in ohlcv
            ]
            self._cache[cache_key] = candles
            self._cache_ts[cache_key] = now
            return candles
        except Exception as e:
            logger.error(f"Failed to fetch candles: {e}")
            if cache_key in self._cache:
                return self._cache[cache_key]
            raise

    async def get_latest_price(self, symbol: str) -> float:
        """Get latest price for symbol."""
        try:
            exchange = getattr(ccxt, self.exchange_id)({"enableRateLimit": True})
            ticker = await exchange.fetch_ticker(symbol)
            await exchange.close()
            return float(ticker["last"])
        except Exception as e:
            logger.error(f"Failed to get price: {e}")
            if "price" in self._cache:
                return self._cache["price"]
            raise


class MarketStructureDetector:
    """Detect ICT/SMC concepts: FVG, BOS, HH/HL, order blocks."""

    @staticmethod
    def detect_fvg(candles: list[dict]) -> list[dict]:
        """
        Detect 3-candle Fair Value Gaps (ICT).

        Candle 1 = first, candle 2 = impulse, candle 3 = last.
        Bullish: last low does not overlap first high (last.low > first.high).
        Bearish: last high does not overlap first low (last.high < first.low).
        The gap zone is the imbalance between those two wicks.
        """
        fvgs = []
        for i in range(2, len(candles)):
            c_first = candles[i - 2]
            c_last = candles[i]

            # Bullish FVG: last low above first high (no wick overlap)
            if c_last["low"] > c_first["high"]:
                fvg_bottom = c_first["high"]
                fvg_top = c_last["low"]
                fvgs.append({
                    "type": "bullish",
                    "top": fvg_top,
                    "bottom": fvg_bottom,
                    "mid": (fvg_top + fvg_bottom) / 2,
                    "start_candle": i - 2,
                    "end_candle": i,
                    "timestamp": c_first["timestamp"],
                    "unmitigated": True,
                })

            # Bearish FVG: last high below first low (no wick overlap)
            elif c_last["high"] < c_first["low"]:
                fvg_top = c_first["low"]
                fvg_bottom = c_last["high"]
                fvgs.append({
                    "type": "bearish",
                    "top": fvg_top,
                    "bottom": fvg_bottom,
                    "mid": (fvg_top + fvg_bottom) / 2,
                    "start_candle": i - 2,
                    "end_candle": i,
                    "timestamp": c_first["timestamp"],
                    "unmitigated": True,
                })

        for fvg in fvgs:
            fvg["unmitigated"] = MarketStructureDetector._is_fvg_unmitigated(candles, fvg)

        return fvgs

    @staticmethod
    def _is_fvg_unmitigated(candles: list[dict], fvg: dict) -> bool:
        """Check if an FVG has been mitigated by price."""
        for c in candles[fvg["end_candle"]:]:
            if fvg["type"] == "bullish":
                # Price has mitigated if low <= fvg_bottom
                if c["low"] <= fvg["bottom"]:
                    return False
            else:
                # Price has mitigated if high >= fvg_top
                if c["high"] >= fvg["top"]:
                    return False
        return True

    @staticmethod
    def detect_bos_hh_hl(candles: list[dict], lookback: int = 50) -> dict:
        """Detect Break of Structure (BOS), Higher Highs (HH), Higher Lows (HL), etc."""
        if len(candles) < lookback:
            return {"bos": [], "hh": [], "hl": [], "lh": [], "ll": []}

        recent = candles[-lookback:]
        prices = [(c["high"], c["low"], c["timestamp"]) for c in recent]

        # Find HH/HL/LH/LL
        hh = []  # Higher High
        hl = []  # Higher Low
        lh = []  # Lower High
        ll = []  # Lower Low

        last_hh_idx = -1
        last_hl_idx = -1
        last_lh_idx = -1
        last_ll_idx = -1

        for i in range(1, len(prices)):
            high, low, ts = prices[i]
            prev_high, prev_low, _ = prices[i - 1]

            # HH: new high that is higher than previous high
            if high > prev_high:
                if last_hh_idx == -1 or high > prices[last_hh_idx][0]:
                    hh.append({"index": i, "price": high, "timestamp": ts})
                    last_hh_idx = i

            # LH: new high that is lower than previous swing high
            if high < prev_high:
                if last_lh_idx == -1 or high < prices[last_lh_idx][0]:
                    lh.append({"index": i, "price": high, "timestamp": ts})
                    last_lh_idx = i

            # HL: new low that is higher than previous low
            if low > prev_low:
                if last_hl_idx == -1 or low > prices[last_hl_idx][1]:
                    hl.append({"index": i, "price": low, "timestamp": ts})
                    last_hl_idx = i

            # LL: new low that is lower than previous swing low
            if low < prev_low:
                if last_ll_idx == -1 or low < prices[last_ll_idx][1]:
                    ll.append({"index": i, "price": low, "timestamp": ts})
                    last_ll_idx = i

        # BOS: when price moves beyond previous high/low in trend direction
        bos = []
        for i in range(2, len(prices)):
            high, low, ts = prices[i]
            # Simple BOS: price breaks previous structure high (uptrend) or low (downtrend)
            if i > 0:
                prev_high = prices[i - 1][0]
                prev_low = prices[i - 1][1]
                if high > prev_high + (prev_high * 0.0005):  # 0.05% buffer
                    bos.append({"index": i, "type": "bullish", "price": high, "timestamp": ts})
                elif low < prev_low - (prev_low * 0.0005):
                    bos.append({"index": i, "type": "bearish", "price": low, "timestamp": ts})

        return {
            "bos": bos,
            "hh": hh,
            "hl": hl,
            "lh": lh,
            "ll": ll,
            "latest_hh": hh[-1] if hh else None,
            "latest_hl": hl[-1] if hl else None,
            "latest_lh": lh[-1] if lh else None,
            "latest_ll": ll[-1] if ll else None,
        }


class TrendAnalyzer:
    """Analyze trend using EMA and market structure."""

    @staticmethod
    def calculate_ema(candles: list[dict], period: int) -> float | None:
        """Calculate Exponential Moving Average."""
        if len(candles) < period:
            return None
        closes = [c["close"] for c in candles]
        k = 2 / (period + 1)
        ema = sum(closes[:period]) / period
        for price in closes[period:]:
            ema = price * k + ema * (1 - k)
        return ema

    @staticmethod
    def analyze_trend(
        candles_5m: list[dict],
        candles_15m: list[dict],
        candles_1h: list[dict],
        config: dict,
    ) -> dict:
        """Analyze trend across multiple timeframes and return trend assessment."""
        result = {
            "trend_5m": "neutral",
            "trend_15m": "neutral",
            "trend_1h": "neutral",
            "overall": "neutral",
            "details": {},
        }

        # EMA analysis
        ema_period = config.get("trend_filter", {}).get("ema_period", 50)

        ema_5m = TrendAnalyzer.calculate_ema(candles_5m, ema_period)
        ema_15m = TrendAnalyzer.calculate_ema(candles_15m, ema_period)
        ema_1h = TrendAnalyzer.calculate_ema(candles_1h, ema_period)

        result["details"]["ema_5m"] = ema_5m
        result["details"]["ema_15m"] = ema_15m
        result["details"]["ema_1h"] = ema_1h

        # Determine trend direction
        if ema_5m:
            current_price_5m = candles_5m[-1]["close"]
            if current_price_5m > ema_5m:
                result["trend_5m"] = "bullish"
            elif current_price_5m < ema_5m:
                result["trend_5m"] = "bearish"

        if ema_15m:
            current_price_15m = candles_15m[-1]["close"]
            if current_price_15m > ema_15m:
                result["trend_15m"] = "bullish"
            elif current_price_15m < ema_15m:
                result["trend_15m"] = "bearish"

        if ema_1h:
            current_price_1h = candles_1h[-1]["close"]
            if current_price_1h > ema_1h:
                result["trend_1h"] = "bullish"
            elif current_price_1h < ema_1h:
                result["trend_1h"] = "bearish"

        # Market structure analysis
        structure_5m = MarketStructureDetector.detect_bos_hh_hl(candles_5m)
        structure_15m = MarketStructureDetector.detect_bos_hh_hl(candles_15m)
        structure_1h = MarketStructureDetector.detect_bos_hh_hl(candles_1h)

        result["details"]["structure_5m"] = structure_5m
        result["details"]["structure_15m"] = structure_15m
        result["details"]["structure_1h"] = structure_1h

        # Overall trend determination
        bullish_count = 0
        bearish_count = 0

        if result["trend_5m"] == "bullish":
            bullish_count += 1
        elif result["trend_5m"] == "bearish":
            bearish_count += 1

        if result["trend_15m"] == "bullish":
            bullish_count += 1
        elif result["trend_15m"] == "bearish":
            bearish_count += 1

        if result["trend_1h"] == "bullish":
            bullish_count += 1
        elif result["trend_1h"] == "bearish":
            bearish_count += 1

        if bullish_count > bearish_count:
            result["overall"] = "bullish"
        elif bearish_count > bullish_count:
            result["overall"] = "bearish"
        else:
            result["overall"] = "neutral"

        # Check for BOS and HH/HL (uptrend) / LH/LL (downtrend) confirmation
        latest_hh_1h = structure_1h.get("latest_hh")
        latest_hl_1h = structure_1h.get("latest_hl")
        latest_hh_15m = structure_15m.get("latest_hh")
        latest_hl_15m = structure_15m.get("latest_hl")
        latest_lh_1h = structure_1h.get("latest_lh")
        latest_ll_1h = structure_1h.get("latest_ll")
        latest_lh_15m = structure_15m.get("latest_lh")
        latest_ll_15m = structure_15m.get("latest_ll")

        result["details"]["confirmed_uptrend"] = (
            latest_hh_1h is not None and latest_hl_1h is not None
        ) or (
            latest_hh_15m is not None and latest_hl_15m is not None
        )
        result["details"]["confirmed_downtrend"] = (
            latest_lh_1h is not None and latest_ll_1h is not None
        ) or (
            latest_lh_15m is not None and latest_ll_15m is not None
        )

        # HH/HL and LH/LL counts
        result["details"]["hh_count_1h"] = len(structure_1h.get("hh", []))
        result["details"]["hl_count_1h"] = len(structure_1h.get("hl", []))
        result["details"]["lh_count_1h"] = len(structure_1h.get("lh", []))
        result["details"]["ll_count_1h"] = len(structure_1h.get("ll", []))

        # Trend filter: long needs bullish+HH/HL, short needs bearish+LH/LL
        trend_filter_enabled = config.get("trend_filter", {}).get("enabled", True)
        if trend_filter_enabled:
            result["trend_filter_pass_long"] = (
                result["overall"] == "bullish" and result["details"]["confirmed_uptrend"]
            )
            result["trend_filter_pass_short"] = (
                result["overall"] == "bearish" and result["details"]["confirmed_downtrend"]
            )
            result["trend_filter_pass"] = (
                result["trend_filter_pass_long"] or result["trend_filter_pass_short"]
            )
        else:
            result["trend_filter_pass_long"] = True
            result["trend_filter_pass_short"] = True
            result["trend_filter_pass"] = True

        return result
