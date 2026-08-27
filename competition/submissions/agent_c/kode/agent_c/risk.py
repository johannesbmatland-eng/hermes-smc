"""Hard prop risk engine with soft pre-fail halts."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import PropRules, DEFAULT_PROP


@dataclass
class RiskState:
    equity: float
    cash: float
    hwm: float
    day_start_equity: float
    day_key: str | None = None
    halted: bool = False
    halt_reason: str | None = None
    soft_halted: bool = False
    soft_reason: str | None = None
    daily_breach: bool = False
    dd_breach: bool = False
    leverage_breach: bool = False
    events: list[str] = field(default_factory=list)
    max_daily_loss_observed: float = 0.0
    max_dd_observed: float = 0.0
    max_leverage_used: float = 0.0

    @property
    def drawdown_from_hwm(self) -> float:
        if self.hwm <= 0:
            return 0.0
        return max(0.0, (self.hwm - self.equity) / self.hwm)

    @property
    def day_pnl_frac(self) -> float:
        if self.day_start_equity <= 0:
            return 0.0
        return (self.equity - self.day_start_equity) / self.day_start_equity


class RiskEngine:
    def __init__(
        self,
        rules: PropRules | None = None,
        start_equity: float | None = None,
        use_soft_stops: bool = True,
        enforce_hard_halt: bool = True,
    ):
        self.rules = rules or DEFAULT_PROP
        self.use_soft_stops = use_soft_stops
        self.enforce_hard_halt = enforce_hard_halt
        eq = float(start_equity if start_equity is not None else self.rules.account_usd)
        self.state = RiskState(equity=eq, cash=eq, hwm=eq, day_start_equity=eq)

    def on_bar(self, day_key: str, mark_equity: float, notional: float) -> None:
        st = self.state
        if st.day_key is None or day_key != st.day_key:
            st.day_key = day_key
            st.day_start_equity = mark_equity
            # Soft daily halt clears on new day; soft DD persists for challenge window
            if st.soft_halted and st.soft_reason == "soft_daily":
                st.soft_halted = False
                st.soft_reason = None

        st.equity = mark_equity
        if mark_equity > st.hwm:
            st.hwm = mark_equity

        day_loss = -min(0.0, st.day_pnl_frac)
        st.max_daily_loss_observed = max(st.max_daily_loss_observed, day_loss)
        st.max_dd_observed = max(st.max_dd_observed, st.drawdown_from_hwm)
        if mark_equity > 0:
            st.max_leverage_used = max(st.max_leverage_used, abs(notional) / mark_equity)

        # Soft stops first
        if self.use_soft_stops:
            if st.day_pnl_frac <= -self.rules.soft_daily_loss and not st.soft_halted:
                st.soft_halted = True
                st.soft_reason = "soft_daily"
                st.events.append(f"SOFT daily {st.day_pnl_frac:.4f}")

            if st.drawdown_from_hwm >= self.rules.soft_dd_hwm and not st.soft_halted:
                st.soft_halted = True
                st.soft_reason = "soft_dd"
                st.events.append(f"SOFT dd {st.drawdown_from_hwm:.4f}")

        # Hard fails (always recorded; halt only if enforce_hard_halt)
        if st.day_pnl_frac <= -self.rules.daily_loss_limit:
            st.daily_breach = True
            st.events.append(f"FAIL daily_loss {st.day_pnl_frac:.4f}")
            if self.enforce_hard_halt:
                st.halted = True
                st.halt_reason = "daily_loss_limit"

        if st.drawdown_from_hwm >= self.rules.max_dd_hwm:
            st.dd_breach = True
            st.events.append(f"FAIL max_dd {st.drawdown_from_hwm:.4f}")
            if self.enforce_hard_halt:
                st.halted = True
                st.halt_reason = "max_dd_hwm"

        if mark_equity > 0 and abs(notional) / mark_equity > self.rules.max_leverage + 1e-9:
            st.leverage_breach = True
            st.events.append(f"FAIL leverage {abs(notional)/mark_equity:.3f}x")
            if self.enforce_hard_halt:
                st.halted = True
                st.halt_reason = "leverage"

    def can_open(self) -> bool:
        return (not self.state.halted) and (not self.state.soft_halted)

    def max_notional(self, equity: float, max_lev: float) -> float:
        return max(0.0, equity * min(max_lev, self.rules.max_leverage))

    def passed_challenge(self) -> bool:
        st = self.state
        if st.daily_breach or st.dd_breach or st.leverage_breach:
            return False
        return st.equity >= self.rules.account_usd * (1.0 + self.rules.pass_pct)
