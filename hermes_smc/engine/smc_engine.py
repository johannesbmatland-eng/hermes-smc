#!/usr/bin/env python3
"""SMC trading engine: FVG, BOS, HH/HL, order block detection + entry/execution."""

import asyncio
import json
import logging
import os
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
        "method": "ema_majority",  # ema_majority | ema_and_structure
        "ema_period": 50,
    },
    "exits": {
        "structure_break": False,  # SL/TP only — avoid killing FVG pullback entries
        "mode": "be_trail",        # fixed_tp | be_trail | trail_only
        "be_at_rr": 2.0,
        "trail_after_be": True,
        "trail_rr": 1.0,
        "tp_rr": 5.0,
        "be_buffer_pct": 0.0001,
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
        "rr_target": 2.0,       # 1:2 RR → 0.5% risk makes 1%
        "rr_alternative": 3.0,
        "sl_buffer_pct": 0.002,
    },
}


class SMCConfig:
    """Load and manage strategy configuration."""

    def __init__(self, config_path: Path | None = None):
        import copy
        self._config = copy.deepcopy(DEFAULT_STRATEGY)
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

    def __init__(self, initial_capital: float = 100000, currency: str = "USD",
                 state_dir: Path | None = None):
        self.initial_capital = initial_capital
        self.currency = currency
        self.capital = initial_capital
        self.open_positions: dict[str, dict] = {}
        self.closed_positions: list[dict] = []
        self.trade_history: list[dict] = []
        self.state_dir = state_dir
        if self.state_dir:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self._load_state()

    @property
    def _state_file(self) -> Path | None:
        return self.state_dir / "state.json" if self.state_dir else None

    def _load_state(self):
        """Restore capital/positions/history from disk if present."""
        f = self._state_file
        if not f or not f.exists():
            return
        try:
            data = json.loads(f.read_text())
            self.capital = data.get("capital", self.capital)
            self.initial_capital = data.get("initial_capital", self.initial_capital)
            self.open_positions = data.get("open_positions", {})
            self.closed_positions = data.get("closed_positions", [])
            self.trade_history = data.get("trade_history", [])
            self._repair_capital_accounting()
            logger.info(
                f"Restored state: capital={self.capital:.2f} "
                f"open={len(self.open_positions)} closed={len(self.closed_positions)}"
            )
        except Exception as e:
            logger.error(f"Failed to load state ({f}): {e}")

    def _repair_capital_accounting(self):
        """
        Migrate from old notional-reservation model to equity/PnL model.

        Old model subtracted full entry notional from capital on open (often
        driving capital negative on BTC). New model keeps capital as realized
        equity (initial + closed PnL) and only applies PnL on close.
        """
        reserved = sum(
            float(p.get("entry_value", p["entry_price"] * p["position_size"]))
            for p in self.open_positions.values()
        )
        closed_pnl = sum(float(p.get("pnl", 0) or 0) for p in self.closed_positions)
        expected = self.initial_capital + closed_pnl

        if reserved > 0:
            reconstructed = self.capital + reserved
            if self.capital < 0 or abs(reconstructed - expected) < max(1.0, abs(expected) * 0.01):
                logger.warning(
                    f"Repairing capital accounting: {self.capital:.2f} → {expected:.2f} "
                    f"(reserved notional was {reserved:.2f})"
                )
                self.capital = expected
                self.save_state()
        elif self.capital < 0:
            logger.warning(f"Repairing negative capital: {self.capital:.2f} → {expected:.2f}")
            self.capital = expected
            self.save_state()

    @property
    def equity(self) -> float:
        """Realized capital plus unrealized PnL on open positions."""
        unrealized = 0.0
        for p in self.open_positions.values():
            entry = p["entry_price"]
            size = p["position_size"]
            price = p.get("current_price", entry)
            if p.get("side", "long") == "short":
                unrealized += (entry - price) * size
            else:
                unrealized += (price - entry) * size
        return self.capital + unrealized

    def save_state(self):
        """Persist full trading state to disk (atomic write)."""
        f = self._state_file
        if not f:
            return
        try:
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "capital": self.capital,
                "initial_capital": self.initial_capital,
                "open_positions": self.open_positions,
                "closed_positions": self.closed_positions,
                "trade_history": self.trade_history,
                "saved_at": time.time(),
            }, indent=2))
            tmp.replace(f)
        except Exception as e:
            logger.error(f"Failed to save state ({f}): {e}")

    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss_price: float,
        risk_pct: float = 0.5,
    ) -> float:
        """
        Calculate position size based on risk percentage of account equity.
        position_size = risk_amount / |entry - stop_loss|
        """
        risk_amount = max(0.0, self.capital) * (risk_pct / 100)
        price_risk = abs(entry_price - stop_loss_price)
        if price_risk <= 0 or risk_amount <= 0:
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
        rr_target: float = 2.0,
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
            "pnl_pct": 0,  # price move %
            "pnl_account_pct": 0,  # return on account capital %
            "r_multiple": 0,
            "capital_at_open": self.capital,
            "current_price": entry_price,
            "initial_stop_loss": sl_price,
            "peak_price": entry_price if side == "long" else entry_price,
            "trough_price": entry_price,
            "be_moved": False,
            "sl_mode": "initial",
        }
        self.open_positions[trade_id] = position
        # Paper equity model: do not reserve full notional (BTC size can exceed cash).
        # Capital stays as realized equity; only PnL is applied on close.
        self.save_state()
        return position

    def _pnl_metrics(
        self,
        side: str,
        entry: float,
        exit_price: float,
        size: float,
        sl_price: float,
        capital_at_open: float | None = None,
    ) -> tuple[float, float, float, float]:
        """Return pnl $, price %, account %, R-multiple."""
        if side == "long":
            pnl = (exit_price - entry) * size
            price_pct = (exit_price - entry) / entry * 100 if entry else 0.0
        else:
            pnl = (entry - exit_price) * size
            price_pct = (entry - exit_price) / entry * 100 if entry else 0.0

        baseline = capital_at_open if capital_at_open and capital_at_open > 0 else self.initial_capital
        account_pct = (pnl / baseline) * 100 if baseline else 0.0
        risk_amount = abs(entry - sl_price) * size
        r_mult = (pnl / risk_amount) if risk_amount > 0 else 0.0
        return pnl, price_pct, account_pct, r_mult

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
        sl = position.get("stop_loss", entry)

        pnl, price_pct, account_pct, r_mult = self._pnl_metrics(
            side, entry, exit_price, size, sl,
            capital_at_open=position.get("capital_at_open"),
        )

        position["exit_price"] = exit_price
        position["exit_time"] = time.time()
        position["exit_reason"] = exit_reason
        position["pnl"] = pnl
        position["pnl_pct"] = price_pct
        position["pnl_account_pct"] = account_pct
        position["r_multiple"] = r_mult
        position["status"] = "closed"

        # Equity model: apply realized PnL only
        self.capital += pnl

        # Move to closed
        self.closed_positions.append(position)
        self.trade_history.append(position)
        del self.open_positions[trade_id]
        self.save_state()

        logger.info(
            f"Closed position {trade_id}: {side} {size:.6f} @ {entry:.2f} → "
            f"{exit_price:.2f} | PnL: {pnl:+.2f} "
            f"(account {account_pct:+.2f}%, price {price_pct:+.2f}%, R={r_mult:+.2f}) "
            f"[{exit_reason}]"
        )
        return position

    def update_position_price(self, trade_id: str, current_price: float):
        """Update current price for an open position (for monitoring)."""
        if trade_id in self.open_positions:
            position = self.open_positions[trade_id]
            position["current_price"] = current_price
            entry = position["entry_price"]
            size = position["position_size"]
            side = position["side"]
            sl = position.get("stop_loss", entry)

            pnl, price_pct, account_pct, r_mult = self._pnl_metrics(
                side, entry, current_price, size, sl,
                capital_at_open=position.get("capital_at_open"),
            )
            position["pnl"] = pnl
            position["pnl_pct"] = price_pct
            position["pnl_account_pct"] = account_pct
            position["r_multiple"] = r_mult


