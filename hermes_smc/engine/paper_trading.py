"""Paper trading simulator for SMC engine testing."""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import ccxt.async_support as ccxt
import numpy as np

from .smc_engine import SMCEngine, SMCConfig, PositionManager

logger = logging.getLogger(__name__)


class PaperTradingEngine(SMCEngine):
    """Paper trading version of SMC engine with simulated fills."""

    def __init__(self, config: SMCConfig | None = None):
        super().__init__(config)
        self.paper_mode = True
        self.simulated_fills: list[dict] = []

    async def fetch_all_timeframes(self) -> dict[str, list[dict]]:
        """Fetch candles including 1m for confirmation signals."""
        market = self.config.get("market", "BTC/EUR")
        timeframes = self.config.get("timeframes", {})

        tasks = []
        # Main 5m
        tasks.append(self.market_data.fetch_candles(market, "5m", 500))
        # Trend 15m
        tasks.append(self.market_data.fetch_candles(market, "15m", 200))
        # Trend 1h
        tasks.append(self.market_data.fetch_candles(market, "1h", 200))
        # 1m for confirmation (IFVG detection)
        tasks.append(self.market_data.fetch_candles(market, "1m", 300))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        return {
            "main": results[0] if not isinstance(results[0], Exception) else [],
            "trend_15m": results[1] if len(results) > 1 and not isinstance(results[1], Exception) else [],
            "trend_1h": results[2] if len(results) > 2 and not isinstance(results[2], Exception) else [],
            "confirmation_1m": results[3] if len(results) > 3 and not isinstance(results[3], Exception) else [],
        }

    def detect_entry_signal(
        self,
        candles_5m: list[dict],
        candles_15m: list[dict],
        candles_1h: list[dict],
        candles_1m: list[dict] | None = None,
    ) -> dict | None:
        """
        Detect SMC entry signal with full ICT concepts:
        - Unmitigated FVG
        - Pullback into FVG (50% of move)
        - Confirmation: engulfing on 5m OR IFVG on 1m
        - Trend filter: EMA + BOS/HH-HL on 1h/15m
        """
        market = self.config.get("market", "BTC/EUR")

        # Detect FVG on 5m
        fvgs = self._detect_fvg_full(candles_5m)
        unmitigated_bullish = [f for f in fvgs if f["unmitigated"] and f["type"] == "bullish"]

        if not unmitigated_bullish:
            return None

        # Trend filter
        trend_result = self._analyze_trend_full(candles_5m, candles_15m, candles_1h)
        if not trend_result.get("trend_filter_pass", True):
            logger.debug("Trend filter not passed")
            return None

        # Find best FVG
        best_fvg = self._find_best_fvg(unmitigated_bullish, candles_5m)
        if not best_fvg:
            return None

        # Confirmation
        confirmation = self._check_smc_confirmation(candles_5m, candles_1m, best_fvg)
        if not confirmation:
            logger.debug("No confirmation signal")
            return None

        # Entry mechanics
        entry_price = candles_5m[-1]["close"]
        sl_price = self._calculate_smc_sl(entry_price, best_fvg)
        tp_price = self._calculate_smc_tp(entry_price, sl_price)

        # Position size (0.5% risk)
        position_size = self._calculate_position_size(entry_price, sl_price)

        if position_size <= 0:
            return None

        logger.info(
            f"SMC Entry Signal: FVG [{best_fvg['bottom']:.2f}-{best_fvg['top']:.2f}], "
            f"entry @ {entry_price:.2f}, SL @ {sl_price:.2f}, TP @ {tp_price:.2f}, "
            f"size: {position_size:.6f} BTC, confirmation: {confirmation}"
        )

        return {
            "type": "entry",
            "fvg": best_fvg,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "position_size": position_size,
            "confirmation": confirmation,
            "trend_info": trend_result,
            "timestamp": time.time(),
        }

    def _detect_fvg_full(self, candles: list[dict]) -> list[dict]:
        """Detect all FVGs with full ICT definition."""
        fvgs = []

        for i in range(2, len(candles)):
            c_current = candles[i]
            c_prev = candles[i - 1]

            # Bullish FVG: current low > previous high (gap up)
            if c_current["low"] > c_prev["high"]:
                fvg_top = c_current["high"]
                fvg_bottom = c_prev["low"]
                fvgs.append({
                    "type": "bullish",
                    "top": fvg_top,
                    "bottom": fvg_bottom,
                    "mid": (fvg_top + fvg_bottom) / 2,
                    "start_candle": i - 1,
                    "end_candle": i,
                    "timestamp": c_prev["timestamp"],
                    "size_pct": (fvg_top - fvg_bottom) / fvg_bottom * 100,
                    "unmitigated": True,
                })

            # Bearish FVG: current high < previous low (gap down)
            elif c_current["high"] < c_prev["low"]:
                fvg_top = c_prev["high"]
                fvg_bottom = c_current["low"]
                fvgs.append({
                    "type": "bearish",
                    "top": fvg_top,
                    "bottom": fvg_bottom,
                    "mid": (fvg_top + fvg_bottom) / 2,
                    "start_candle": i - 1,
                    "end_candle": i,
                    "timestamp": c_prev["timestamp"],
                    "size_pct": (fvg_top - fvg_bottom) / fvg_bottom * 100,
                    "unmitigated": True,
                })

        # Check which FVGs are still unmitigated
        for fvg in fvgs:
            fvg["unmitigated"] = self._check_fvg_unmitigated(candles, fvg)

        return fvgs

    def _check_fvg_unmitigated(self, candles: list[dict], fvg: dict) -> bool:
        """Check if FVG has been mitigated by price."""
        for c in candles[fvg["end_candle"]:]:
            if fvg["type"] == "bullish":
                if c["low"] <= fvg["bottom"]:
                    return False
            else:  # bearish
                if c["high"] >= fvg["top"]:
                    return False
        return True

    def _analyze_trend_full(
        self,
        candles_5m: list[dict],
        candles_15m: list[dict],
        candles_1h: list[dict],
    ) -> dict:
        """Comprehensive trend analysis using EMA + market structure."""
        result = {
            "trend_5m": "neutral",
            "trend_15m": "neutral",
            "trend_1h": "neutral",
            "overall": "neutral",
            "details": {},
        }

        ema_period = self.config.get("trend_filter.ema_period", 50)

        # Calculate EMAs
        ema_5m = self._calc_ema(candles_5m, ema_period)
        ema_15m = self._calc_ema(candles_15m, ema_period)
        ema_1h = self._calc_ema(candles_1h, ema_period)

        result["details"]["ema_5m"] = ema_5m
        result["details"]["ema_15m"] = ema_15m
        result["details"]["ema_1h"] = ema_1h

        # Trend direction from EMA
        if ema_5m:
            price_5m = candles_5m[-1]["close"]
            result["trend_5m"] = "bullish" if price_5m > ema_5m else "bearish"

        if ema_15m:
            price_15m = candles_15m[-1]["close"]
            result["trend_15m"] = "bullish" if price_15m > ema_15m else "bearish"

        if ema_1h:
            price_1h = candles_1h[-1]["close"]
            result["trend_1h"] = "bullish" if price_1h > ema_1h else "bearish"

        # Market structure analysis (BOS, HH, HL)
        struct_5m = self._analyze_structure(candles_5m)
        struct_15m = self._analyze_structure(candles_15m)
        struct_1h = self._analyze_structure(candles_1h)

        result["details"]["structure_5m"] = struct_5m
        result["details"]["structure_15m"] = struct_15m
        result["details"]["structure_1h"] = struct_1h

        # Count bullish vs bearish timeframes
        bullish = 0
        bearish = 0
        for tf in ["trend_5m", "trend_15m", "trend_1h"]:
            if result[tf] == "bullish":
                bullish += 1
            elif result[tf] == "bearish":
                bearish += 1

        if bullish > bearish:
            result["overall"] = "bullish"
        elif bearish > bullish:
            result["overall"] = "bearish"
        else:
            result["overall"] = "neutral"

        # Confirmed uptrend: HH + HL on higher timeframes
        confirmed_uptrend = (
            (struct_1h.get("hh_count", 0) > 0 and struct_1h.get("hl_count", 0) > 0)
            or (struct_15m.get("hh_count", 0) > 0 and struct_15m.get("hl_count", 0) > 0)
        )
        result["details"]["confirmed_uptrend"] = confirmed_uptrend

        # Trend filter pass
        if self.config.get("trend_filter.enabled", True):
            result["trend_filter_pass"] = (
                result["overall"] == "bullish" and confirmed_uptrend
            )
        else:
            result["trend_filter_pass"] = True

        return result

    def _calc_ema(self, candles: list[dict], period: int) -> float | None:
        """Calculate EMA from candle closes."""
        if len(candles) < period:
            return None
        closes = [c["close"] for c in candles]
        k = 2 / (period + 1)
        ema = sum(closes[:period]) / period
        for price in closes[period:]:
            ema = price * k + ema * (1 - k)
        return ema

    def _analyze_structure(self, candles: list[dict]) -> dict:
        """Analyze market structure: BOS, HH, HL, etc."""
        if len(candles) < 20:
            return {"bos_count": 0, "hh_count": 0, "hl_count": 0}

        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]

        hh_list = []
        hl_list = []
        bos_list = []

        last_hh = None
        last_hl = None
        last_swing_high = None
        last_swing_low = None

        for i in range(2, len(candles) - 2):
            # Simple swing detection: 2 candles on each side
            if (
                highs[i] > highs[i - 1]
                and highs[i] > highs[i + 1]
                and highs[i] > highs[i - 2]
                and highs[i] > highs[i + 2]
            ):
                if last_swing_high is None or highs[i] > last_swing_high:
                    hh_list.append({"index": i, "price": highs[i]})
                    last_swing_high = highs[i]
                    last_hh = highs[i]

            if (
                lows[i] < lows[i - 1]
                and lows[i] < lows[i + 1]
                and lows[i] < lows[i - 2]
                and lows[i] < lows[i + 2]
            ):
                if last_swing_low is None or lows[i] < last_swing_low:
                    # This would be LL, but for HL we need previous HL
                    if last_hl is not None and lows[i] > last_hl:
                        hl_list.append({"index": i, "price": lows[i]})
                    last_swing_low = lows[i]
                    last_hl = lows[i]

            # BOS: break of previous structure
            if last_hh and highs[i] > last_hh * 1.0005:
                bos_list.append({"index": i, "type": "bullish", "price": highs[i]})
                last_hh = highs[i]

        return {
            "bos_count": len(bos_list),
            "hh_count": len(hh_list),
            "hl_count": len(hl_list),
            "latest_hh": hh_list[-1] if hh_list else None,
            "latest_hl": hl_list[-1] if hl_list else None,
        }

    def _find_best_fvg(self, fvgs: list[dict], candles: list[dict]) -> dict | None:
        """Find the best FVG to trade: recent, unmitigated, price near it."""
        current_price = candles[-1]["close"]
        best = None
        best_score = -1

        for fvg in fvgs:
            candles_since = len(candles) - fvg["end_candle"]
            if candles_since > self.config.get("fvq_detection.min_candles_since_fvg", 50):
                continue

            # Score based on proximity to current price
            fvg_mid = fvg["mid"]
            distance = abs(current_price - fvg_mid) / fvg_mid

            # Prefer FVGs that price is pulling back into
            if fvg["bottom"] <= current_price <= fvg["top"]:
                score = 1.0 - distance * 2  # inside FVG is good
            elif abs(current_price - fvg["bottom"]) / fvg["bottom"] < 0.005:
                score = 0.8  # near bottom
            elif abs(current_price - fvg["top"]) / fvg["top"] < 0.005:
                score = 0.6  # near top
            else:
                continue  # too far away

            if score > best_score:
                best_score = score
                best = fvg

        return best

    def _check_smc_confirmation(
        self,
        candles_5m: list[dict],
        candles_1m: list[dict] | None,
        fvg: dict,
    ) -> str | None:
        """Check for SMC entry confirmation."""
        method = self.config.get("entry.confirmation", "engulfing_or_ifvg")

        # Engulfing on 5m
        if len(candles_5m) >= 3 and method in ["engulfing", "engulfing_or_ifvg"]:
            c_prev = candles_5m[-2]
            c_curr = candles_5m[-1]

            if (c_curr["close"] > c_curr["open"] and c_prev["close"] < c_prev["open"]
                    and c_curr["close"] > c_prev["open"]
                    and c_curr["open"] < c_prev["close"]):
                return "engulfing_5m"

        # IFVG on 1m (requires 1m data)
        if candles_1m and method in ["ifvg", "engulfing_or_ifvg"]:
            confirmation = self._detect_ifvg(candles_1m, fvg)
            if confirmation:
                return confirmation

        return None

    def _detect_ifvg(self, candles_1m: list[dict], fvg_5m: dict) -> str | None:
        """Detect Inverse FVG on 1m that confirms the 5m FVG entry."""
        if len(candles_1m) < 5:
            return None

        # Look for rapid move creating imbalance on 1m
        # This is a simplified IFVG detection
        recent = candles_1m[-10:]

        for i in range(2, len(recent)):
            c_prev = recent[i - 1]
            c_curr = recent[i]

            # Bullish IFVG-like: strong impulsive move up
            if c_curr["low"] > c_prev["high"]:
                move_size = (c_curr["high"] - c_prev["low"]) / c_prev["low"]
                if move_size > 0.003:  # 0.3% move in one candle
                    # Check if this aligns with 5m FVG
                    if c_curr["low"] >= fvg_5m["bottom"]:
                        return "ifvg_1m_bullish"

        return None

    def _calculate_smc_sl(self, entry_price: float, fvg: dict) -> float:
        """Calculate SL below FVG bottom with buffer."""
        sl_base = fvg["bottom"]
        buffer = sl_base * self.config.get("risk.sl_buffer_pct", 0.002)
        return sl_base - buffer

    def _calculate_smc_tp(self, entry_price: float, sl_price: float) -> float:
        """Calculate TP with RR 1/2 or 1/3."""
        risk_distance = entry_price - sl_price
        rr = self.config.get("risk.rr_target", 0.5)
        return entry_price + (risk_distance * rr)

    def _calculate_position_size(self, entry_price: float, sl_price: float) -> float:
        """Calculate position size based on 0.5% risk."""
        risk_pct = self.config.get("risk.risk_pct_per_trade", 0.5)
        risk_amount = self.position_manager.capital * (risk_pct / 100)
        price_risk = entry_price - sl_price
        if price_risk <= 0:
            return 0
        size = risk_amount / price_risk
        return max(0, size)

    async def run_tick(self):
        """Paper trading tick with simulated fills."""
        if self._stopped:
            return

        market = self.config.get("market", "BTC/EUR")

        try:
            data = await self.fetch_all_timeframes()
        except Exception as e:
            logger.error(f"Data fetch failed: {e}")
            return

        candles_5m = data.get("main", [])
        candles_15m = data.get("trend_15m", [])
        candles_1h = data.get("trend_1h", [])
        candles_1m = data.get("confirmation_1m", [])

        if len(candles_5m) < 100:
            return

        try:
            current_price = await self.market_data.get_latest_price(market)
        except Exception as e:
            logger.error(f"Price fetch failed: {e}")
            return

        # Update and check open positions
        for trade_id, position in list(self.position_manager.open_positions.items()):
            self.position_manager.update_position_price(trade_id, current_price)

            exit_reason = self.check_exit_conditions(position, candles_5m, current_price)
            if exit_reason:
                logger.info(f"Closing {trade_id}: {exit_reason} @ {current_price:.2f}")
                self.position_manager.close_position(trade_id, current_price, exit_reason)
                self.trades.append({
                    "id": trade_id,
                    "type": "close",
                    "reason": exit_reason,
                    "price": current_price,
                    "timestamp": time.time(),
                })

        # Check for new entry
        if len(self.position_manager.open_positions) < self.config.get("entry.max_open_positions", 1):
            if time.time() - self.last_trade_time < self.config.get("entry.cooldown_seconds", 300):
                return

            signal = self.detect_entry_signal(candles_5m, candles_15m, candles_1h, candles_1m)
            if signal and signal["position_size"] > 0:
                trade_id = str(uuid.uuid4())

                # Simulate fill at current price (paper trading)
                fill_price = current_price
                fill_size = signal["position_size"]

                position = self.position_manager.open_position(
                    trade_id=trade_id,
                    asset=market,
                    side="long",
                    entry_price=fill_price,
                    position_size=fill_size,
                    sl_price=signal["sl_price"],
                    tp_price=signal["tp_price"],
                    strategy_info={
                        "fvg_bottom": signal["fvg"]["bottom"],
                        "fvg_top": signal["fvg"]["top"],
                        "confirmation": signal["confirmation"],
                        "trend": signal["trend_info"]["overall"],
                        "paper_trade": True,
                    },
                )

                self.last_trade_time = time.time()
                self.simulated_fills.append({
                    "id": trade_id,
                    "fill_price": fill_price,
                    "fill_size": fill_size,
                    "timestamp": time.time(),
                })

                self.trades.append({
                    "id": trade_id,
                    "type": "open",
                    "entry_price": fill_price,
                    "position_size": fill_size,
                    "sl_price": signal["sl_price"],
                    "tp_price": signal["tp_price"],
                    "confirmation": signal["confirmation"],
                    "timestamp": time.time(),
                })

                logger.info(
                    f"PAPER TRADE OPEN: {trade_id[:8]}... | "
                    f"Long {fill_size:.6f} BTC @ {fill_price:.2f} EUR | "
                    f"SL: {signal['sl_price']:.2f} | TP: {signal['tp_price']:.2f} | "
                    f"Risk: 0.5% | Confirmation: {signal['confirmation']}"
                )
