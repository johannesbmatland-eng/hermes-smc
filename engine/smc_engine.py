#!/usr/bin/env python3
"""SMC trading engine: FVG, BOS, HH/HL, order block detection + entry/execution."""

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import ccxt.async_support as ccxt
import numpy as np

from .core import MarketData, MarketStructureDetector, TrendAnalyzer

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# SMC Strategy Configuration
# ----------------------------------------------------------------------

DEFAULT_STRATEGY = {
    "market": "BTC/USD",
    "paper_trading": {
        "initial_capital": 100000,
        "currency": "USD",
    },
    "timeframes": {
        "main": "5m",       # primary trading timeframe
        "trend_1h": "1h",   # higher timeframe trend filter
        "trend_15m": "15m", # intermediate trend filter
    },
    "trend_filter": {
        "enabled": True,
        "method": "ema_and_structure",
        "ema_period": 50,
    },
    "fvq_detection": {
        "min_candles_since_fvg": 50,
        "fvg_buffer_pct": 0.001,
    },
    "entry": {
        "confirmation": "engulfing_or_ifvg",
        "pullback_depth_pct": 0.5,
        "max_open_positions": 1,
        "cooldown_seconds": 300,
    },
    "risk": {
        "risk_pct_per_trade": 0.5,
        "rr_target": 0.5,       # 1/2 RR
        "rr_alternative": 0.333,
        "sl_buffer_pct": 0.002,
    },
}


