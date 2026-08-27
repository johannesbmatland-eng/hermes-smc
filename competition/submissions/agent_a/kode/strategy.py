"""Markov regime strategy — trade only positive-edge states via Bayes posterior."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import (
    ATR_STOP_MULT,
    ATR_TP_MULT,
    BASE_RISK_FRAC,
    COST_BPS_PER_SIDE,
    EDGE_FLOOR_AFTER_COST,
    MAX_HOLD_BARS,
    MAX_LEVERAGE,
    MIN_POSTERIOR,
    STATE_IDX,
    STATES,
)
from .markov_model import MarkovFit, bayes_update, hard_state


@dataclass
class Position:
    side: int = 0  # +1 long, -1 short, 0 flat
    entry: float = 0.0
    notional: float = 0.0  # signed USD notional
    stop: float = 0.0
    tp: float = 0.0
    bars_held: int = 0
    entry_state: int = -1


@dataclass
class Trade:
    entry_time: object
    exit_time: object
    side: int
    entry: float
    exit: float
    pnl: float
    state: str
    reason: str


class MarkovStrategy:
    def __init__(self, fit: MarkovFit):
        self.fit = fit
        self.posterior = fit.prior.copy()
        self.pos = Position()
        self.trades: list[Trade] = []
        # tradeable states with positive edge
        self.tradeable = {
            i
            for i, e in enumerate(fit.edge_after_cost)
            if e >= EDGE_FLOOR_AFTER_COST and STATES[i] in ("TREND_UP", "TREND_DOWN")
        }

    def reset_posterior(self) -> None:
        self.posterior = self.fit.prior.copy()
        self.pos = Position()

    def update_belief(self, observed_ret: float) -> np.ndarray:
        self.posterior = bayes_update(
            self.posterior,
            self.fit.transition,
            self.fit.emission_mean,
            self.fit.emission_std,
            observed_ret,
        )
        return self.posterior

    def desired_side(self) -> int:
        s = hard_state(self.posterior)
        p = float(self.posterior[s])
        if p < MIN_POSTERIOR or s not in self.tradeable:
            return 0
        if s == STATE_IDX["TREND_UP"]:
            return 1
        if s == STATE_IDX["TREND_DOWN"]:
            return -1
        return 0

    def size_notional(self, equity: float, price: float, atr: float, side: int) -> float:
        if side == 0 or not np.isfinite(atr) or atr <= 0 or price <= 0:
            return 0.0
        stop_dist = ATR_STOP_MULT * atr
        risk_usd = equity * BASE_RISK_FRAC
        qty = risk_usd / stop_dist
        notional = side * qty * price
        max_n = MAX_LEVERAGE * equity
        if abs(notional) > max_n:
            notional = side * max_n
        return float(notional)

    def stops(self, price: float, atr: float, side: int) -> tuple[float, float]:
        if side > 0:
            return price - ATR_STOP_MULT * atr, price + ATR_TP_MULT * atr
        return price + ATR_STOP_MULT * atr, price - ATR_TP_MULT * atr


def apply_side_cost(notional: float, price: float) -> float:
    """Cost in USD for opening/closing |notional| at price (fees+slippage)."""
    return abs(notional) * (COST_BPS_PER_SIDE / 10_000.0)


def unrealized_pnl(pos: Position, price: float) -> float:
    if pos.side == 0 or pos.entry <= 0:
        return 0.0
    return pos.notional * ((price - pos.entry) / pos.entry)


__all__ = [
    "MarkovStrategy",
    "Position",
    "Trade",
    "apply_side_cost",
    "unrealized_pnl",
    "MAX_HOLD_BARS",
]
