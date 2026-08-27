"""Hard prop-firm risk engine. Instant halt on rule breach."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from .config import PropRules


@dataclass
class RiskSnapshot:
    equity: float
    peak_equity: float
    day_start_equity: float
    daily_pnl: float
    daily_pnl_pct: float
    drawdown: float
    drawdown_pct: float
    halted: bool
    halt_reason: str | None
    compliance: str  # ok | risk | fail


@dataclass
class PropRiskEngine:
    rules: PropRules
    equity: float = 0.0
    peak_equity: float = 0.0
    day_start_equity: float = 0.0
    current_day: date | None = None
    halted: bool = False
    halt_reason: str | None = None
    compliance: str = "ok"
    open_notional: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.equity <= 0:
            self.equity = self.rules.starting_capital
        if self.peak_equity <= 0:
            self.peak_equity = self.equity
        if self.day_start_equity <= 0:
            self.day_start_equity = self.equity

    def _ts_to_day(self, ts: float | datetime | None) -> date:
        if ts is None:
            return datetime.now(timezone.utc).date()
        if isinstance(ts, datetime):
            return ts.astimezone(timezone.utc).date()
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()

    def on_new_bar(self, ts: float | datetime | None = None) -> None:
        d = self._ts_to_day(ts)
        if self.current_day is None:
            self.current_day = d
            self.day_start_equity = self.equity
            return
        if d != self.current_day:
            self.current_day = d
            self.day_start_equity = self.equity
            if not self.halted and self.compliance != "fail":
                # new day resets daily halt only if not permanently failed
                if self.halt_reason and self.halt_reason.startswith("daily_loss"):
                    self.halted = False
                    self.halt_reason = None
                    self.compliance = "ok"

    def mark_equity(self, equity: float, ts: float | datetime | None = None) -> RiskSnapshot:
        self.on_new_bar(ts)
        self.equity = float(equity)
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity

        daily_pnl = self.equity - self.day_start_equity
        daily_pnl_pct = (daily_pnl / self.day_start_equity) * 100.0 if self.day_start_equity else 0.0
        dd = self.peak_equity - self.equity
        dd_pct = (dd / self.peak_equity) * 100.0 if self.peak_equity else 0.0

        # Hard fails
        if daily_pnl_pct <= -self.rules.daily_loss_pct:
            self._fail(f"daily_loss {daily_pnl_pct:.3f}% <= -{self.rules.daily_loss_pct}%")
        if dd_pct >= self.rules.max_drawdown_pct:
            self._fail(f"max_dd {dd_pct:.3f}% >= {self.rules.max_drawdown_pct}%")

        # Soft risk zone: within 80% of limit
        elif (
            daily_pnl_pct <= -0.8 * self.rules.daily_loss_pct
            or dd_pct >= 0.8 * self.rules.max_drawdown_pct
        ):
            if self.compliance != "fail":
                self.compliance = "risk"

        return self.snapshot()

    def _fail(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason
        self.compliance = "fail"
        self.events.append(
            {
                "type": "FAIL",
                "reason": reason,
                "equity": self.equity,
                "peak": self.peak_equity,
            }
        )

    def can_open(self, notional: float, equity: float | None = None) -> tuple[bool, str]:
        if self.halted:
            return False, f"halted:{self.halt_reason}"
        eq = equity if equity is not None else self.equity
        if eq <= 0:
            return False, "zero_equity"
        # projected leverage including existing open notional
        lev = (self.open_notional + abs(notional)) / eq
        if lev > self.rules.max_leverage + 1e-9:
            return False, f"leverage {lev:.3f}x > {self.rules.max_leverage}x"
        # refuse new risk if already in soft risk zone near daily/DD limits
        snap = self.snapshot()
        if snap.daily_pnl_pct <= -0.9 * self.rules.daily_loss_pct:
            return False, "near_daily_loss_limit"
        if snap.drawdown_pct >= 0.9 * self.rules.max_drawdown_pct:
            return False, "near_max_dd_limit"
        return True, "ok"

    def register_open(self, notional: float) -> None:
        self.open_notional += abs(notional)

    def register_close(self, notional: float) -> None:
        self.open_notional = max(0.0, self.open_notional - abs(notional))

    def max_notional(self, equity: float | None = None) -> float:
        eq = equity if equity is not None else self.equity
        headroom = max(0.0, self.rules.max_leverage * eq - self.open_notional)
        return headroom

    def size_from_risk(
        self,
        entry: float,
        stop: float,
        risk_pct: float,
        equity: float | None = None,
    ) -> float:
        """Return position size in base units given $ risk and stop distance."""
        eq = equity if equity is not None else self.equity
        risk_dollars = eq * (risk_pct / 100.0)
        stop_dist = abs(entry - stop)
        if stop_dist <= 0 or entry <= 0:
            return 0.0
        size = risk_dollars / stop_dist
        notional = size * entry
        max_n = self.max_notional(eq)
        if notional > max_n:
            size = max_n / entry if entry else 0.0
        return max(0.0, size)

    def snapshot(self) -> RiskSnapshot:
        daily_pnl = self.equity - self.day_start_equity
        daily_pnl_pct = (daily_pnl / self.day_start_equity) * 100.0 if self.day_start_equity else 0.0
        dd = self.peak_equity - self.equity
        dd_pct = (dd / self.peak_equity) * 100.0 if self.peak_equity else 0.0
        return RiskSnapshot(
            equity=self.equity,
            peak_equity=self.peak_equity,
            day_start_equity=self.day_start_equity,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            drawdown=dd,
            drawdown_pct=dd_pct,
            halted=self.halted,
            halt_reason=self.halt_reason,
            compliance=self.compliance,
        )
