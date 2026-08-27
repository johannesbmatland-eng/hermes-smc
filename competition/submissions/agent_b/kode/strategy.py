#!/usr/bin/env python3
"""AGENT_B — Microstructure Hybrid (momentum + mean-reversion) for BTCUSD prop.

Session-aware MOM/MR switch, hard 3%/6%/5x risk, fees+slippage.
Entry: python -m kode.run_all  (from submissions/agent_b)
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "btcusd_hourly_public.csv"
REPORTS = ROOT / "reports"
RESEARCH = ROOT / "research"

ACCOUNT = 100_000.0
PASS_PCT = 0.10
DAILY_FAIL = 0.03
MAX_DD_FAIL = 0.06
MAX_LEV = 5.0

# Kraken-design costs (per side)
FEE_BPS = 6.0
SLIP_BPS = 4.0
COST_BPS_SIDE = FEE_BPS + SLIP_BPS  # 10 bps/side → 20 bps RT


@dataclass
class StrategyParams:
    z_lookback: int = 48
    burst_z: float = 1.75
    mr_z_enter: float = 1.35
    mom_hold: int = 2
    mr_hold: int = 2
    base_lev: float = 1.35
    burst_lev: float = 2.10
    shock_lev_mult: float = 0.45
    vol_target: float = 0.012  # hourly vol target scale
    daily_stop: float = 0.022  # cut before -3%
    hwm_stop: float = 0.050  # cut before -6%
    flatten_quiet: bool = True
    skip_hours: tuple = (1, 13, 19, 23)
    skip_dow: tuple = (3,)  # Thursday weak historically
    asia_prefer_mr: bool = True
    min_bars_warmup: int = 72


# Session permission: allowed modes
# MOM, MR, BOTH, NONE
SESSION_MATRIX = {
    "Asia": "MR",
    "London": "BOTH",
    "Overlap": "MOM",
    "NY": "BOTH",
    "Quiet": "MR",  # fade only; often flatten
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
    out["vol"] = out["ret"].rolling(p.z_lookback).std()
    out["z"] = out["ret"] / out["vol"].replace(0, np.nan)
    # distance from slow VWAP proxy (rolling mean of close)
    out["vwap_proxy"] = out["close"].rolling(24).mean()
    out["dev"] = (out["close"] - out["vwap_proxy"]) / out["vwap_proxy"]
    out["dev_z"] = out["dev"] / out["dev"].rolling(p.z_lookback).std().replace(0, np.nan)
    out["vol24"] = out["ret"].rolling(24).std()
    # expanding percentile for regime (causal)
    out["vol_pct"] = out["vol24"].expanding(min_periods=24 * 14).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
    )
    # faster approx for speed on large runs
    return out


def add_features_fast(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    out = df.copy()
    out["vol"] = out["ret"].rolling(p.z_lookback).std()
    out["z"] = out["ret"] / out["vol"].replace(0, np.nan)
    out["vwap_proxy"] = out["close"].rolling(24).mean()
    out["dev"] = (out["close"] - out["vwap_proxy"]) / out["vwap_proxy"]
    out["dev_z"] = out["dev"] / out["dev"].rolling(p.z_lookback).std().replace(0, np.nan)
    out["vol24"] = out["ret"].rolling(24).std()
    # rolling rank pct over 90d window (causal-ish, faster)
    win = 24 * 90
    v = out["vol24"]
    # percentile rank via rolling apply is slow; use rank of last vs window mean/std approx
    mu = v.rolling(win, min_periods=24 * 14).mean()
    sd = v.rolling(win, min_periods=24 * 14).std()
    out["vol_z_reg"] = (v - mu) / sd.replace(0, np.nan)
    return out


def regime_from_volz(vz: float) -> str:
    if not np.isfinite(vz):
        return "trend"
    if vz < -0.4:
        return "range"
    if vz > 1.2:
        return "shock"
    return "trend"


def decide_signal(row: pd.Series, p: StrategyParams) -> tuple[float, str]:
    """Return desired position direction in {-1,0,1} and mode label."""
    hour = int(row["hour"])
    dow = int(row["dow"])
    sess = row["session"]
    z = row["z"]
    dev_z = row["dev_z"]
    if not np.isfinite(z) or not np.isfinite(dev_z):
        return 0.0, "none"
    if hour in p.skip_hours or dow in p.skip_dow:
        return 0.0, "filter"
    perm = SESSION_MATRIX.get(sess, "NONE")
    if perm == "NONE":
        return 0.0, "blocked"
    if p.flatten_quiet and sess == "Quiet" and abs(z) < p.burst_z:
        return 0.0, "quiet_flat"

    reg = regime_from_volz(row.get("vol_z_reg", np.nan))
    burst = abs(z) >= p.burst_z

    # MOM: ride burst in permitted sessions
    if burst and perm in ("MOM", "BOTH"):
        if reg == "shock" and sess == "Asia":
            # Asia shock → still prefer fade
            if perm in ("MR", "BOTH"):
                return float(-np.sign(z)), "mr_asia_shock"
        return float(np.sign(z)), "mom_burst"

    # MR: fade stretch vs VWAP when no burst / Asia preference
    if perm in ("MR", "BOTH") and abs(dev_z) >= p.mr_z_enter:
        if burst and perm == "BOTH" and sess in ("London", "NY", "Overlap"):
            return float(np.sign(z)), "mom_priority"
        return float(-np.sign(dev_z)), "mr_fade"

    # mild continuation in London open hours without full burst
    if sess == "London" and hour in (8, 9, 10, 11) and abs(z) >= 0.9 and perm != "MR":
        return float(np.sign(z)), "mom_london"

    return 0.0, "flat"


def leverage_for(mode: str, vol: float, p: StrategyParams, regime: str) -> float:
    if mode.startswith("mom"):
        lev = p.burst_lev
    elif mode.startswith("mr"):
        lev = p.base_lev
    else:
        lev = 0.0
    if regime == "shock":
        lev *= p.shock_lev_mult
    if regime == "range" and mode.startswith("mom"):
        lev *= 0.7
    # vol scale
    if np.isfinite(vol) and vol > 0:
        scale = min(1.5, max(0.4, p.vol_target / vol))
        lev *= scale
    return float(min(MAX_LEV, max(0.0, lev)))


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
    d = add_features_fast(df, p)
    if start_idx is None:
        start_idx = p.min_bars_warmup
    if end_idx is None:
        end_idx = len(d) - 1
    start_idx = max(start_idx, p.min_bars_warmup)
    end_idx = min(end_idx, len(d) - 1)

    equity = initial
    peak = initial
    day_start_eq = initial
    cur_date = None
    position = 0.0  # signed leverage fraction of equity in BTC exposure
    entry_price = 0.0
    hold_left = 0
    mode = "flat"
    trades = 0
    wins = 0
    losses = 0
    win_pnl = 0.0
    loss_pnl = 0.0
    gross = 0.0
    costs = 0.0
    daily_breach = 0
    hwm_breach = 0
    lev_breach = 0
    failed = False
    fail_reason = ""
    passed = False
    eq_path = []
    eq_idx = []
    trade_rets = []
    monthly = {}

    challenge_end_date = None

    i = start_idx
    while i <= end_idx:
        row = d.iloc[i]
        px = float(row["close"])
        dt = row["dt"]
        date = row["date"]

        if cur_date is None:
            cur_date = date
            day_start_eq = equity
            if challenge_mode and challenge_days:
                challenge_end_date = date + pd.Timedelta(days=challenge_days)

        # new day
        if date != cur_date:
            # daily fail check at day boundary on prior day PnL
            day_pnl_pct = (equity - day_start_eq) / day_start_eq if day_start_eq else 0.0
            if day_pnl_pct <= -DAILY_FAIL:
                daily_breach += 1
                failed = True
                fail_reason = "daily_loss"
            cur_date = date
            day_start_eq = equity
            if challenge_mode and challenge_end_date is not None and date > challenge_end_date:
                break

        if failed:
            eq_path.append(equity)
            eq_idx.append(dt)
            i += 1
            continue

        # mark-to-market previous position on bar return
        bar_ret = float(row["ret"]) if np.isfinite(row["ret"]) else 0.0
        if position != 0.0 and np.isfinite(bar_ret):
            equity *= 1.0 + position * bar_ret

        # risk checks intraday
        day_pnl_pct = (equity - day_start_eq) / day_start_eq if day_start_eq else 0.0
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if equity > peak:
            peak = equity

        if day_pnl_pct <= -p.daily_stop or day_pnl_pct <= -DAILY_FAIL:
            if position != 0:
                costs += equity * abs(position) * (COST_BPS_SIDE / 10000.0)
                equity -= equity * abs(position) * (COST_BPS_SIDE / 10000.0)
                position = 0.0
                hold_left = 0
            if day_pnl_pct <= -DAILY_FAIL:
                daily_breach += 1
                failed = True
                fail_reason = "daily_loss"
            eq_path.append(equity)
            eq_idx.append(dt)
            i += 1
            continue

        if dd >= p.hwm_stop or dd >= MAX_DD_FAIL:
            if position != 0:
                costs += equity * abs(position) * (COST_BPS_SIDE / 10000.0)
                equity -= equity * abs(position) * (COST_BPS_SIDE / 10000.0)
                position = 0.0
                hold_left = 0
            if dd >= MAX_DD_FAIL:
                hwm_breach += 1
                failed = True
                fail_reason = "max_dd"
            eq_path.append(equity)
            eq_idx.append(dt)
            i += 1
            continue

        # hold countdown / exit
        if hold_left > 0:
            hold_left -= 1
            if hold_left == 0 and position != 0:
                # exit
                exit_cost = equity * abs(position) * (COST_BPS_SIDE / 10000.0)
                costs += exit_cost
                equity -= exit_cost
                # approximate trade pnl vs entry via equity already marked; track hit via last move
                position = 0.0
                mode = "flat"

        # flatten quiet
        if p.flatten_quiet and row["session"] == "Quiet" and position != 0 and hold_left == 0:
            exit_cost = equity * abs(position) * (COST_BPS_SIDE / 10000.0)
            costs += exit_cost
            equity -= exit_cost
            position = 0.0

        # new entries only when flat
        if position == 0.0 and not failed:
            direction, sig_mode = decide_signal(row, p)
            if direction != 0.0:
                reg = regime_from_volz(row.get("vol_z_reg", np.nan))
                lev = leverage_for(sig_mode, float(row["vol"]) if np.isfinite(row["vol"]) else p.vol_target, p, reg)
                if abs(lev) > MAX_LEV + 1e-9:
                    lev_breach += 1
                    lev = MAX_LEV
                if lev > 0:
                    # entry cost
                    entry_cost = equity * lev * (COST_BPS_SIDE / 10000.0)
                    costs += entry_cost
                    equity -= entry_cost
                    position = direction * lev
                    entry_price = px
                    mode = sig_mode
                    hold_left = p.mom_hold if sig_mode.startswith("mom") else p.mr_hold
                    trades += 1
                    # record signed trade outcome later at exit via stored
                    trade_rets.append(0.0)  # placeholder filled roughly

        # challenge pass
        if challenge_mode and (equity / initial - 1.0) >= PASS_PCT:
            passed = True
            # flatten and stop
            if position != 0:
                exit_cost = equity * abs(position) * (COST_BPS_SIDE / 10000.0)
                costs += exit_cost
                equity -= exit_cost
                position = 0.0
            eq_path.append(equity)
            eq_idx.append(dt)
            break

        # track monthly
        mk = (dt.year, dt.month)
        monthly[mk] = equity

        eq_path.append(equity)
        eq_idx.append(dt)
        i += 1

    eq = pd.Series(eq_path, index=pd.DatetimeIndex(eq_idx), dtype=float)
    rets = eq.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(24 * 365)) if len(rets) > 10 and rets.std() > 0 else 0.0
    downside = rets[rets < 0]
    sortino = float(rets.mean() / downside.std() * np.sqrt(24 * 365)) if len(downside) > 5 and downside.std() > 0 else 0.0
    peak_s = eq.cummax()
    dd_s = (peak_s - eq) / peak_s
    max_dd = float(dd_s.max()) if len(dd_s) else 0.0

    # monthly returns from month-end equity marks
    m_items = sorted(monthly.items())
    m_rets = []
    prev = initial
    # rebuild month ends from equity series
    if len(eq):
        m_eq = eq.resample("ME").last().dropna()
        if len(m_eq):
            # first month vs initial
            vals = m_eq.values
            prev_v = initial
            for v in vals:
                m_rets.append(float(v / prev_v - 1.0))
                prev_v = v

    # trade hitrate approximation from bar strategy: use signal PnL samples
    # Recompute simple trade stats from equity path jumps around entries — use return sign of held bars
    hitrate = 0.0
    payoff = 0.0
    expectancy = 0.0
    # Use offline quick trade replay for stats
    tr_stats = _trade_stats_quick(d.iloc[start_idx : end_idx + 1], p)
    hitrate = tr_stats["hitrate"]
    payoff = tr_stats["payoff"]
    expectancy = tr_stats["expectancy"]
    trades = max(trades, tr_stats["trades"])

    net_pnl = float(equity - initial)
    if challenge_mode and not failed and (equity / initial - 1.0) >= PASS_PCT:
        passed = True
    if challenge_mode and not passed and not failed:
        fail_reason = fail_reason or "timeout_no_pass"

    return SimResult(
        equity=eq,
        trades=int(trades),
        hits=int(tr_stats["hits"]),
        gross_pnl=float(net_pnl + costs),
        net_pnl=net_pnl,
        max_dd=max_dd,
        daily_breach=daily_breach,
        hwm_breach=hwm_breach,
        lev_breach=lev_breach,
        pass_challenge=passed and not failed,
        fail_reason=fail_reason if not passed else "",
        monthly_returns=m_rets,
        sharpe=sharpe,
        sortino=sortino,
        hitrate=hitrate,
        payoff=payoff,
        expectancy=expectancy,
        meta={"costs": costs, "final_equity": equity, "params": asdict(p)},
    )


def _trade_stats_quick(d: pd.DataFrame, p: StrategyParams) -> dict:
    """Causal trade list for hitrate/payoff/expectancy after costs."""
    pnls = []
    i = 0
    n = len(d)
    while i < n - 3:
        row = d.iloc[i]
        direction, mode = decide_signal(row, p)
        if direction == 0:
            i += 1
            continue
        hold = p.mom_hold if mode.startswith("mom") else p.mr_hold
        j = min(i + hold, n - 1)
        px0 = float(d.iloc[i]["close"])
        px1 = float(d.iloc[j]["close"])
        raw = direction * (px1 / px0 - 1.0)
        # costs as fraction of notional; lev cancelled in unit trade pnl
        net = raw - 2 * (COST_BPS_SIDE / 10000.0)
        pnls.append(net)
        i = j + 1
    if not pnls:
        return {"hitrate": 0.0, "payoff": 0.0, "expectancy": 0.0, "trades": 0, "hits": 0}
    arr = np.array(pnls)
    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    hitrate = float((arr > 0).mean())
    payoff = float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) and abs(losses.mean()) > 0 else 0.0
    return {
        "hitrate": hitrate,
        "payoff": payoff,
        "expectancy": float(arr.mean()),
        "trades": int(len(arr)),
        "hits": int((arr > 0).sum()),
    }


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
        mask = (df["dt"] >= cursor) & (df["dt"] < test_end)
        # need warmup before test
        warm = df[(df["dt"] >= train_start) & (df["dt"] < test_end)].reset_index(drop=True)
        if len(warm) < p.min_bars_warmup + 24:
            cursor += pd.Timedelta(days=step_days)
            continue
        # start index = first test bar
        start_i = int((warm["dt"] >= cursor).idxmax()) if False else int(np.argmax(warm["dt"].values >= np.datetime64(cursor)))
        # robust:
        start_i = int(np.searchsorted(warm["dt"].values, np.datetime64(cursor)))
        start_i = max(start_i, p.min_bars_warmup)
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
    window_days: int = 45,
    seed: int = 42,
) -> pd.DataFrame:
    """Randomized start prop challenges: pass +10% before daily -3% or DD -6%."""
    rng = np.random.default_rng(seed)
    # valid start indices
    min_i = p.min_bars_warmup
    max_i = len(df) - window_days * 24 - 2
    if max_i <= min_i:
        raise ValueError("Not enough data for prop sims")
    starts = rng.integers(min_i, max_i, size=n)
    rows = []
    for k, s in enumerate(starts):
        end = min(len(df) - 1, s + window_days * 24)
        # include warmup before s
        warm_start = max(0, s - p.min_bars_warmup)
        chunk = df.iloc[warm_start : end + 1].reset_index(drop=True)
        start_idx = s - warm_start
        res = run_backtest(
            chunk,
            p,
            start_idx=start_idx,
            challenge_mode=True,
            challenge_days=window_days,
            initial=ACCOUNT,
        )
        rows.append(
            {
                "run": k,
                "start_ts": int(df.iloc[s]["timestamp"]),
                "start_dt": str(df.iloc[s]["dt"]),
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
    d["absret"] = d["ret"].abs()
    hour = d.groupby("hour")["ret"].mean().mul(10000)
    sess = d.groupby("session")["ret"].agg(["mean", "std", "count"])
    sess["ann_sharpe"] = sess["mean"] / sess["std"] * np.sqrt(24 * 365)
    dow = d.groupby("dow")["ret"].mean().mul(10000)
    return {
        "hour_mean_bps": {int(k): float(v) for k, v in hour.items()},
        "session": sess.round(6).to_dict(),
        "dow_mean_bps": {int(k): float(v) for k, v in dow.items()},
    }


if __name__ == "__main__":
    print("Use kode.run_all")
