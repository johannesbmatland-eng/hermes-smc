"""Adaptive Regime Breakout strategy signals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

from .config import StrategyParams
from .indicators import atr, donchian_high, donchian_low, ema, rolling_percentile_rank


class Regime(str, Enum):
    TREND = "trend"
    CHOP = "chop"


@dataclass
class Signal:
    side: str
    entry: float
    stop: float
    take_profit: float
    regime: Regime
    meta: dict[str, Any]


class AdaptiveRegimeBreakout:
    """
    2-state regime model with persistence:
      CHOP / chaos extremes → flat
      TREND → Donchian breakout + EMA slope alignment
    """

    def __init__(self, params: StrategyParams | None = None):
        self.p = params or StrategyParams()
        self._regime: Regime = Regime.CHOP
        self._regime_count: int = 0
        self._pending_regime: Regime | None = None

    def reset(self) -> None:
        self._regime = Regime.CHOP
        self._regime_count = 0
        self._pending_regime = None

    def _update_regime(self, desired: Regime) -> Regime:
        if desired == self._regime:
            self._pending_regime = None
            self._regime_count += 1
            return self._regime
        if self._pending_regime != desired:
            self._pending_regime = desired
            self._regime_count = 1
        else:
            self._regime_count += 1
        if self._regime_count >= self.p.regime_persist_bars:
            self._regime = desired
            self._pending_regime = None
        return self._regime

    def prepare(self, ohlcv: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        high, low, close = ohlcv["high"], ohlcv["low"], ohlcv["close"]
        atr_v = atr(high, low, close, self.p.atr_period)
        atr_pct = atr_v / np.where(close > 0, close, np.nan) * 100.0
        return {
            "ema_fast": ema(close, self.p.ema_fast),
            "ema_slow": ema(close, self.p.ema_slow),
            "atr": atr_v,
            "atr_pct": atr_pct,
            "atr_pct_rank": rolling_percentile_rank(atr_pct, self.p.regime_vol_lookback),
            "dc_high": donchian_high(high, self.p.lookback),
            "dc_low": donchian_low(low, self.p.lookback),
        }

    def signal_at(
        self,
        i: int,
        ohlcv: dict[str, np.ndarray],
        feat: dict[str, np.ndarray],
    ) -> Signal | None:
        close = ohlcv["close"][i]
        high = ohlcv["high"][i]
        low = ohlcv["low"][i]
        ef = feat["ema_fast"][i]
        es = feat["ema_slow"][i]
        atr_v = feat["atr"][i]
        atr_pct = feat["atr_pct"][i]
        rank = feat["atr_pct_rank"][i]
        dc_h = feat["dc_high"][i]
        dc_l = feat["dc_low"][i]

        if any(np.isnan(x) for x in (close, ef, es, atr_v, atr_pct, rank, dc_h, dc_l)):
            return None
        if atr_pct < self.p.min_atr_pct:
            self._update_regime(Regime.CHOP)
            return None

        # mid-high vol = trendable; very high = chaos skip
        if rank < self.p.chop_vol_percentile or rank > self.p.chaos_vol_percentile:
            desired = Regime.CHOP
        else:
            desired = Regime.TREND
        regime = self._update_regime(desired)
        if regime != Regime.TREND:
            return None

        ema_sep = abs(ef - es) / close * 100.0
        if ema_sep < self.p.min_ema_sep_pct:
            return None

        buf = self.p.breakout_buffer_atr * atr_v
        long_break = close > (dc_h + buf) and ef > es
        short_break = close < (dc_l - buf) and ef < es
        if long_break == short_break:
            return None

        # reject weak closes (body must be majority of range in breakout direction)
        body = abs(close - ohlcv["open"][i])
        rng = max(high - low, 1e-12)
        if body / rng < 0.45:
            return None

        if long_break:
            if close < ohlcv["open"][i]:
                return None
            stop = close - self.p.sl_atr_mult * atr_v
            risk = close - stop
            if risk <= 0:
                return None
            tp = close + self.p.rr_target * risk
            return Signal(
                side="long",
                entry=float(close),
                stop=float(stop),
                take_profit=float(tp),
                regime=regime,
                meta={
                    "atr": float(atr_v),
                    "atr_pct": float(atr_pct),
                    "vol_rank": float(rank),
                    "dc_high": float(dc_h),
                    "ema_sep_pct": float(ema_sep),
                },
            )

        if close > ohlcv["open"][i]:
            return None
        stop = close + self.p.sl_atr_mult * atr_v
        risk = stop - close
        if risk <= 0:
            return None
        tp = close - self.p.rr_target * risk
        return Signal(
            side="short",
            entry=float(close),
            stop=float(stop),
            take_profit=float(tp),
            regime=regime,
            meta={
                "atr": float(atr_v),
                "atr_pct": float(atr_pct),
                "vol_rank": float(rank),
                "dc_low": float(dc_l),
                "ema_sep_pct": float(ema_sep),
            },
        )