class SMCEngine:
    """Main SMC trading engine with FVG/BOS/structure detection."""

    def __init__(self, config: SMCConfig | None = None):
        self.config = config or SMCConfig()
        self.market_data = MarketData()
        state_dir_env = os.environ.get("STATE_DIR")
        state_dir = Path(state_dir_env) if state_dir_env else None
        self.position_manager = PositionManager(
            initial_capital=self.config.get("paper_trading.initial_capital", 100000),
            currency=self.config.get("paper_trading.currency", "USD"),
            state_dir=state_dir,
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
            self.config.get("risk.rr_target", 2.0),
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
        Locked-candle confirmation:
        touch candle ([-3]) hits FVG, confirm candle ([-2]) is larger engulf and closed.
        """
        confirmation_method = self.config.get("entry.confirmation", "engulfing_or_ifvg")

        if len(candles_5m) >= 3 and confirmation_method in ["engulfing", "engulfing_or_ifvg"]:
            touch = candles_5m[-3]
            confirm = candles_5m[-2]
            prev_top = max(touch["open"], touch["close"])
            prev_bot = min(touch["open"], touch["close"])
            curr_top = max(confirm["open"], confirm["close"])
            curr_bot = min(confirm["open"], confirm["close"])
            prev_body = abs(touch["close"] - touch["open"])
            curr_body = abs(confirm["close"] - confirm["open"])
            touches = touch["low"] <= fvg["top"] and touch["high"] >= fvg["bottom"]

            if side == "long":
                if (
                    touches
                    and confirm["close"] > confirm["open"]
                    and touch["close"] < touch["open"]
                    and curr_body > prev_body
                    and curr_top > prev_top
                    and curr_bot <= prev_bot
                ):
                    return "engulfing_5m"
            else:
                if (
                    touches
                    and confirm["close"] < confirm["open"]
                    and touch["close"] > touch["open"]
                    and curr_body > prev_body
                    and curr_bot < prev_bot
                    and curr_top >= prev_top
                ):
                    return "engulfing_5m"

        if confirmation_method in ["ifvg", "engulfing_or_ifvg"]:
            if len(candles_5m) >= 4:
                c0 = candles_5m[-4]
                c1 = candles_5m[-3]
                c2 = candles_5m[-2]

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
        Manage open trade exits:
        - Optional BE at N:R then trailing SL
        - SL hit / optional hard TP
        - Optional structure break
        """
        self._ensure_exit_plan(position)
        sl_before = position.get("stop_loss")
        self._update_trailing_stops(position, current_price)
        if position.get("stop_loss") != sl_before:
            self.position_manager.save_state()

        entry_price = position["entry_price"]
        sl_price = position["stop_loss"]
        tp_price = position.get("take_profit")
        side = position["side"]

        if side == "long" and current_price <= sl_price:
            return "trailing_stop" if position.get("sl_mode") in ("trailing", "break_even") else "stop_loss"
        if side == "short" and current_price >= sl_price:
            return "trailing_stop" if position.get("sl_mode") in ("trailing", "break_even") else "stop_loss"

        if tp_price is not None:
            if side == "long" and current_price >= tp_price:
                return "take_profit"
            if side == "short" and current_price <= tp_price:
                return "take_profit"

        if self.config.get("exits.structure_break", False) and len(candles_5m) >= 10:
            open_time = position.get("open_time", 0)
            post = [c for c in candles_5m[-10:-1] if c.get("timestamp", 0) > open_time]
            if side == "long":
                recent_lows = [c["low"] for c in post]
                if recent_lows and min(recent_lows) < entry_price * 0.995:
                    return "structure_break"
            else:
                recent_highs = [c["high"] for c in post]
                if recent_highs and max(recent_highs) > entry_price * 1.005:
                    return "structure_break"

        return None

    def _ensure_exit_plan(self, position: dict):
        """Initialize trail fields and widen TP for be_trail mode (migrates live trades)."""
        entry = position["entry_price"]
        side = position.get("side", "long")
        initial_sl = position.setdefault("initial_stop_loss", position["stop_loss"])
        position.setdefault("peak_price", entry)
        position.setdefault("trough_price", entry)
        position.setdefault("be_moved", False)
        position.setdefault("sl_mode", "initial")

        mode = self.config.get("exits.mode", "fixed_tp")
        risk = abs(entry - initial_sl)
        if risk <= 0:
            return

        if mode in ("be_trail", "trail_only"):
            tp_rr = float(self.config.get("exits.tp_rr", 5.0) or 0)
            if tp_rr > 0:
                desired = entry + tp_rr * risk if side == "long" else entry - tp_rr * risk
                cur_tp = position.get("take_profit")
                if cur_tp is None:
                    position["take_profit"] = desired
                elif side == "long" and cur_tp < desired:
                    position["take_profit"] = desired
                elif side == "short" and cur_tp > desired:
                    position["take_profit"] = desired
            elif mode == "trail_only":
                position["take_profit"] = None

    def _update_trailing_stops(self, position: dict, current_price: float):
        """At be_at_rr → SL to BE; then trail peak/trough by trail_rr."""
        mode = self.config.get("exits.mode", "fixed_tp")
        if mode not in ("be_trail", "trail_only"):
            return

        entry = position["entry_price"]
        side = position.get("side", "long")
        initial_sl = position.get("initial_stop_loss") or position["stop_loss"]
        risk = abs(entry - initial_sl)
        if risk <= 0:
            return

        be_at = float(self.config.get("exits.be_at_rr", 2.0))
        trail_rr = float(self.config.get("exits.trail_rr", 1.0))
        trail_after = bool(self.config.get("exits.trail_after_be", True))
        be_buf = float(self.config.get("exits.be_buffer_pct", 0.0001))

        if side == "long":
            peak = max(float(position.get("peak_price", entry)), current_price)
            position["peak_price"] = peak
            rr_now = (peak - entry) / risk
            if rr_now >= be_at:
                be_price = entry * (1 + be_buf)
                if position["stop_loss"] < be_price:
                    position["stop_loss"] = be_price
                    position["be_moved"] = True
                    position["sl_mode"] = "break_even"
                if trail_after and position.get("be_moved"):
                    trail_sl = peak - trail_rr * risk
                    if trail_sl > position["stop_loss"]:
                        position["stop_loss"] = trail_sl
                        position["sl_mode"] = "trailing"
        else:
            trough = min(float(position.get("trough_price", entry)), current_price)
            position["trough_price"] = trough
            rr_now = (entry - trough) / risk
            if rr_now >= be_at:
                be_price = entry * (1 - be_buf)
                if position["stop_loss"] > be_price:
                    position["stop_loss"] = be_price
                    position["be_moved"] = True
                    position["sl_mode"] = "break_even"
                if trail_after and position.get("be_moved"):
                    trail_sl = trough + trail_rr * risk
                    if trail_sl < position["stop_loss"]:
                        position["stop_loss"] = trail_sl
                        position["sl_mode"] = "trailing"

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
