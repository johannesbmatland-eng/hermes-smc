#!/usr/bin/env python3
"""AGENT_B — Microstructure Hybrid (causal session MOM + Asia MR).

All signal features are lagged 1 bar (no same-bar look-ahead).
Exploits London/NY/Overlap session continuation and Asia fade.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "btcusd_hourly_public.csv"

ACCOUNT = 100_000.0
PASS_PCT = 0.10
DAILY_FAIL = 0.03
MAX_DD_FAIL = 0.06
MAX_LEV = 5.0

FEE_BPS = 3.0
SLIP_BPS = 3.0
COST_BPS_SIDE = FEE_BPS + SLIP_BPS


@dataclass
class StrategyParams:
    z_lookback: int = 48
    lon_thr: float = 0.010
    ny_thr: float = 0.016
    ol_thr: float = 0.014
    asia_z: float = 2.3
    lon_hold: int = 30
    ny_hold: int = 14
    ol_hold: int = 30
    asia_hold: int = 8
    lev_lon: float = 3.2
    lev_ny: float = 3.0
    lev_ol: float = 2.6
    lev_asia: float = 1.4
    daily_stop: float = 0.015
    trade_stop: float = 0.022
    hwm_stop: float = 0.040
    entry_dd_cap: float = 0.048
    cooldown: int = 6
    max_trades_per_day: int = 2
    min_bars_warmup: int = 120
    skip_hours: tuple = (1, 13, 19, 23)
    use_asia_mr: bool = True
    use_overlap: bool = True


SESSION_MATRIX = {
    "Asia": "MR",
    "London": "MOM",
    "Overlap": "MOM",
    "NY": "MOM",
    "Quiet": "NONE",
}


def session_of(hour: int) -> str:
    if 0 <= hour < 7:
        return "Asia"
    if 7 <= hour < 12:
        return "London"
    if 12 <= hour < 16:
        return "Overlap"
    if 16 <= hour < 21:
        return "NY"
    return "Quiet"


def load_ohlcv(path: Path = DATA) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["dt"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = df.sort_values("dt").drop_duplicates("timestamp").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    df["ret"] = df["close"].pct_change()
    df["hour"] = df["dt"].dt.hour
    df["dow"] = df["dt"].dt.dayofweek
    df["session"] = df["hour"].map(session_of)
    df["date"] = df["dt"].dt.floor("D")
    return df


def add_features(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    out = df.copy()
    # CAUSAL: all decision features shifted by 1 bar
    out["vol"] = out["ret"].rolling(p.z_lookback).std().shift(1)
    out["z"] = (out["ret"].shift(1) / out["vol"]).replace([np.inf, -np.inf], np.nan)
    out["mom12"] = out["close"].pct_change(12).shift(1)
    out["mom48"] = out["close"].pct_change(48).shift(1)
    vol24 = out["ret"].rolling(24).std()
    out["vol_ratio"] = (vol24 / vol24.rolling(24 * 7, min_periods=72).mean()).shift(1)
    mu = vol24.rolling(24 * 90, min_periods=24 * 14).mean()
    sd = vol24.rolling(24 * 90, min_periods=24 * 14).std()
    out["vol_z_reg"] = ((vol24 - mu) / sd.replace(0, np.nan)).shift(1)
    return out


def regime_of(vz: float) -> str:
    if not np.isfinite(vz):
        return "trend"
    if vz < -0.45:
        return "range"
    if vz > 1.2:
        return "shock"
    return "trend"


def decide(row: pd.Series, p: StrategyParams) -> tuple[float, str, int, float]:
    hour = int(row["hour"])
    sess = row["session"]
    if hour in p.skip_hours:
        return 0.0, "filter", 0, 0.0
    perm = SESSION_MATRIX.get(sess, "NONE")
    if perm == "NONE":
        return 0.0, "blocked", 0, 0.0

    mom12 = float(row["mom12"]) if np.isfinite(row["mom12"]) else np.nan
    z = float(row["z"]) if np.isfinite(row["z"]) else np.nan
    vr = float(row["vol_ratio"]) if np.isfinite(row["vol_ratio"]) else 1.0
    reg = regime_of(float(row["vol_z_reg"]) if np.isfinite(row["vol_z_reg"]) else np.nan)
    shock = 0.55 if reg == "shock" else 1.0

    if perm == "MOM":
        # London open continuation (strongest causal edge)
        if sess == "London" and hour in (8, 9) and np.isfinite(mom12) and abs(mom12) >= p.lon_thr:
            return float(np.sign(mom12)), "mom_london", p.lon_hold, min(MAX_LEV, p.lev_lon * shock)

        # NY continuation
        if sess == "NY" and hour in (16, 17) and np.isfinite(mom12) and abs(mom12) >= p.ny_thr:
            return float(np.sign(mom12)), "mom_ny", p.ny_hold, min(MAX_LEV, p.lev_ny * shock)

        # Overlap continuation (optional)
        if p.use_overlap and sess == "Overlap" and hour in (12, 13, 14):
            if np.isfinite(mom12) and abs(mom12) >= p.ol_thr and reg != "range":
                return float(np.sign(mom12)), "mom_overlap", p.ol_hold, min(MAX_LEV, p.lev_ol * shock)

    # Asia MR: fade lagged burst when vol contracted
    if p.use_asia_mr and perm == "MR" and sess == "Asia" and hour in (2, 3, 4, 5):
        if np.isfinite(z) and abs(z) >= p.asia_z and (vr <= 0.95 or reg == "range"):
            return float(-np.sign(z)), "mr_asia", p.asia_hold, min(MAX_LEV, p.lev_asia)

    return 0.0, "flat", 0, 0.0


@dataclass
class SimResult:
    equity: pd.Series
    trades: int
    hits: int
    gross_pnl: float
    net_pnl: float
    max_dd: float
    daily_breach: int
    hwm_breach: int
    lev_breach: int
    pass_challenge: bool
    fail_reason: str
    monthly_returns: list
    sharpe: float
    sortino: float
    hitrate: float
    payoff: float
    expectancy: float
    meta: dict = field(default_factory=dict)


def run_backtest(
    df: pd.DataFrame,
    p: StrategyParams | None = None,
    start_idx: int | None = None,
    end_idx: int | None = None,
    initial: float = ACCOUNT,
    challenge_mode: bool = False,
    challenge_days: int | None = None,
) -> SimResult:
    p = p or StrategyParams()
    d = add_features(df, p)
    if start_idx is None:
        start_idx = p.min_bars_warmup
    if end_idx is None:
        end_idx = len(d) - 1
    start_idx = max(int(start_idx), p.min_bars_warmup)
    end_idx = min(int(end_idx), len(d) - 1)

    equity = float(initial)
    peak = float(initial)
    day_start = float(initial)
    cur_date = None
    pos = 0.0
    hold = 0
    cool = 0
    day_trades = 0
    day_paused = False
    trades = 0
    costs = 0.0
    daily_breach = 0
    hwm_breach = 0
    lev_breach = 0
    failed = False
    fail_reason = ""
    passed = False
    entry_eq = None
    trade_pnls: list[float] = []
    eq_path: list[float] = []
    eq_idx: list = []
    challenge_end = None
    seen_hwm = False
    seen_daily = False

    def flatten(add_cool: bool = True) -> None:
        nonlocal pos, hold, entry_eq, costs, equity, cool
        if pos == 0:
            return
        c = equity * abs(pos) * (COST_BPS_SIDE / 10000.0)
        costs += c
        equity -= c
        if entry_eq is not None:
            trade_pnls.append((equity - entry_eq) / max(entry_eq, 1e-9))
        pos = 0.0
        hold = 0
        entry_eq = None
        if add_cool:
            cool = max(cool, p.cooldown)

    for i in range(start_idx, end_idx + 1):
        row = d.iloc[i]
        dt = row["dt"]
        date = row["date"]

        if cur_date is None:
            cur_date = date
            day_start = equity
            if challenge_mode and challenge_days:
                challenge_end = date + pd.Timedelta(days=int(challenge_days))

        if date != cur_date:
            dp = (equity - day_start) / day_start if day_start else 0.0
            if dp <= -DAILY_FAIL and not seen_daily:
                daily_breach += 1
                if challenge_mode:
                    failed = True
                    fail_reason = "daily_loss"
            cur_date = date
            day_start = equity
            day_trades = 0
            day_paused = False
            seen_daily = False
            if challenge_mode and challenge_end is not None and date > challenge_end:
                break

        if failed:
            eq_path.append(equity)
            eq_idx.append(dt)
            continue

        if cool > 0:
            cool -= 1

        ret = float(row["ret"]) if np.isfinite(row["ret"]) else 0.0
        if pos != 0:
            equity *= 1.0 + pos * ret

        if equity > peak:
            peak = equity
            seen_hwm = False
        dp = (equity - day_start) / day_start if day_start else 0.0
        dd = (peak - equity) / peak if peak else 0.0

        if pos != 0 and entry_eq is not None:
            if (entry_eq - equity) / entry_eq >= p.trade_stop:
                flatten(True)

        if dp <= -DAILY_FAIL:
            flatten(True)
            if not seen_daily:
                daily_breach += 1
                seen_daily = True
            fail_reason = "daily_loss"
            day_paused = True
            if challenge_mode:
                failed = True
                eq_path.append(equity)
                eq_idx.append(dt)
                continue

        if dd >= MAX_DD_FAIL:
            had_pos = pos != 0.0
            flatten(True)
            if not seen_hwm:
                hwm_breach += 1
                seen_hwm = True
            fail_reason = "max_dd"
            if challenge_mode:
                failed = True
                eq_path.append(equity)
                eq_idx.append(dt)
                continue
            # research mode: ratchet peak down toward equity to restore a tradable DD budget
            peak = equity / (1.0 - min(0.04, p.hwm_stop) * 0.5)
            seen_hwm = False
            if not had_pos:
                # already flat: do not keep refreshing cooldown forever
                pass
            else:
                cool = max(cool, p.cooldown)

        if dp <= -p.daily_stop:
            day_paused = True
            flatten(True)
        # Judge redesign: no new entries after −1.0% day
        if dp <= -0.010:
            day_paused = True
        if dd >= p.hwm_stop:
            flatten(True)

        if hold > 0 and pos != 0:
            hold -= 1
            if hold == 0:
                flatten(True)

        allow = pos == 0 and not failed and not day_paused and cool == 0 and day_trades < p.max_trades_per_day
        if allow and challenge_mode and dd >= p.entry_dd_cap:
            allow = False
        if allow:
            direction, mode, h, lev = decide(row, p)
            if direction != 0 and h > 0 and lev > 0:
                if dd > 0.02:
                    lev *= max(0.4, 1.0 - dd / MAX_DD_FAIL)
                if lev > MAX_LEV:
                    lev_breach += 1
                    lev = MAX_LEV
                if lev >= 0.25:
                    c = equity * lev * (COST_BPS_SIDE / 10000.0)
                    costs += c
                    equity -= c
                    pos = direction * lev
                    hold = int(h)
                    trades += 1
                    day_trades += 1
                    entry_eq = equity

        if challenge_mode and (equity / initial - 1.0) >= PASS_PCT:
            passed = True
            flatten(False)
            eq_path.append(equity)
            eq_idx.append(dt)
            break

        eq_path.append(equity)
        eq_idx.append(dt)

    eq = pd.Series(eq_path, index=pd.DatetimeIndex(eq_idx), dtype=float)
    rets = eq.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(24 * 365)) if len(rets) > 10 and rets.std() > 0 else 0.0
    down = rets[rets < 0]
    sortino = float(rets.mean() / down.std() * np.sqrt(24 * 365)) if len(down) > 5 and down.std() > 0 else 0.0
    max_dd = float(((eq.cummax() - eq) / eq.cummax()).max()) if len(eq) else 0.0

    m_rets: list[float] = []
    if len(eq):
        m_eq = eq.resample("ME").last().dropna()
        prev = float(initial)
        for v in m_eq.values:
            m_rets.append(float(v / prev - 1.0))
            prev = float(v)

    if trade_pnls:
        arr = np.array(trade_pnls)
        wins = arr[arr > 0]
        losses = arr[arr <= 0]
        hitrate = float((arr > 0).mean())
        payoff = (
            float(wins.mean() / abs(losses.mean()))
            if len(wins) and len(losses) and abs(losses.mean()) > 0
            else 0.0
        )
        expectancy = float(arr.mean())
        hits = int((arr > 0).sum())
    else:
        hitrate = payoff = expectancy = 0.0
        hits = 0

    if challenge_mode and not failed and (equity / initial - 1.0) >= PASS_PCT:
        passed = True
    if challenge_mode and not passed and not failed:
        fail_reason = fail_reason or "timeout_no_pass"

    return SimResult(
        equity=eq,
        trades=trades,
        hits=hits,
        gross_pnl=float(equity - initial + costs),
        net_pnl=float(equity - initial),
        max_dd=max_dd,
        daily_breach=daily_breach,
        hwm_breach=hwm_breach,
        lev_breach=lev_breach,
        pass_challenge=bool(passed and not failed),
        fail_reason="" if passed else fail_reason,
        monthly_returns=m_rets,
        sharpe=sharpe,
        sortino=sortino,
        hitrate=hitrate,
        payoff=payoff,
        expectancy=expectancy,
        meta={"costs": costs, "final_equity": equity, "params": asdict(p)},
    )


def walk_forward(
    df: pd.DataFrame,
    p: StrategyParams,
    train_days: int = 180,
    test_days: int = 60,
    step_days: int = 60,
) -> list[dict]:
    results = []
    t0 = df["dt"].iloc[0]
    t_last = df["dt"].iloc[-1]
    cursor = t0 + pd.Timedelta(days=train_days)
    while cursor + pd.Timedelta(days=test_days) <= t_last:
        train_start = cursor - pd.Timedelta(days=train_days)
        test_end = cursor + pd.Timedelta(days=test_days)
        warm = df[(df["dt"] >= train_start) & (df["dt"] < test_end)].reset_index(drop=True)
        if len(warm) < p.min_bars_warmup + 48:
            cursor += pd.Timedelta(days=step_days)
            continue
        mask = warm["dt"] >= cursor
        if not mask.any():
            cursor += pd.Timedelta(days=step_days)
            continue
        start_i = max(int(mask.idxmax()), p.min_bars_warmup)
        res = run_backtest(warm, p, start_idx=start_i, challenge_mode=False)
        results.append(
            {
                "test_start": str(cursor),
                "test_end": str(test_end),
                "net_pnl_pct": res.net_pnl / ACCOUNT,
                "sharpe": res.sharpe,
                "max_dd": res.max_dd,
                "hitrate": res.hitrate,
                "expectancy": res.expectancy,
                "trades": res.trades,
                "daily_breach": res.daily_breach,
                "hwm_breach": res.hwm_breach,
            }
        )
        cursor += pd.Timedelta(days=step_days)
    return results


def prop_sims(
    df: pd.DataFrame,
    p: StrategyParams,
    n: int = 100,
    window_days: int = 60,
    seed: int = 42,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    min_i = p.min_bars_warmup
    max_i = len(df) - window_days * 24 - 2
    starts = rng.integers(min_i, max_i, size=n)
    rows = []
    for k, s in enumerate(starts):
        end = min(len(df) - 1, int(s) + window_days * 24)
        warm_start = max(0, int(s) - p.min_bars_warmup)
        chunk = df.iloc[warm_start : end + 1].reset_index(drop=True)
        start_idx = int(s) - warm_start
        res = run_backtest(
            chunk, p, start_idx=start_idx, challenge_mode=True, challenge_days=window_days, initial=ACCOUNT
        )
        rows.append(
            {
                "run": k,
                "start_ts": int(df.iloc[int(s)]["timestamp"]),
                "start_dt": str(df.iloc[int(s)]["dt"]),
                "passed": bool(res.pass_challenge),
                "fail_reason": res.fail_reason,
                "final_equity": float(res.meta["final_equity"]),
                "pnl_pct": float(res.meta["final_equity"] / ACCOUNT - 1.0),
                "max_dd": res.max_dd,
                "daily_breach": res.daily_breach,
                "hwm_breach": res.hwm_breach,
                "lev_breach": res.lev_breach,
                "trades": res.trades,
                "sharpe": res.sharpe,
            }
        )
    return pd.DataFrame(rows)


def research_tables(df: pd.DataFrame) -> dict[str, Any]:
    d = df.copy()
    hour = d.groupby("hour")["ret"].mean().mul(10000)
    sess = d.groupby("session")["ret"].agg(["mean", "std", "count"])
    sess["ann_sharpe"] = sess["mean"] / sess["std"] * np.sqrt(24 * 365)
    dow = d.groupby("dow")["ret"].mean().mul(10000)
    return {
        "hour_mean_bps": {int(k): float(v) for k, v in hour.items()},
        "session": {k: {kk: float(vv) for kk, vv in v.items()} for k, v in sess.to_dict().items()},
        "dow_mean_bps": {int(k): float(v) for k, v in dow.items()},
    }
