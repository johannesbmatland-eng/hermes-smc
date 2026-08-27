"""Markov regime strategy — Bayes states, transition edges, dollar-risk sizing."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import (
    COST_BPS_PER_SIDE,
    EDGE_FLOOR_AFTER_COST,
    MAX_HOLD_BARS,
    MAX_LEVERAGE,
    MIN_POSTERIOR,
    PREFERRED_HOURS,
    RECOVERY_HOLD,
    SHOCK_CUM3,
    STATE_IDX,
    STATES,
)
from .markov_model import MarkovFit, bayes_update


# Per-trade equity risk target (stop-based); keeps daily -3% headroom
RISK_FRAC_BASE = 0.009
RISK_FRAC_HIGH = 0.012
STOP_PCT = 0.028  # -2.8% stop
TP_PCT = 0.055


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
        """Returns (side, risk_frac, tag, max_hold)."""
        if self.cooldown > 0:
            self.cooldown -= 1
            self.prev_hard = hard_label
            return 0, 0.0, "", RECOVERY_HOLD

        p_shock = float(self.posterior[STATE_IDX["SHOCK"]])
        p_state = float(self.posterior[hard_label])
        prev = self.prev_hard
        te = float(self.fit.transition_edge[prev, hard_label])

        side = 0
        risk_frac = 0.0
        tag = ""
        hold = RECOVERY_HOLD

        dump = np.isfinite(cum3) and cum3 <= -0.022
        deep_dump = np.isfinite(cum3) and cum3 <= -0.032
        enter_shock = hard_label == STATE_IDX["SHOCK"] and prev != STATE_IDX["SHOCK"]
        in_shock = hard_label == STATE_IDX["SHOCK"]

        # Primary: SHOCK recovery after dump
        if dump and (enter_shock or in_shock) and p_shock >= 0.35:
            if hour in PREFERRED_HOURS or deep_dump:
                side = 1
                risk_frac = RISK_FRAC_HIGH if deep_dump else RISK_FRAC_BASE
                tag = f"SHOCK_RECOVERY|{STATES[prev]}→SHOCK"
                hold = 40 if deep_dump else 28

        # Transition edge long
        elif te >= EDGE_FLOOR_AFTER_COST and p_state >= MIN_POSTERIOR:
            if hard_label == STATE_IDX["SHOCK"] or (
                prev == STATE_IDX["TREND_DOWN"] and hard_label == STATE_IDX["TREND_UP"]
            ):
                side = 1
                risk_frac = RISK_FRAC_BASE * 0.85
                tag = f"TRANS_EDGE|{STATES[prev]}→{STATES[hard_label]}"
                hold = 24

        # Mild: TREND_DOWN persistence fade when posterior shifting to RANGE/UP
        elif (
            prev == STATE_IDX["TREND_DOWN"]
            and hard_label == STATE_IDX["RANGE"]
            and te > 0
            and hour in PREFERRED_HOURS
            and float(self.posterior[STATE_IDX["TREND_UP"]] + self.posterior[STATE_IDX["RANGE"]])
            >= 0.55
        ):
            side = 1
            risk_frac = RISK_FRAC_BASE * 0.55
            tag = "TD_FADE_TO_RANGE"
            hold = 16

        self.prev_hard = hard_label
        return side, float(risk_frac), tag, hold

    def size_notional(self, equity: float, risk_frac: float, side: int) -> float:
        if side == 0 or risk_frac <= 0:
            return 0.0
        # notional so that STOP_PCT move ≈ risk_frac * equity
        notional = (risk_frac * equity) / STOP_PCT
        max_n = MAX_LEVERAGE * equity
        notional = min(notional, max_n)
        return float(side * notional)


def apply_side_cost(notional: float, price: float) -> float:
    return abs(notional) * (COST_BPS_PER_SIDE / 10_000.0)


def unrealized_pnl(pos: Position, price: float) -> float:
    if pos.side == 0 or pos.entry <= 0:
        return 0.0
    return pos.notional * ((price - pos.entry) / pos.entry)


# re-export stop constants for backtest
STRATEGY_STOP_PCT = STOP_PCT
STRATEGY_TP_PCT = TP_PCT
