"""Hard risk engine — prop rules are non-negotiable fail conditions."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import (
    ACCOUNT_EQUITY,
    DAILY_LOSS_LIMIT,
    DAILY_SOFT_STOP_FRAC,
    DD_SOFT_STOP_FRAC,
    MAX_DD_FROM_PEAK,
    MAX_LEVERAGE,
)


@dataclass
class RiskState:
    equity: float = ACCOUNT_EQUITY
    peak_equity: float = ACCOUNT_EQUITY
    day_start_equity: float = ACCOUNT_EQUITY
    current_day: object | None = None
    failed: bool = False
    fail_reason: str | None = None
    passed: bool = False
    breaches: dict = field(
        default_factory=lambda: {"daily_3pct": 0, "dd_6pct": 0, "leverage_5x": 0}
    )
    max_leverage_used: float = 0.0
    max_daily_loss_obs: float = 0.0  # most negative day pnl / day_start
    max_dd_obs: float = 0.0
    halted_today: bool = False
    risk_scale: float = 1.0


class RiskEngine:
    """Enforces daily -3%, max DD -6% from HWM, leverage ≤ 5x."""

    def __init__(
        self,
        equity: float = ACCOUNT_EQUITY,
        daily_loss_limit: float = DAILY_LOSS_LIMIT,
        max_dd: float = MAX_DD_FROM_PEAK,
        max_leverage: float = MAX_LEVERAGE,
        pass_profit: float = 10_000.0,
    ):
        self.daily_loss_limit = daily_loss_limit
        self.max_dd = max_dd
        self.max_leverage = max_leverage
        self.pass_profit = pass_profit
        self.initial = equity
        self.state = RiskState(equity=equity, peak_equity=equity, day_start_equity=equity)

    def on_new_bar(self, day_key) -> None:
        st = self.state
        if st.current_day is None:
            st.current_day = day_key
            st.day_start_equity = st.equity
            st.halted_today = False
            return
        if day_key != st.current_day:
            st.current_day = day_key
            st.day_start_equity = st.equity
            st.halted_today = False
            st.risk_scale = 1.0

    def update_equity(self, equity: float) -> None:
        st = self.state
        st.equity = equity
        if equity > st.peak_equity:
            st.peak_equity = equity
        dd = (st.peak_equity - equity) / st.peak_equity if st.peak_equity > 0 else 0.0
        st.max_dd_obs = max(st.max_dd_obs, dd)
        day_pnl = equity - st.day_start_equity
        day_loss_frac = day_pnl / st.day_start_equity if st.day_start_equity else 0.0
        if day_loss_frac < st.max_daily_loss_obs:
            st.max_daily_loss_obs = day_loss_frac

        # soft controls
        if day_pnl <= -DAILY_SOFT_STOP_FRAC * st.day_start_equity:
            st.halted_today = True
            st.risk_scale = 0.0
        if dd >= DD_SOFT_STOP_FRAC:
            st.risk_scale = min(st.risk_scale, 0.25)
        if dd >= DD_SOFT_STOP_FRAC + 0.01:
            st.halted_today = True
            st.risk_scale = 0.0

        # hard fails
        if day_pnl <= -self.daily_loss_limit:
            st.failed = True
            st.fail_reason = "daily_3pct"
            st.breaches["daily_3pct"] += 1
            st.halted_today = True
            st.risk_scale = 0.0
        if dd >= self.max_dd:
            st.failed = True
            st.fail_reason = "dd_6pct"
            st.breaches["dd_6pct"] += 1
            st.halted_today = True
            st.risk_scale = 0.0

        if equity - self.initial >= self.pass_profit:
            st.passed = True

    def clamp_notional(self, notional: float, price: float, equity: float | None = None) -> float:
        """Clamp absolute notional to max leverage; record breaches if attempted over."""
        eq = equity if equity is not None else self.state.equity
        max_n = self.max_leverage * eq
        lev = abs(notional) / eq if eq > 0 else 0.0
        self.state.max_leverage_used = max(self.state.max_leverage_used, min(lev, self.max_leverage))
        if abs(notional) > max_n + 1e-6:
            self.state.breaches["leverage_5x"] += 1
            notional = np_sign(notional) * max_n
        # apply risk scale
        notional *= self.state.risk_scale
        if self.state.halted_today or self.state.failed:
            return 0.0
        return notional

    def allow_new_trade(self) -> bool:
        st = self.state
        return (not st.failed) and (not st.halted_today) and st.risk_scale > 0


def np_sign(x: float) -> float:
    return 1.0 if x >= 0 else -1.0
