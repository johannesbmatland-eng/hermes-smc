"""Markov regime strategy — SHOCK recovery + transition edges with equity stops."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import (
    COST_BPS_PER_SIDE,
    MAX_HOLD_BARS,
    MAX_LEVERAGE,
    MIN_POSTERIOR,
    PREFERRED_HOURS,
    RECOVERY_HOLD,
    STATE_IDX,
    STATES,
)
from .markov_model import MarkovFit, bayes_update

# Equity-fraction stop/TP keeps daily -3% structurally out of reach (single-trade).
EQUITY_STOP = 0.017
EQUITY_TP = 0.060
LEV_DEEP = 0.75
LEV_MILD = 0.55
LEV_TRANS = 0.40


@dataclass
class Position:
    side: int = 0
    entry: float = 0.0
    notional: float = 0.0
    stop: float = 0.0
    tp: float = 0.0
    bars_held: int = 0
    entry_state: int = -1
    max_hold: int = MAX_HOLD_BARS
    tag: str = ""
    leverage: float = 0.0


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
        self.prev_hard = int(np.argmax(fit.prior))
        self.cooldown = 0

    def reset_posterior(self) -> None:
        self.posterior = self.fit.prior.copy()
        self.pos = Position()
        self.prev_hard = int(np.argmax(self.fit.prior))
        self.cooldown = 0

    def update_belief(self, observed_ret: float) -> np.ndarray:
        proxy = observed_ret * np.sqrt(max(self.fit.edge_hold_bars, 1))
        self.posterior = bayes_update(
            self.posterior,
            self.fit.transition,
            self.fit.emission_mean,
            self.fit.emission_std,
            proxy,
        )
        return self.posterior

    def signal(
        self,
        hard_label: int,
        cum3: float,
        hour: int,
        bar_ret: float,
    ) -> tuple[int, float, str, int]:
        """Return (side, leverage, tag, max_hold)."""
        if self.cooldown > 0:
            self.cooldown -= 1
            self.prev_hard = hard_label
            return 0, 0.0, "", RECOVERY_HOLD

        p_shock = float(self.posterior[STATE_IDX["SHOCK"]])
        prev = self.prev_hard
        te = float(self.fit.transition_edge[prev, hard_label])

        side = 0
        lev = 0.0
        tag = ""
        hold = RECOVERY_HOLD

        deep = np.isfinite(cum3) and cum3 <= -0.035
        mild = np.isfinite(cum3) and cum3 <= -0.025
        in_shock = hard_label == STATE_IDX["SHOCK"]
        enter_shock = in_shock and prev != STATE_IDX["SHOCK"]

        # Primary: capitulation recovery inside SHOCK (Bayes-weighted)
        if in_shock and deep and (hour in PREFERRED_HOURS or cum3 <= -0.045):
            if bar_ret > -0.012 or cum3 <= -0.045:
                if p_shock >= 0.30 or enter_shock:
                    side, lev, tag, hold = 1, LEV_DEEP, f"DEEP_RECOVERY|{STATES[prev]}→SHOCK", 48

        elif in_shock and mild and enter_shock and prev == STATE_IDX["TREND_DOWN"]:
            if hour in PREFERRED_HOURS and p_shock >= MIN_POSTERIOR * 0.8:
                side, lev, tag, hold = 1, LEV_MILD, "TD→SHOCK", 40

        # Secondary: transition edge TREND_DOWN → TREND_UP
        elif (
            prev == STATE_IDX["TREND_DOWN"]
            and hard_label == STATE_IDX["TREND_UP"]
            and hour in PREFERRED_HOURS
            and (te > 0 or float(self.posterior[STATE_IDX["TREND_UP"]]) >= MIN_POSTERIOR)
        ):
            side, lev, tag, hold = 1, LEV_TRANS, "TD→TU", 24

        self.prev_hard = hard_label
        lev = float(min(max(lev, 0.0), MAX_LEVERAGE))
        return side, lev, tag, hold

    def size_notional(self, equity: float, leverage: float, side: int) -> float:
        if side == 0 or leverage <= 0:
            return 0.0
        return float(side * min(leverage, MAX_LEVERAGE) * equity)

    def stops_for(self, price: float, leverage: float) -> tuple[float, float]:
        # Map equity stop/TP to price levels given leverage
        stop = price * (1.0 - EQUITY_STOP / max(leverage, 1e-6))
        tp = price * (1.0 + EQUITY_TP / max(leverage, 1e-6))
        return stop, tp


def apply_side_cost(notional: float, price: float) -> float:
    return abs(notional) * (COST_BPS_PER_SIDE / 10_000.0)


def unrealized_pnl(pos: Position, price: float) -> float:
    if pos.side == 0 or pos.entry <= 0:
        return 0.0
    return pos.notional * ((price - pos.entry) / pos.entry)
