"""Paper trading simulator for SMC engine testing."""

import asyncio
import logging
import time
import uuid
from typing import Any

from .core import MarketStructureDetector
from .smc_engine import SMCEngine, SMCConfig, PositionManager
from .analytics import build_entry_context

logger = logging.getLogger(__name__)


class PaperTradingEngine(SMCEngine):
    """Paper trading version of SMC engine with simulated fills."""

    def __init__(self, config: SMCConfig | None = None):
        super().__init__(config)
        self.paper_mode = True
        self.simulated_fills: list[dict] = []
        self.last_analysis: dict[str, Any] = {}
        self.last_candles_5m: list[dict] = []
        self.last_ema_5m: list[dict] = []
        self.last_price: float | None = None
        self.last_fvg_boxes: list[dict] = []

    async def fetch_all_timeframes(self) -> dict[str, list[dict]]:
        """Fetch candles including 1m for confirmation signals."""
        market = self.config.get("market", "BTC/USD")

        tasks = [
            self.market_data.fetch_candles(market, "5m", 500),
            self.market_data.fetch_candles(market, "15m", 200),
            self.market_data.fetch_candles(market, "1h", 200),
            self.market_data.fetch_candles(market, "1m", 300),
        ]

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
        Entry rule (long example):
        1) Trend OK + unmitigated bullish FVG
        2) Bear candle touches the FVG
        3) Next candle locks as a larger bull engulfing candle
        4) Enter at locked candle close; SL under FVG; TP at 1:2 RR (0.5%→1%)
        """
        if len(candles_5m) < 3:
            return None

        fvgs = self._detect_fvg_full(candles_5m)
        # Only trade FVGs that have fully closed (exclude forming candle)
        last_closed_idx = len(candles_5m) - 2
        fvgs = [f for f in fvgs if f["end_candle"] <= last_closed_idx]
        trend_result = self._analyze_trend_full(candles_5m, candles_15m, candles_1h)

        side = None
        candidate_fvgs: list[dict] = []
        if trend_result.get("trend_filter_pass_long", False):
            side = "long"
            candidate_fvgs = [f for f in fvgs if f["unmitigated"] and f["type"] == "bullish"]
        elif trend_result.get("trend_filter_pass_short", False):
            side = "short"
            candidate_fvgs = [f for f in fvgs if f["unmitigated"] and f["type"] == "bearish"]
        else:
            logger.info(
                "Skip entry: trend filter not passed "
                f"(overall={trend_result.get('overall')})"
            )
            return None

        rsi = self._calc_rsi(candles_5m, int(self.config.get("rsi.period", 14)))
        if not self._rsi_allows_side(side, rsi):
            long_max = self.config.get("rsi.long_max", 65)
            short_min = self.config.get("rsi.short_min", 35)
            logger.info(
                f"Skip entry: RSI filter blocked {side} "
                f"(rsi={rsi if rsi is not None else 'n/a'}, "
                f"long_max={long_max}, short_min={short_min})"
            )
            return None

        if not candidate_fvgs:
            logger.debug("Skip entry: no unmitigated FVGs for side=%s", side)
            return None

        min_age = self.config.get("fvq_detection.min_candles_since_fvg", 50)
        recent = []
        for f in candidate_fvgs:
            age = len(candles_5m) - f["end_candle"]
            if age <= min_age:
                recent.append({**f, "age_candles": age})
        if not recent:
            return None

        # Prefer most recent FVG that the locked bear/bull touch+engulf setup confirms
        recent.sort(key=lambda f: f.get("age_candles", 999))
        best_fvg = None
        confirmation = None
        for fvg in recent:
            confirmation = self._check_smc_confirmation(
                candles_5m, candles_1m, fvg, side=side
            )
            if confirmation:
                best_fvg = fvg
                break

        if not best_fvg or not confirmation:
            logger.debug("No confirmation signal")
            return None

        # Enter at the locked confirmation candle close (not the forming candle)
        entry_price = candles_5m[-2]["close"]
        sl_price = self._calculate_smc_sl(entry_price, best_fvg, side=side)
        tp_price = self._calculate_smc_tp(entry_price, sl_price, side=side)
        position_size = self._calculate_position_size(entry_price, sl_price)

        if position_size <= 0:
            logger.warning(
                "Skip entry: position_size=0 (capital=%.2f). Check equity accounting.",
                self.position_manager.capital,
            )
            return None

        tp_label = f"{tp_price:.2f}" if tp_price is not None else "none (trail)"
        logger.info(
            f"SMC Entry Signal ({side}): FVG [{best_fvg['bottom']:.2f}-{best_fvg['top']:.2f}], "
            f"entry @ {entry_price:.2f}, SL @ {sl_price:.2f}, TP @ {tp_label}, "
            f"size: {position_size:.6f} BTC, confirmation: {confirmation}"
        )

        return {
            "type": "entry",
            "side": side,
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
        """Detect 3-candle FVGs (same ICT definition as MarketStructureDetector)."""
        fvgs = MarketStructureDetector.detect_fvg(candles)
        for fvg in fvgs:
            if fvg["bottom"] > 0:
                fvg["size_pct"] = (fvg["top"] - fvg["bottom"]) / fvg["bottom"] * 100
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
        result: dict[str, Any] = {
            "trend_5m": "neutral",
            "trend_15m": "neutral",
            "trend_1h": "neutral",
            "overall": "neutral",
            "details": {},
        }

        ema_period = self.config.get("trend_filter.ema_period", 50)

        ema_5m = self._calc_ema(candles_5m, ema_period)
        ema_15m = self._calc_ema(candles_15m, ema_period)
        ema_1h = self._calc_ema(candles_1h, ema_period)

        result["details"]["ema_5m"] = ema_5m
        result["details"]["ema_15m"] = ema_15m
        result["details"]["ema_1h"] = ema_1h

        if ema_5m:
            price_5m = candles_5m[-1]["close"]
            result["trend_5m"] = "bullish" if price_5m > ema_5m else "bearish"

        if ema_15m:
            price_15m = candles_15m[-1]["close"]
            result["trend_15m"] = "bullish" if price_15m > ema_15m else "bearish"

        if ema_1h:
            price_1h = candles_1h[-1]["close"]
            result["trend_1h"] = "bullish" if price_1h > ema_1h else "bearish"

        struct_5m = self._analyze_structure(candles_5m)
        struct_15m = self._analyze_structure(candles_15m)
        struct_1h = self._analyze_structure(candles_1h)

        result["details"]["structure_5m"] = struct_5m
        result["details"]["structure_15m"] = struct_15m
        result["details"]["structure_1h"] = struct_1h

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

        confirmed_uptrend = (
            (struct_1h.get("hh_count", 0) > 0 and struct_1h.get("hl_count", 0) > 0)
            or (struct_15m.get("hh_count", 0) > 0 and struct_15m.get("hl_count", 0) > 0)
        )
        confirmed_downtrend = (
            (struct_1h.get("lh_count", 0) > 0 and struct_1h.get("ll_count", 0) > 0)
            or (struct_15m.get("lh_count", 0) > 0 and struct_15m.get("ll_count", 0) > 0)
        )
        result["details"]["confirmed_uptrend"] = confirmed_uptrend
        result["details"]["confirmed_downtrend"] = confirmed_downtrend

        if self.config.get("trend_filter.enabled", True):
            method = self.config.get("trend_filter.method", "ema_majority")
            if method == "ema_and_structure":
                result["trend_filter_pass_long"] = (
                    result["overall"] == "bullish" and confirmed_uptrend
                )
                result["trend_filter_pass_short"] = (
                    result["overall"] == "bearish" and confirmed_downtrend
                )
            else:
                # ema_majority: allow trades when EMA bias is clear
                result["trend_filter_pass_long"] = result["overall"] == "bullish"
                result["trend_filter_pass_short"] = result["overall"] == "bearish"
            result["trend_filter_pass"] = (
                result["trend_filter_pass_long"] or result["trend_filter_pass_short"]
            )
        else:
            result["trend_filter_pass_long"] = True
            result["trend_filter_pass_short"] = True
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

    def _calc_rsi(self, candles: list[dict], period: int = 14) -> float | None:
        """Wilder RSI on closed candles (exclude still-forming last bar)."""
        if len(candles) < period + 2:
            return None
        closes = [c["close"] for c in candles[:-1]]  # locked closes only
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _rsi_allows_side(self, side: str, rsi: float | None) -> bool:
        """No long if RSI > long_max; no short if RSI < short_min."""
        if not self.config.get("rsi.enabled", True):
            return True
        if rsi is None:
            return False
        if side == "long":
            return rsi <= float(self.config.get("rsi.long_max", 65))
        if side == "short":
            return rsi >= float(self.config.get("rsi.short_min", 35))
        return False

    def _analyze_structure(self, candles: list[dict]) -> dict:
        """Analyze market structure: BOS, HH/HL (uptrend) and LH/LL (downtrend)."""
        if len(candles) < 20:
            return {
                "bos_count": 0,
                "hh_count": 0,
                "hl_count": 0,
                "lh_count": 0,
                "ll_count": 0,
            }

        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]

        hh_list = []
        hl_list = []
        lh_list = []
        ll_list = []
        bos_list = []

        last_swing_high = None
        last_swing_low = None
        last_hh = None
        last_ll = None

        for i in range(2, len(candles) - 2):
            is_swing_high = (
                highs[i] > highs[i - 1]
                and highs[i] > highs[i + 1]
                and highs[i] > highs[i - 2]
                and highs[i] > highs[i + 2]
            )
            is_swing_low = (
                lows[i] < lows[i - 1]
                and lows[i] < lows[i + 1]
                and lows[i] < lows[i - 2]
                and lows[i] < lows[i + 2]
            )

            if is_swing_high:
                if last_swing_high is None or highs[i] > last_swing_high:
                    hh_list.append({"index": i, "price": highs[i]})
                    last_hh = highs[i]
                else:
                    lh_list.append({"index": i, "price": highs[i]})
                last_swing_high = highs[i]

            if is_swing_low:
                if last_swing_low is None or lows[i] < last_swing_low:
                    ll_list.append({"index": i, "price": lows[i]})
                    last_ll = lows[i]
                else:
                    hl_list.append({"index": i, "price": lows[i]})
                last_swing_low = lows[i]

            if last_hh and highs[i] > last_hh * 1.0005:
                bos_list.append({"index": i, "type": "bullish", "price": highs[i]})
                last_hh = highs[i]

            if last_ll and lows[i] < last_ll * 0.9995:
                bos_list.append({"index": i, "type": "bearish", "price": lows[i]})
                last_ll = lows[i]

        return {
            "bos_count": len(bos_list),
            "hh_count": len(hh_list),
            "hl_count": len(hl_list),
            "lh_count": len(lh_list),
            "ll_count": len(ll_list),
            "latest_hh": hh_list[-1] if hh_list else None,
            "latest_hl": hl_list[-1] if hl_list else None,
            "latest_lh": lh_list[-1] if lh_list else None,
            "latest_ll": ll_list[-1] if ll_list else None,
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

            fvg_mid = fvg["mid"]
            distance = abs(current_price - fvg_mid) / fvg_mid

            if fvg["bottom"] <= current_price <= fvg["top"]:
                score = 1.0 - distance * 2
            elif abs(current_price - fvg["bottom"]) / fvg["bottom"] < 0.005:
                score = 0.8
            elif abs(current_price - fvg["top"]) / fvg["top"] < 0.005:
                score = 0.6
            else:
                continue

            if score > best_score:
                best_score = score
                best = fvg

        return best

    def _candle_touches_fvg(self, candle: dict, fvg: dict) -> bool:
        """True if candle wick overlaps the FVG zone."""
        return candle["low"] <= fvg["top"] and candle["high"] >= fvg["bottom"]

    def _is_body_engulfing(self, prev: dict, curr: dict, side: str) -> bool:
        """
        FVG-touch candle (any color) engulfed by a larger directional candle.

        Long: curr must be bullish, larger body, and cover prev body.
        Short: curr must be bearish, larger body, and cover prev body.
        Touch candle color does not matter.
        Crypto-friendly: open may equal previous close.
        """
        prev_top = max(prev["open"], prev["close"])
        prev_bot = min(prev["open"], prev["close"])
        curr_top = max(curr["open"], curr["close"])
        curr_bot = min(curr["open"], curr["close"])
        prev_body = abs(prev["close"] - prev["open"])
        curr_body = abs(curr["close"] - curr["open"])

        if side == "long":
            return (
                curr["close"] > curr["open"]
                and curr_body > prev_body
                and curr_top > prev_top
                and curr_bot <= prev_bot
            )
        return (
            curr["close"] < curr["open"]
            and curr_body > prev_body
            and curr_bot < prev_bot
            and curr_top >= prev_top
        )

    def _check_smc_confirmation(
        self,
        candles_5m: list[dict],
        candles_1m: list[dict] | None,
        fvg: dict,
        side: str = "long",
    ) -> str | None:
        """
        Confirmation after FVG touch:
        - Skip still-forming candle[-1]; last closed is the potential engulf lock
        - Any candle that touches the FVG
        - 1st or 2nd following closed candle engulfs that touch (bull for long / bear for short)
        """
        method = self.config.get("entry.confirmation", "engulfing_or_ifvg")
        lookback = int(self.config.get("entry.engulf_lookback", 2) or 2)

        if len(candles_5m) >= 3 and method in ["engulfing", "engulfing_or_ifvg"]:
            locked_idx = len(candles_5m) - 2  # last fully closed candle
            confirm = candles_5m[locked_idx]
            # Touch may be 1..lookback candles before the locked engulf
            for offset in range(1, lookback + 1):
                touch_idx = locked_idx - offset
                if touch_idx < 0:
                    continue
                # FVG must be fully formed before the touch candle
                if fvg.get("end_candle", -1) >= touch_idx:
                    continue
                touch = candles_5m[touch_idx]
                if self._candle_touches_fvg(touch, fvg) and self._is_body_engulfing(
                    touch, confirm, side
                ):
                    return "engulfing_5m"

        if candles_1m and method in ["ifvg", "engulfing_or_ifvg"]:
            confirmation = self._detect_ifvg(candles_1m, fvg, side=side)
            if confirmation:
                return confirmation

        return None

    def _detect_ifvg(
        self,
        candles_1m: list[dict],
        fvg_5m: dict,
        side: str = "long",
    ) -> str | None:
        """Detect Inverse FVG on 1m that confirms the 5m FVG entry."""
        if len(candles_1m) < 5:
            return None

        recent = candles_1m[-10:]

        for i in range(2, len(recent)):
            c_first = recent[i - 2]
            c_last = recent[i]

            if side == "long":
                # Bullish 3-candle IFVG: last low does not overlap first high
                if c_last["low"] > c_first["high"]:
                    move_size = (c_last["low"] - c_first["high"]) / c_first["high"]
                    if move_size > 0.003 and c_last["low"] >= fvg_5m["bottom"]:
                        return "ifvg_1m_bullish"
            else:
                # Bearish 3-candle IFVG: last high does not overlap first low
                if c_last["high"] < c_first["low"]:
                    move_size = (c_first["low"] - c_last["high"]) / c_first["low"]
                    if move_size > 0.003 and c_last["high"] <= fvg_5m["top"]:
                        return "ifvg_1m_bearish"

        return None

    def _calculate_smc_sl(self, entry_price: float, fvg: dict, side: str = "long") -> float:
        """
        SL just beyond the FVG *zone* (gap edge), not under/over the full
        first candle that helped form the FVG.

        Bullish FVG bottom = first.high (gap edge) → SL slightly below that.
        Bearish FVG top = first.low (gap edge) → SL slightly above that.
        """
        buffer_pct = self.config.get("risk.sl_buffer_pct", 0.0003)
        if side == "short":
            # Protect above the FVG zone top (gap edge), never first-candle high
            sl_base = float(fvg["top"])
            return sl_base * (1 + buffer_pct)
        sl_base = float(fvg["bottom"])
        return sl_base * (1 - buffer_pct)

    def _calculate_smc_tp(
        self,
        entry_price: float,
        sl_price: float,
        side: str = "long",
    ) -> float | None:
        """
        TP price: in be_trail mode use exits.tp_rr (default 1:5 hard cap).
        trail_only with tp_rr=0 → no hard TP.
        """
        mode = self.config.get("exits.mode", "fixed_tp")
        if mode in ("be_trail", "trail_only"):
            rr = float(self.config.get("exits.tp_rr", 5.0) or 0)
            if rr <= 0:
                return None
        else:
            rr = self.config.get("risk.rr_target", 2.0)
        risk_distance = abs(entry_price - sl_price)
        if side == "short":
            return entry_price - (risk_distance * rr)
        return entry_price + (risk_distance * rr)

    def _calculate_position_size(self, entry_price: float, sl_price: float) -> float:
        """Calculate position size based on risk % of realized equity."""
        risk_pct = self.config.get("risk.risk_pct_per_trade", 0.5)
        return self.position_manager.calculate_position_size(
            entry_price, sl_price, risk_pct=risk_pct
        )

    def _ema_series(self, candles: list[dict], period: int) -> list[dict]:
        """Build EMA series aligned to candle timestamps (for chart)."""
        if len(candles) < period:
            return []
        closes = [c["close"] for c in candles]
        k = 2 / (period + 1)
        ema = sum(closes[:period]) / period
        series = [{"time": candles[period - 1]["timestamp"], "value": ema}]
        for i in range(period, len(candles)):
            ema = closes[i] * k + ema * (1 - k)
            series.append({"time": candles[i]["timestamp"], "value": ema})
        return series

    def build_analysis_snapshot(
        self,
        candles_5m: list[dict],
        candles_15m: list[dict],
        candles_1h: list[dict],
        candles_1m: list[dict] | None,
        current_price: float,
    ) -> dict[str, Any]:
        """
        Snapshot of what the bot currently sees / waits for.
        Used by the dashboard "bot thinking" panel.
        """
        market = self.config.get("market", "BTC/USD")
        ema_period = self.config.get("trend_filter.ema_period", 50)
        trend = self._analyze_trend_full(candles_5m, candles_15m, candles_1h)
        fvgs = self._detect_fvg_full(candles_5m)
        min_age = self.config.get("fvq_detection.min_candles_since_fvg", 50)

        def _recent_unmitigated(fvg_type: str) -> list[dict]:
            out = []
            for f in fvgs:
                if not f["unmitigated"] or f["type"] != fvg_type:
                    continue
                age = len(candles_5m) - f["end_candle"]
                if age <= min_age:
                    out.append({**f, "age_candles": age})
            return out

        bullish_fvgs = _recent_unmitigated("bullish")
        bearish_fvgs = _recent_unmitigated("bearish")

        bias = "neutral"
        if trend.get("trend_filter_pass_long"):
            bias = "long"
        elif trend.get("trend_filter_pass_short"):
            bias = "short"

        side = bias if bias in ("long", "short") else None
        candidate_fvgs = bullish_fvgs if side == "long" else bearish_fvgs if side == "short" else []
        nearest_fvg = self._find_best_fvg(candidate_fvgs, candles_5m) if candidate_fvgs else None
        if nearest_fvg is None and candidate_fvgs:
            # Fall back to most recent FVG even if price is not near it yet
            nearest_fvg = min(candidate_fvgs, key=lambda f: f.get("age_candles", 999))

        price_in_fvg = False
        confirmation = None
        lookback = int(self.config.get("entry.engulf_lookback", 2) or 2)
        locked = candles_5m[-2] if len(candles_5m) >= 2 else None
        # Any recent closed candle in the engulf window that touched the FVG
        recent_touches = []
        if len(candles_5m) >= 3:
            locked_idx = len(candles_5m) - 2
            for offset in range(1, lookback + 1):
                idx = locked_idx - offset
                if idx >= 0:
                    recent_touches.append(candles_5m[idx])
        touch = recent_touches[0] if recent_touches else None
        if nearest_fvg and side and locked:
            for fvg in ([nearest_fvg] + [f for f in candidate_fvgs if f is not nearest_fvg]):
                confirmation = self._check_smc_confirmation(
                    candles_5m, candles_1m, fvg, side=side
                )
                if confirmation:
                    nearest_fvg = fvg
                    break
            price_in_fvg = nearest_fvg["bottom"] <= current_price <= nearest_fvg["top"]
            for t in recent_touches + ([locked] if locked else []):
                if self._candle_touches_fvg(t, nearest_fvg):
                    price_in_fvg = True
                    touch = t
                    break
        elif nearest_fvg:
            price_in_fvg = nearest_fvg["bottom"] <= current_price <= nearest_fvg["top"]

        open_count = len(self.position_manager.open_positions)
        max_open = self.config.get("entry.max_open_positions", 1)
        cooldown = self.config.get("entry.cooldown_seconds", 300)
        cooldown_remaining = max(0, cooldown - (time.time() - self.last_trade_time))

        checklist = []
        waiting: list[str] = []

        # Capacity / cooldown
        if open_count >= max_open:
            checklist.append({
                "id": "capacity",
                "label": "Position capacity",
                "status": "fail",
                "detail": f"{open_count}/{max_open} open — managing existing trade",
            })
            waiting.append("Waiting for open position to close")
        elif cooldown_remaining > 0:
            checklist.append({
                "id": "capacity",
                "label": "Cooldown",
                "status": "wait",
                "detail": f"{int(cooldown_remaining)}s remaining after last trade",
            })
            waiting.append(f"Cooldown ({int(cooldown_remaining)}s)")
        else:
            checklist.append({
                "id": "capacity",
                "label": "Ready to trade",
                "status": "pass",
                "detail": f"{open_count}/{max_open} open positions",
            })

        # Trend / EMA
        ema_pass_long = trend.get("trend_filter_pass_long", False)
        ema_pass_short = trend.get("trend_filter_pass_short", False)
        if ema_pass_long:
            checklist.append({
                "id": "trend",
                "label": "Trend filter",
                "status": "pass",
                "detail": "Bullish — EMA majority aligned for longs",
            })
        elif ema_pass_short:
            checklist.append({
                "id": "trend",
                "label": "Trend filter",
                "status": "pass",
                "detail": "Bearish — EMA majority aligned for shorts",
            })
        else:
            checklist.append({
                "id": "trend",
                "label": "Trend filter",
                "status": "fail",
                "detail": f"Overall {trend.get('overall', 'neutral')} — need clear up/downtrend",
            })
            waiting.append("Waiting for clear trend (EMA + structure)")

        # RSI filter — no long if overbought, no short if oversold
        rsi_period = int(self.config.get("rsi.period", 14))
        rsi_val = self._calc_rsi(candles_5m, rsi_period)
        long_max = float(self.config.get("rsi.long_max", 65))
        short_min = float(self.config.get("rsi.short_min", 35))
        rsi_ok = self._rsi_allows_side(side, rsi_val) if side else False
        if not self.config.get("rsi.enabled", True):
            checklist.append({
                "id": "rsi",
                "label": "RSI filter",
                "status": "pass",
                "detail": "Disabled",
            })
        elif rsi_val is None:
            checklist.append({
                "id": "rsi",
                "label": "RSI filter",
                "status": "wait",
                "detail": f"Need more candles for RSI({rsi_period})",
            })
            waiting.append("Waiting for RSI")
        elif side == "long" and not rsi_ok:
            checklist.append({
                "id": "rsi",
                "label": "RSI filter",
                "status": "fail",
                "detail": f"RSI {rsi_val:.1f} > {long_max:.0f} — no longs when overbought",
            })
            waiting.append(f"RSI too high for long ({rsi_val:.1f})")
        elif side == "short" and not rsi_ok:
            checklist.append({
                "id": "rsi",
                "label": "RSI filter",
                "status": "fail",
                "detail": f"RSI {rsi_val:.1f} < {short_min:.0f} — no shorts when oversold",
            })
            waiting.append(f"RSI too low for short ({rsi_val:.1f})")
        else:
            checklist.append({
                "id": "rsi",
                "label": "RSI filter",
                "status": "pass" if side else "wait",
                "detail": (
                    f"RSI {rsi_val:.1f} OK for {side or 'flat'} "
                    f"(long≤{long_max:.0f} / short≥{short_min:.0f})"
                ),
            })

        for tf_key, label in [("5m", "EMA 5m"), ("15m", "EMA 15m"), ("1h", "EMA 1h")]:
            tf_trend = trend.get(f"trend_{tf_key}", "neutral")
            ema_val = trend.get("details", {}).get(f"ema_{tf_key}")
            status = "pass" if tf_trend == "bullish" else ("fail" if tf_trend == "bearish" else "wait")
            # For short bias, invert what "pass" means visually on checklist item color via detail
            detail = f"Price {'above' if tf_trend == 'bullish' else 'below' if tf_trend == 'bearish' else '≈'} EMA"
            if ema_val is not None:
                detail += f" ({ema_val:.2f})"
            checklist.append({
                "id": f"ema_{tf_key}",
                "label": label,
                "status": status,
                "detail": f"{tf_trend} · {detail}",
            })

        # FVG
        if side == "long":
            if not bullish_fvgs:
                checklist.append({
                    "id": "fvg",
                    "label": "Bullish FVG",
                    "status": "fail",
                    "detail": "No recent unmitigated bullish FVG",
                })
                waiting.append("Waiting for a fresh bullish FVG")
            elif not price_in_fvg:
                checklist.append({
                    "id": "fvg",
                    "label": "Pullback into FVG",
                    "status": "wait",
                    "detail": (
                        f"FVG {nearest_fvg['bottom']:.2f}–{nearest_fvg['top']:.2f} "
                        f"(age {nearest_fvg.get('age_candles', '?')} candles)"
                        if nearest_fvg else "FVG found but price not near it"
                    ),
                })
                waiting.append("Waiting for pullback into bullish FVG")
            else:
                checklist.append({
                    "id": "fvg",
                    "label": "Price in bullish FVG",
                    "status": "pass",
                    "detail": f"{nearest_fvg['bottom']:.2f}–{nearest_fvg['top']:.2f}",
                })
        elif side == "short":
            if not bearish_fvgs:
                checklist.append({
                    "id": "fvg",
                    "label": "Bearish FVG",
                    "status": "fail",
                    "detail": "No recent unmitigated bearish FVG",
                })
                waiting.append("Waiting for a fresh bearish FVG")
            elif not price_in_fvg:
                checklist.append({
                    "id": "fvg",
                    "label": "Rally into FVG",
                    "status": "wait",
                    "detail": (
                        f"FVG {nearest_fvg['bottom']:.2f}–{nearest_fvg['top']:.2f} "
                        f"(age {nearest_fvg.get('age_candles', '?')} candles)"
                        if nearest_fvg else "FVG found but price not near it"
                    ),
                })
                waiting.append("Waiting for rally into bearish FVG")
            else:
                checklist.append({
                    "id": "fvg",
                    "label": "Price in bearish FVG",
                    "status": "pass",
                    "detail": f"{nearest_fvg['bottom']:.2f}–{nearest_fvg['top']:.2f}",
                })
        else:
            checklist.append({
                "id": "fvg",
                "label": "FVG setup",
                "status": "wait",
                "detail": (
                    f"{len(bullish_fvgs)} bullish / {len(bearish_fvgs)} bearish "
                    "unmitigated (need trend first)"
                ),
            })

        # Confirmation
        if side and nearest_fvg:
            if confirmation:
                checklist.append({
                    "id": "confirmation",
                    "label": "Entry confirmation",
                    "status": "pass",
                    "detail": confirmation,
                })
            elif any(self._candle_touches_fvg(t, nearest_fvg) for t in recent_touches):
                checklist.append({
                    "id": "confirmation",
                    "label": "Entry confirmation",
                    "status": "wait",
                    "detail": f"FVG touched — waiting for engulf on next {lookback} candle(s)",
                })
                waiting.append(f"Waiting for engulf within {lookback} candles")
            else:
                checklist.append({
                    "id": "confirmation",
                    "label": "Entry confirmation",
                    "status": "wait",
                    "detail": f"Need FVG touch, then engulf within {lookback} candles",
                })
                waiting.append("Waiting for FVG-touch + engulf lock")
        else:
            checklist.append({
                "id": "confirmation",
                "label": "Entry confirmation",
                "status": "wait",
                "detail": "Armed after FVG touch + engulf lock",
            })

        if open_count > 0:
            phase = "Managing open position"
        elif (
            confirmation
            and side
            and rsi_ok
            and open_count < max_open
            and cooldown_remaining <= 0
        ):
            phase = f"Entry ready — {side.upper()}"
            waiting = [f"Ready to open {side}"]
        elif waiting:
            phase = waiting[0]
        else:
            phase = "Scanning market"

        def _fvg_public(f: dict | None) -> dict | None:
            if not f:
                return None
            return {
                "type": f["type"],
                "top": f["top"],
                "bottom": f["bottom"],
                "mid": f["mid"],
                "age_candles": f.get("age_candles"),
                "price_inside": f["bottom"] <= current_price <= f["top"],
            }

        chart_fvgs = []
        for f in (bullish_fvgs + bearish_fvgs)[:8]:
            chart_fvgs.append(_fvg_public(f))

        return {
            "market": market,
            "price": current_price,
            "bias": bias,
            "phase": phase,
            "waiting_for": waiting,
            "checklist": checklist,
            "ema": {
                "period": ema_period,
                "trend_5m": trend.get("trend_5m"),
                "trend_15m": trend.get("trend_15m"),
                "trend_1h": trend.get("trend_1h"),
                "overall": trend.get("overall"),
                "ema_5m": trend.get("details", {}).get("ema_5m"),
                "ema_15m": trend.get("details", {}).get("ema_15m"),
                "ema_1h": trend.get("details", {}).get("ema_1h"),
                "pass_long": ema_pass_long,
                "pass_short": ema_pass_short,
            },
            "rsi": {
                "period": rsi_period,
                "value": rsi_val,
                "long_max": long_max,
                "short_min": short_min,
                "allows_side": rsi_ok if side else None,
            },
            "structure": {
                "confirmed_uptrend": trend.get("details", {}).get("confirmed_uptrend"),
                "confirmed_downtrend": trend.get("details", {}).get("confirmed_downtrend"),
            },
            "fvgs": {
                "bullish_unmitigated": len(bullish_fvgs),
                "bearish_unmitigated": len(bearish_fvgs),
                "nearest": _fvg_public(nearest_fvg),
                "recent": chart_fvgs,
                "price_in_fvg": price_in_fvg,
            },
            "confirmation": confirmation,
            "open_positions": open_count,
            "cooldown_remaining": round(cooldown_remaining, 1),
            "updated_at": time.time(),
        }

    async def run_tick(self):
        """Paper trading tick with simulated fills."""
        if self._stopped:
            return

        market = self.config.get("market", "BTC/USD")

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
            self.last_analysis = {
                "market": market,
                "phase": "Warming up — need more candle history",
                "waiting_for": ["Waiting for enough 5m candles"],
                "checklist": [],
                "updated_at": time.time(),
            }
            return

        try:
            current_price = await self.market_data.get_latest_price(market)
        except Exception as e:
            logger.error(f"Price fetch failed: {e}")
            return

        self.last_price = current_price
        self.last_candles_5m = candles_5m[-150:]
        self.last_ema_5m = self._ema_series(
            candles_5m, self.config.get("trend_filter.ema_period", 50)
        )[-150:]
        # nephew_sam_-style FVG boxes for the visible chart window
        self.last_fvg_boxes = MarketStructureDetector.build_fvg_boxes(
            self.last_candles_5m,
            max_age_candles=self.config.get("fvq_detection.min_candles_since_fvg", 50),
            include_mitigated=True,
        )
        self.last_analysis = self.build_analysis_snapshot(
            candles_5m, candles_15m, candles_1h, candles_1m, current_price
        )

        for trade_id, position in list(self.position_manager.open_positions.items()):
            self.position_manager.update_position_price(trade_id, current_price)

            exit_reason = self.check_exit_conditions(position, candles_5m, current_price)
            if exit_reason:
                logger.info(f"Closing {trade_id}: {exit_reason} @ {current_price:.2f}")
                self.position_manager.close_position(trade_id, current_price, exit_reason)
                self.trades.append({
                    "id": trade_id,
                    "type": "close",
                    "side": position.get("side"),
                    "reason": exit_reason,
                    "price": current_price,
                    "timestamp": time.time(),
                })

        if len(self.position_manager.open_positions) >= self.config.get("entry.max_open_positions", 1):
            return

        if time.time() - self.last_trade_time < self.config.get("entry.cooldown_seconds", 300):
            return

        signal = self.detect_entry_signal(candles_5m, candles_15m, candles_1h, candles_1m)
        if signal and signal["position_size"] > 0:
            trade_id = str(uuid.uuid4())
            side = signal.get("side", "long")
            fill_price = signal["entry_price"]
            fill_size = signal["position_size"]
            ctx = build_entry_context(signal.get("trend_info"), signal.get("confirmation"))

            self.position_manager.open_position(
                trade_id=trade_id,
                asset=market,
                side=side,
                entry_price=fill_price,
                position_size=fill_size,
                sl_price=signal["sl_price"],
                tp_price=signal["tp_price"],
                strategy_info={
                    "fvg_bottom": signal["fvg"]["bottom"],
                    "fvg_top": signal["fvg"]["top"],
                    "confirmation": signal["confirmation"],
                    "trend": signal["trend_info"]["overall"],
                    "side": side,
                    "paper_trade": True,
                    **ctx,
                },
            )

            self.last_trade_time = time.time()
            self.simulated_fills.append({
                "id": trade_id,
                "side": side,
                "fill_price": fill_price,
                "fill_size": fill_size,
                "timestamp": time.time(),
            })

            self.trades.append({
                "id": trade_id,
                "type": "open",
                "side": side,
                "entry_price": fill_price,
                "position_size": fill_size,
                "sl_price": signal["sl_price"],
                "tp_price": signal["tp_price"],
                "confirmation": signal["confirmation"],
                "timestamp": time.time(),
            })

            self.last_analysis = self.build_analysis_snapshot(
                candles_5m, candles_15m, candles_1h, candles_1m, current_price
            )

            logger.info(
                f"PAPER TRADE OPEN: {trade_id[:8]}... | "
                f"{side.upper()} {fill_size:.6f} BTC @ {fill_price:.2f} USD | "
                f"SL: {signal['sl_price']:.2f} | TP: {signal['tp_price']:.2f} | "
                f"Risk: 0.5% | RR 1:2 | Confirmation: {signal['confirmation']}"
            )