class SMCConfig:
    """Load and manage strategy configuration."""

    def __init__(self, config_path: Path | None = None):
        self._config = DEFAULT_STRATEGY.copy()
        if config_path and config_path.exists():
            import yaml
            with open(config_path) as f:
                user_config = yaml.safe_load(f)
            self._config = self._deep_merge(self._config, user_config or {})

    def _deep_merge(self, base: dict, override: dict) -> dict:
        """Deep merge two dicts."""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value using dot notation (e.g., 'risk.rr_target')."""
        keys = key.split(".")
        value = self._config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        return value if value is not None else default

    @property
    def config(self) -> dict:
        return self._config


class PositionManager:
    """Manage open positions and paper trading state."""

    def __init__(self, initial_capital: float = 100000, currency: str = "USD"):
        self.initial_capital = initial_capital
        self.currency = currency
        self.capital = initial_capital
        self.open_positions: dict[str, dict] = {}
        self.closed_positions: list[dict] = []
        self.trade_history: list[dict] = []

    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss_price: float,
        risk_pct: float = 0.5,
    ) -> float:
        """
        Calculate position size based on risk percentage.
        Risk = (entry - stop_loss) / entry * position_size
        position_size = risk_amount / (entry - stop_loss)
        """
        risk_amount = self.capital * (risk_pct / 100)
        price_risk = abs(entry_price - stop_loss_price)
        if price_risk <= 0:
            return 0
        size = risk_amount / price_risk
        return max(0, size)

    def calculate_sl_price(
        self,
        entry_price: float,
        fvg_bottom: float,
        sl_buffer_pct: float = 0.002,
        side: str = "long",
        fvg_top: float | None = None,
    ) -> float:
        """Calculate stop loss: below FVG bottom (long) or above FVG top (short)."""
        if side == "short":
            sl_base = fvg_top if fvg_top is not None else fvg_bottom
            sl_buffer = sl_base * sl_buffer_pct
            return sl_base + sl_buffer
        sl_base = fvg_bottom
        sl_buffer = sl_base * sl_buffer_pct
        return sl_base - sl_buffer

    def calculate_tp_price(
        self,
        entry_price: float,
        sl_price: float,
        rr_target: float = 0.5,
        side: str = "long",
    ) -> float:
        """Calculate take profit price based on RR target (mirrored for shorts)."""
        risk_distance = abs(entry_price - sl_price)
        reward_distance = risk_distance * rr_target
        if side == "short":
            return entry_price - reward_distance
        return entry_price + reward_distance

    def open_position(
        self,
        trade_id: str,
        asset: str,
        side: str,
        entry_price: float,
        position_size: float,
        sl_price: float,
        tp_price: float,
        strategy_info: dict,
    ) -> dict:
        """Open a new position."""
        position = {
            "id": trade_id,
            "asset": asset,
            "side": side,
            "entry_price": entry_price,
            "position_size": position_size,
            "entry_value": entry_price * position_size,
            "stop_loss": sl_price,
            "take_profit": tp_price,
            "open_time": time.time(),
            "strategy_info": strategy_info,
            "status": "open",
            "pnl": 0,
            "pnl_pct": 0,
            "current_price": entry_price,
        }
        self.open_positions[trade_id] = position
        self.capital -= entry_price * position_size
        return position

    def close_position(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str = "manual",
    ) -> dict | None:
        """Close an open position and record results."""
        if trade_id not in self.open_positions:
            return None

        position = self.open_positions[trade_id]
        entry = position["entry_price"]
        size = position["position_size"]
        side = position["side"]

        # Calculate PnL
        if side == "long":
            pnl = (exit_price - entry) * size
            pnl_pct = (exit_price - entry) / entry * 100
        else:
            pnl = (entry - exit_price) * size
            pnl_pct = (entry - exit_price) / entry * 100

        position["exit_price"] = exit_price
        position["exit_time"] = time.time()
        position["exit_reason"] = exit_reason
        position["pnl"] = pnl
        position["pnl_pct"] = pnl_pct
        position["status"] = "closed"

        # Return reserved notional + PnL (works for long and short)
        self.capital += position["entry_value"] + pnl

        # Move to closed
        self.closed_positions.append(position)
        self.trade_history.append(position)
        del self.open_positions[trade_id]

        logger.info(
            f"Closed position {trade_id}: {side} {size:.6f} @ {entry:.2f} → "
            f"{exit_price:.2f} | PnL: {pnl:+.2f} ({pnl_pct:+.2f}%) [{exit_reason}]"
        )
        return position

    def update_position_price(self, trade_id: str, current_price: float):
        """Update current price for an open position (for monitoring)."""
        if trade_id in self.open_positions:
            self.open_positions[trade_id]["current_price"] = current_price
            position = self.open_positions[trade_id]
            entry = position["entry_price"]
            size = position["position_size"]
            side = position["side"]

            if side == "long":
                pnl_pct = (current_price - entry) / entry * 100
            else:
                pnl_pct = (entry - current_price) / entry * 100

            position["pnl_pct"] = pnl_pct


class SMCEngine:
    """Main SMC trading engine with FVG/BOS/structure detection."""

    def __init__(self, config: SMCConfig | None = None):
        self.config = config or SMCConfig()
        self.market_data = MarketData()
        self.position_manager = PositionManager(
            initial_capital=self.config.get("paper_trading.initial_capital", 100000),
            currency=self.config.get("paper_trading.currency", "USD"),
        )
        self.trades: list[dict] = []
        self.last_trade_time: float = 0
        self._running = False
        self._stopped = False

    async def fetch_all_timeframes(self) -> dict[str, list[dict]]:
        """Fetch candles for all required timeframes."""
        market = self.config.get("market", "BTC/USD")
        timeframes = self.config.get("timeframes", {})

        tasks = []
        for name, tf in timeframes.items():
            limit = 500 if name == "main" else 200
            tasks.append(self.market_data.fetch_candles(market, tf, limit))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            "main": results[0] if not isinstance(results[0], Exception) else [],
            "trend_1h": results[1] if len(results) > 1 and not isinstance(results[1], Exception) else [],
            "trend_15m": results[2] if len(results) > 2 and not isinstance(results[2], Exception) else [],
        }

    def detect_entry_signal(
        self,
        candles_5m: list[dict],
        candles_15m: list[dict],
        candles_1h: list[dict],
    ) -> dict | None:
        """
        Detect SMC entry signal (long or short):
        unmitigated FVG + pullback + confirmation, filtered by trend.
        """
        # Detect FVG on 5m
        fvgs = MarketStructureDetector.detect_fvg(candles_5m)

        # Check trend filter
        trend_result = TrendAnalyzer.analyze_trend(
            candles_5m, candles_15m, candles_1h,
            {"trend_filter": self.config.get("trend_filter", {})},
        )

        # Prefer long in uptrend, short in downtrend (mirrored setup)
        side = None
        unmitigated_fvgs: list[dict] = []
        if trend_result.get("trend_filter_pass_long", False):
            side = "long"
            unmitigated_fvgs = [f for f in fvgs if f["unmitigated"] and f["type"] == "bullish"]
        elif trend_result.get("trend_filter_pass_short", False):
            side = "short"
            unmitigated_fvgs = [f for f in fvgs if f["unmitigated"] and f["type"] == "bearish"]
        else:
            logger.debug("Trend filter not passed, skipping entry")
            return None

        if not unmitigated_fvgs:
            return None

        # Find best FVG for entry (most recently formed, unmitigated)
        best_fvg = None
        for fvg in unmitigated_fvgs:
            candles_since = len(candles_5m) - fvg["end_candle"]
            if candles_since > self.config.get("fvq_detection.min_candles_since_fvg", 50):
                continue

            current_price = candles_5m[-1]["close"]
            fvg_top = fvg["top"]
            fvg_bottom = fvg["bottom"]
            fvg_mid = fvg["mid"]

            pullback_threshold = (fvg_top - fvg_bottom) * self.config.get("entry.pullback_depth_pct", 0.5)

            if fvg_bottom <= current_price <= fvg_top:
                best_fvg = fvg
                break
            elif abs(current_price - fvg_mid) <= pullback_threshold:
                best_fvg = fvg
                break

        if not best_fvg:
            return None

        confirmation = self._check_confirmation(candles_5m, best_fvg, side=side)
        if not confirmation:
            return None

        entry_price = candles_5m[-1]["close"]
        sl_price = self.position_manager.calculate_sl_price(
            entry_price,
            best_fvg["bottom"],
            self.config.get("risk.sl_buffer_pct", 0.002),
            side=side,
            fvg_top=best_fvg["top"],
        )
        tp_price = self.position_manager.calculate_tp_price(
            entry_price,
            sl_price,
            self.config.get("risk.rr_target", 0.5),
            side=side,
        )

        position_size = self.position_manager.calculate_position_size(
            entry_price,
            sl_price,
            self.config.get("risk.risk_pct_per_trade", 0.5),
        )

        logger.info(
            f"Entry signal ({side}): FVG at {best_fvg['bottom']:.2f}-{best_fvg['top']:.2f}, "
            f"entry @ {entry_price:.2f}, SL @ {sl_price:.2f}, TP @ {tp_price:.2f}, "
            f"size: {position_size:.6f} BTC"
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

    def _check_confirmation(
        self,
        candles_5m: list[dict],
        fvg: dict,
        side: str = "long",
    ) -> str | None:
        """
        Check for entry confirmation (mirrored for shorts):
        - Engulfing candle on 5m
        - IFVG-like structure on 5m
        """
        confirmation_method = self.config.get("entry.confirmation", "engulfing_or_ifvg")

        if len(candles_5m) >= 3:
            c1 = candles_5m[-2]
            c2 = candles_5m[-1]

            if confirmation_method in ["engulfing", "engulfing_or_ifvg"]:
                if side == "long":
                    # Bullish engulfing
                    if c2["close"] > c2["open"] and c1["close"] < c1["open"]:
                        if c2["close"] > c1["open"] and c2["open"] < c1["close"]:
                            return "engulfing_5m"
                else:
                    # Bearish engulfing
                    if c2["close"] < c2["open"] and c1["close"] > c1["open"]:
                        if c2["close"] < c1["open"] and c2["open"] > c1["close"]:
                            return "engulfing_5m"

        if confirmation_method in ["ifvg", "engulfing_or_ifvg"]:
            if len(candles_5m) >= 3:
                c0 = candles_5m[-3]
                c1 = candles_5m[-2]
                c2 = candles_5m[-1]

                if side == "long":
                    if c1["high"] > c0["high"] and c2["low"] > c1["low"]:
                        if c1["high"] - c0["low"] > (c0["high"] - c0["low"]) * 2:
                            return "ifvg_5m"
                else:
                    if c1["low"] < c0["low"] and c2["high"] < c1["high"]:
                        if c0["high"] - c1["low"] > (c0["high"] - c0["low"]) * 2:
                            return "ifvg_5m"

        return None

    def check_exit_conditions(
        self,
        position: dict,
        candles_5m: list[dict],
        current_price: float,
    ) -> str | None:
        """
        Check if position should be closed:
        - SL hit
        - TP hit
        - RSI overbought/oversold
        - Structure break
        """
        entry_price = position["entry_price"]
        sl_price = position["stop_loss"]
        tp_price = position["take_profit"]
        side = position["side"]

        # Check stop loss
        if side == "long" and current_price <= sl_price:
            return "stop_loss"
        elif side == "short" and current_price >= sl_price:
            return "stop_loss"

        # Check take profit
        if side == "long" and current_price >= tp_price:
            return "take_profit"
        elif side == "short" and current_price <= tp_price:
            return "take_profit"

        # Check market structure break (BOS against us)
        if len(candles_5m) >= 10:
            if side == "long":
                recent_lows = [c["low"] for c in candles_5m[-10:-1]]
                if recent_lows and min(recent_lows) < entry_price * 0.995:
                    return "structure_break"
            else:
                recent_highs = [c["high"] for c in candles_5m[-10:-1]]
                if recent_highs and max(recent_highs) > entry_price * 1.005:
                    return "structure_break"

        return None

    async def run_tick(self):
        """Execute one tick of the trading engine."""
        if self._stopped:
            return

        market = self.config.get("market", "BTC/USD")

        # Fetch all timeframe data
        try:
            data = await self.fetch_all_timeframes()
        except Exception as e:
            logger.error(f"Failed to fetch data: {e}")
            return

        candles_5m = data.get("main", [])
        candles_15m = data.get("trend_15m", [])
        candles_1h = data.get("trend_1h", [])

        if len(candles_5m) < 100:
            logger.debug("Not enough 5m data for analysis")
            return

        # Get current price
        try:
            current_price = await self.market_data.get_latest_price(market)
        except Exception as e:
            logger.error(f"Failed to get current price: {e}")
            return

        # Check open positions
        for trade_id, position in list(self.position_manager.open_positions.items()):
            # Update position with current price
            self.position_manager.update_position_price(trade_id, current_price)

            # Check exit conditions
            exit_reason = self.check_exit_conditions(position, candles_5m, current_price)
            if exit_reason:
                logger.info(f"Closing position {trade_id}: {exit_reason}")
                self.position_manager.close_position(trade_id, current_price, exit_reason)
                self.trades.append({
                    "id": trade_id,
                    "type": "close",
                    "reason": exit_reason,
                    "timestamp": time.time(),
                })

        # Check for new entry (if we have capacity)
        if len(self.position_manager.open_positions) < self.config.get("entry.max_open_positions", 1):
            # Check cooldown
            if time.time() - self.last_trade_time < self.config.get("entry.cooldown_seconds", 300):
                return

            signal = self.detect_entry_signal(candles_5m, candles_15m, candles_1h)
            if signal and signal["position_size"] > 0:
                trade_id = str(uuid.uuid4())
                side = signal.get("side", "long")
                position = self.position_manager.open_position(
                    trade_id=trade_id,
                    asset=market,
                    side=side,
                    entry_price=signal["entry_price"],
                    position_size=signal["position_size"],
                    sl_price=signal["sl_price"],
                    tp_price=signal["tp_price"],
                    strategy_info={
                        "fvg_bottom": signal["fvg"]["bottom"],
                        "fvg_top": signal["fvg"]["top"],
                        "confirmation": signal["confirmation"],
                        "trend": signal["trend_info"]["overall"],
                        "side": side,
                    },
                )

                self.last_trade_time = time.time()
                self.trades.append({
                    "id": trade_id,
                    "type": "open",
                    "side": side,
                    "entry_price": signal["entry_price"],
                    "position_size": signal["position_size"],
                    "sl_price": signal["sl_price"],
                    "tp_price": signal["tp_price"],
                    "timestamp": time.time(),
                })

                logger.info(
                    f"Opened {side} position {trade_id}: {signal['position_size']:.6f} BTC @ "
                    f"{signal['entry_price']:.2f} USD | SL: {signal['sl_price']:.2f} | "
                    f"TP: {signal['tp_price']:.2f} | Size: {signal['position_size']:.6f}"
                )

    async def run(self, interval_seconds: int = 10):
        """Run the trading engine continuously."""
        self._running = True
        logger.info(f"Starting SMC engine with {self.position_manager.capital:.2f} {self.position_manager.currency} capital")

        while self._running and not self._stopped:
            try:
                await self.run_tick()
            except Exception as e:
                logger.error(f"Tick failed: {e}")

            await asyncio.sleep(interval_seconds)

    def stop(self):
        """Stop the engine."""
        self._running = False
        self._stopped = True
        logger.info("SMC engine stopped")
