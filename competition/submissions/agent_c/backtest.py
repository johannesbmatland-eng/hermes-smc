"""Event-driven multi-market backtester with fees, slippage, and prop risk."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .config import Config
from .data import load_or_fetch
from .risk import PropRiskEngine
from .strategy import AdaptiveRegimeBreakout


@dataclass
class Position:
    market: str
    side: str
    size: float
    entry: float
    stop: float
    take_profit: float
    notional: float
    entry_ts: float
    entry_i: int
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trade:
    market: str
    side: str
    size: float
    entry: float
    exit: float
    pnl: float
    pnl_pct_equity: float
    reason: str
    entry_ts: float
    exit_ts: float
    bars_held: int
    fees: float
    slippage_cost: float


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: list[float]
    timestamps: list[float]
    final_equity: float
    starting_capital: float
    profit: float
    profit_pct: float
    profit_per_month: float
    winrate: float
    expectancy: float
    max_dd_pct: float
    worst_day_pct: float
    n_trades: int
    compliance: str
    halt_reason: str | None
    months_span: float
    source: dict[str, str]
    metrics: dict[str, Any]


class Backtester:
    def __init__(self, config: Config | None = None):
        self.cfg = config or Config()

    def _apply_entry_price(self, raw: float, side: str) -> tuple[float, float]:
        slip = self.cfg.costs.slippage_bps / 10_000.0
        fee = self.cfg.costs.fee_bps / 10_000.0
        fill = raw * (1 + slip) if side == "long" else raw * (1 - slip)
        return fill, fee

    def _apply_exit_price(self, raw: float, side: str) -> tuple[float, float]:
        slip = self.cfg.costs.slippage_bps / 10_000.0
        fee = self.cfg.costs.fee_bps / 10_000.0
        fill = raw * (1 - slip) if side == "long" else raw * (1 + slip)
        return fill, fee

    def run(
        self,
        limit: int = 720,
        cache_dir: Path | None = None,
        allow_synthetic: bool = True,
    ) -> BacktestResult:
        markets = self.cfg.strategy.markets
        series: dict[str, dict[str, np.ndarray]] = {}
        feats: dict[str, dict[str, np.ndarray]] = {}
        sources: dict[str, str] = {}
        strategies: dict[str, AdaptiveRegimeBreakout] = {}

        for m in markets:
            data, src = load_or_fetch(
                m,
                timeframe=self.cfg.strategy.timeframe,
                limit=limit,
                cache_dir=cache_dir,
                allow_synthetic=allow_synthetic,
            )
            series[m] = data
            sources[m] = src
            strat = AdaptiveRegimeBreakout(self.cfg.strategy)
            strategies[m] = strat
            feats[m] = strat.prepare(data)

        n = min(len(series[m]["close"]) for m in markets)
        warmup = (
            max(
                self.cfg.strategy.lookback,
                self.cfg.strategy.ema_slow,
                self.cfg.strategy.regime_vol_lookback,
                self.cfg.strategy.atr_period,
            )
            + 5
        )
        return self._simulate(series, feats, strategies, sources, n, warmup)

    def _simulate(
        self,
        series: dict[str, dict[str, np.ndarray]],
        feats: dict[str, dict[str, np.ndarray]],
        strategies: dict[str, AdaptiveRegimeBreakout],
        sources: dict[str, str],
        n: int,
        warmup: int,
    ) -> BacktestResult:
        markets = list(series.keys())
        risk = PropRiskEngine(self.cfg.prop)
        positions: dict[str, Position] = {}
        trades: list[Trade] = []
        equity_curve: list[float] = []
        timestamps: list[float] = []
        day_close: dict[str, float] = {}
        cooldown: dict[str, int] = {m: 0 for m in markets}
        realized = self.cfg.prop.starting_capital

        def equity_at(i: int) -> float:
            eq = realized
            for p in positions.values():
                px = float(series[p.market]["close"][i])
                if p.side == "long":
                    eq += (px - p.entry) * p.size
                else:
                    eq += (p.entry - px) * p.size
            return eq

        def close_pos(m: str, i: int, ts: float, exit_raw: float, reason: str) -> None:
            nonlocal realized
            pos = positions[m]
            fill, fee_rate = self._apply_exit_price(exit_raw, pos.side)
            if pos.side == "long":
                gross = (fill - pos.entry) * pos.size
            else:
                gross = (pos.entry - fill) * pos.size
            exit_fee = fee_rate * fill * pos.size
            entry_fee = fee_rate * pos.entry * pos.size
            pnl = gross - exit_fee
            slip_cost = abs(fill - exit_raw) * pos.size
            realized += pnl
            risk.register_close(pos.notional)
            trades.append(
                Trade(
                    market=m,
                    side=pos.side,
                    size=pos.size,
                    entry=pos.entry,
                    exit=fill,
                    pnl=pnl,
                    pnl_pct_equity=(pnl / max(realized - pnl, 1.0)) * 100.0,
                    reason=reason,
                    entry_ts=pos.entry_ts,
                    exit_ts=ts,
                    bars_held=i - pos.entry_i,
                    fees=entry_fee + exit_fee,
                    slippage_cost=slip_cost
                    + abs(pos.entry - float(pos.meta.get("raw_entry", pos.entry))) * pos.size,
                )
            )
            del positions[m]
            cooldown[m] = self.cfg.strategy.cooldown_bars

        for i in range(warmup, n):
            master = markets[0]
            ts = float(series[master]["ts"][i])
            risk.on_new_bar(ts)

            for m in list(positions.keys()):
                pos = positions[m]
                hi = float(series[m]["high"][i])
                lo = float(series[m]["low"][i])
                reason = None
                exit_raw = None
                if pos.side == "long":
                    if lo <= pos.stop:
                        reason, exit_raw = "sl", pos.stop
                    elif hi >= pos.take_profit:
                        reason, exit_raw = "tp", pos.take_profit
                else:
                    if hi >= pos.stop:
                        reason, exit_raw = "sl", pos.stop
                    elif lo <= pos.take_profit:
                        reason, exit_raw = "tp", pos.take_profit
                if reason is not None and exit_raw is not None:
                    close_pos(m, i, ts, exit_raw, reason)

            eq_now = equity_at(i)
            snap = risk.mark_equity(eq_now, ts)
            if snap.halted:
                for m in list(positions.keys()):
                    close_pos(m, i, ts, float(series[m]["close"][i]), f"halt:{snap.halt_reason}")
                eq_now = realized
                risk.mark_equity(eq_now, ts)
                equity_curve.append(eq_now)
                timestamps.append(ts)
                d = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
                day_close[d] = eq_now
                break

            for m in markets:
                if cooldown[m] > 0:
                    cooldown[m] -= 1

            if len(positions) < self.cfg.strategy.max_open_positions and not risk.halted:
                for m in markets:
                    if m in positions or cooldown[m] > 0:
                        continue
                    if len(positions) >= self.cfg.strategy.max_open_positions:
                        break
                    sig = strategies[m].signal_at(i, series[m], feats[m])
                    if sig is None:
                        continue
                    raw_entry = sig.entry
                    fill, fee_rate = self._apply_entry_price(raw_entry, sig.side)
                    if sig.side == "long":
                        risk_dist = raw_entry - sig.stop
                        stop = fill - risk_dist
                        tp = fill + self.cfg.strategy.rr_target * risk_dist
                    else:
                        risk_dist = sig.stop - raw_entry
                        stop = fill + risk_dist
                        tp = fill - self.cfg.strategy.rr_target * risk_dist
                    if risk_dist <= 0:
                        continue
                    size = risk.size_from_risk(
                        fill, stop, self.cfg.strategy.risk_pct_per_trade, equity=eq_now
                    )
                    if size <= 0:
                        continue
                    notional = size * fill
                    ok, _why = risk.can_open(notional, eq_now)
                    if not ok:
                        continue
                    entry_fee = fee_rate * notional
                    realized -= entry_fee
                    risk.register_open(notional)
                    positions[m] = Position(
                        market=m,
                        side=sig.side,
                        size=size,
                        entry=fill,
                        stop=stop,
                        take_profit=tp,
                        notional=notional,
                        entry_ts=ts,
                        entry_i=i,
                        meta={"raw_entry": raw_entry, **sig.meta},
                    )

            eq_now = equity_at(i)
            risk.mark_equity(eq_now, ts)
            equity_curve.append(eq_now)
            timestamps.append(ts)
            d = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
            day_close[d] = eq_now

        if positions and timestamps:
            i = n - 1
            ts = timestamps[-1]
            for m in list(positions.keys()):
                close_pos(m, i, ts, float(series[m]["close"][i]), "eod_flatten")
            equity_curve[-1] = realized
            risk.mark_equity(realized, ts)
            d = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
            day_close[d] = realized

        return self._summarize(trades, equity_curve, timestamps, risk, sources, day_close)

    def _summarize(
        self,
        trades: list[Trade],
        equity_curve: list[float],
        timestamps: list[float],
        risk: PropRiskEngine,
        sources: dict[str, str],
        day_close: dict[str, float],
    ) -> BacktestResult:
        start = self.cfg.prop.starting_capital
        final = equity_curve[-1] if equity_curve else start
        profit = final - start
        profit_pct = (profit / start) * 100.0

        if timestamps and len(timestamps) > 1:
            days = max((timestamps[-1] - timestamps[0]) / 86400.0, 1.0)
        else:
            days = 30.0
        months = max(days / 30.0, 1.0 / 30.0)
        profit_per_month = profit / months

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        winrate = (len(wins) / len(trades) * 100.0) if trades else 0.0
        expectancy = (sum(t.pnl for t in trades) / len(trades)) if trades else 0.0

        peak = start
        max_dd = 0.0
        for e in equity_curve:
            peak = max(peak, e)
            dd = (peak - e) / peak * 100.0 if peak else 0.0
            max_dd = max(max_dd, dd)

        worst_day = 0.0
        prev = start
        for d in sorted(day_close.keys()):
            chg = (day_close[d] - prev) / prev * 100.0 if prev else 0.0
            worst_day = min(worst_day, chg)
            prev = day_close[d]

        snap = risk.snapshot()
        metrics = {
            "profit": round(profit, 2),
            "profit_pct": round(profit_pct, 3),
            "profit_per_month": round(profit_per_month, 2),
            "winrate": round(winrate, 2),
            "expectancy": round(expectancy, 2),
            "max_dd_pct": round(max_dd, 3),
            "worst_day_pct": round(worst_day, 3),
            "trades": len(trades),
            "compliance": snap.compliance,
            "halt_reason": snap.halt_reason,
            "months_span": round(months, 2),
            "avg_win": round(sum(t.pnl for t in wins) / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(t.pnl for t in losses) / len(losses), 2) if losses else 0.0,
            "total_fees": round(sum(t.fees for t in trades), 2),
            "total_slippage": round(sum(t.slippage_cost for t in trades), 2),
            "final_equity": round(final, 2),
        }

        return BacktestResult(
            trades=trades,
            equity_curve=equity_curve,
            timestamps=timestamps,
            final_equity=final,
            starting_capital=start,
            profit=profit,
            profit_pct=profit_pct,
            profit_per_month=profit_per_month,
            winrate=winrate,
            expectancy=expectancy,
            max_dd_pct=max_dd,
            worst_day_pct=worst_day,
            n_trades=len(trades),
            compliance=snap.compliance,
            halt_reason=snap.halt_reason,
            months_span=months,
            source=sources,
            metrics=metrics,
        )

    def write_reports(self, result: BacktestResult, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "backtest_metrics.json").write_text(json.dumps(result.metrics, indent=2))
        (out_dir / "trades.json").write_text(json.dumps([asdict(t) for t in result.trades], indent=2))
        (out_dir / "equity_curve.json").write_text(
            json.dumps(
                {
                    "timestamps": result.timestamps,
                    "equity": result.equity_curve,
                    "sources": result.source,
                }
            )
        )
