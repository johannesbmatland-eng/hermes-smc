"""Hard prop risk engine — daily loss, HWM DD, leverage caps. Soft fails prevented."""

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
    daily_breach: bool = False
    dd_breach: bool = False
    leverage_breach: bool = False
    events: list[str] = field(default_factory=list)

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
    """Enforce prop hard constraints before any order sizing/execution."""

    def __init__(self, rules: PropRules | None = None, start_equity: float | None = None):
        self.rules = rules or DEFAULT_PROP
        eq = float(start_equity if start_equity is not None else self.rules.account_usd)
        self.state = RiskState(
            equity=eq,
            cash=eq,
            hwm=eq,
            day_start_equity=eq,
        )

    def on_bar(self, day_key: str, mark_equity: float, notional: float) -> None:
        st = self.state
        if st.day_key is None:
            st.day_key = day_key
            st.day_start_equity = mark_equity
        elif day_key != st.day_key:
            st.day_key = day_key
            st.day_start_equity = mark_equity

        st.equity = mark_equity
        if mark_equity > st.hwm:
            st.hwm = mark_equity

        # Daily loss
        if st.day_pnl_frac <= -self.rules.daily_loss_limit:
            st.daily_breach = True
            st.halted = True
            st.halt_reason = "daily_loss_limit"
            st.events.append(f"FAIL daily_loss {st.day_pnl_frac:.4f}")

        # Max DD from HWM
        if st.drawdown_from_hwm >= self.rules.max_dd_hwm:
            st.dd_breach = True
            st.halted = True
            st.halt_reason = "max_dd_hwm"
            st.events.append(f"FAIL max_dd {st.drawdown_from_hwm:.4f}")

        # Leverage
        if mark_equity > 0 and abs(notional) / mark_equity > self.rules.max_leverage + 1e-9:
            st.leverage_breach = True
            st.halted = True
            st.halt_reason = "leverage"
            st.events.append(
                f"FAIL leverage {abs(notional)/mark_equity:.3f}x > {self.rules.max_leverage}x"
            )

    def can_open(self) -> bool:
        return not self.state.halted

    def max_notional(self, equity: float, max_lev: float) -> float:
        lev = min(max_lev, self.rules.max_leverage)
        return max(0.0, equity * lev)

    def passed_challenge(self) -> bool:
        st = self.state
        if st.daily_breach or st.dd_breach or st.leverage_breach:
            return False
        return st.equity >= self.rules.account_usd * (1.0 + self.rules.pass_pct)
