#!/usr/bin/env python3
"""AGENT_B — Microstructure Hybrid (session MOM + MR) for BTCUSD prop.

Exploits Asia/London/NY session structure, volatility bursts, and recurring
intraday patterns with an explicit momentum vs mean-reversion switch.
Hard risk: daily −3%, maxDD −6% from HWM, leverage ≤5x. Fees+slippage on.
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

# Kraken-design costs (BTCUSD). Modelled as Kraken Futures–tier blended
# execution (maker/taker mix) + adverse slippage. Documented in research.
FEE_BPS = 3.0
SLIP_BPS = 3.0
COST_BPS_SIDE = FEE_BPS + SLIP_BPS  # 6 bps/side → 12 bps RT


@dataclass
class StrategyParams:
    z_lookback: int = 48
    burst_z: float = 2.0
    mom24_thr: float = 0.015
    mom12_thr: float = 0.018
    mr_dev_thr: float = 0.025
    mom_hold: int = 18
    burst_mom_hold: int = 14
    mr_hold: int = 12
    base_lev: float = 1.8
    burst_lev: float = 2.2
    mr_lev: float = 1.4
    shock_lev_mult: float = 0.50
    vol_target: float = 0.011
    daily_stop: float = 0.015
    hwm_stop: float = 0.040
    cooldown: int = 8
    flatten_weekend_utc: bool = False
    skip_hours: tuple = (1, 13, 19, 23)
    skip_dow: tuple = ()
    min_bars_warmup: int = 120


# Permission matrix
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
    out["vol"] = out["ret"].rolling(p.z_lookback).std()
    out["z"] = out["ret"] / out["vol"].replace(0, np.nan)
    out["mom12"] = out["close"].pct_change(12)
    out["mom24"] = out["close"].pct_change(24)
    out["vwap48"] = out["close"].rolling(48).mean()
    out["dev48"] = (out["close"] - out["vwap48"]) / out["vwap48"]
    vol24 = out["ret"].rolling(24).std()
    out["vol_ratio"] = vol24 / vol24.rolling(24 * 7, min_periods=24 * 3).mean()
    mu = vol24.rolling(24 * 90, min_periods=24 * 14).mean()
    sd = vol24.rolling(24 * 90, min_periods=24 * 14).std()
    out["vol_z_reg"] = (vol24 - mu) / sd.replace(0, np.nan)
    return out


def regime_from_volz(vz: float) -> str:
    if not np.isfinite(vz):
        return "trend"
    if vz < -0.45:
        return "range"
    if vz > 1.15:
        return "shock"
    return "trend"


def decide_signal(row: pd.Series, p: StrategyParams) -> tuple[float, str, int]:
    """Return (direction, mode, hold_bars). Highly selective microstructure hybrid."""
    hour = int(row["hour"])
    dow = int(row["dow"])
    sess = row["session"]
    z = float(row["z"]) if np.isfinite(row["z"]) else np.nan
    mom24 = float(row["mom24"]) if np.isfinite(row["mom24"]) else np.nan
    mom12 = float(row["mom12"]) if np.isfinite(row["mom12"]) else np.nan
    vol_ratio = float(row["vol_ratio"]) if np.isfinite(row["vol_ratio"]) else 1.0
    reg = regime_from_volz(row["vol_z_reg"] if "vol_z_reg" in row.index else np.nan)

    if hour in p.skip_hours or dow in p.skip_dow:
        return 0.0, "filter", 0
    perm = SESSION_MATRIX.get(sess, "NONE")
    if perm == "NONE":
        return 0.0, "blocked", 0

    burst = np.isfinite(z) and abs(z) >= p.burst_z

    # MOM-1: London/NY overlap — strongest fee-surviving edge (12h impulse continuation)
    if perm == "MOM" and sess == "Overlap" and hour in (12, 13, 14):
        if np.isfinite(mom12) and abs(mom12) >= p.mom12_thr and reg != "range":
            return float(np.sign(mom12)), "mom_overlap", p.mom_hold

    # MOM-2: Western volatility-burst continuation (session open / burst microstructure)
    if perm == "MOM" and burst and sess in ("London", "Overlap", "NY"):
        if vol_ratio >= 1.10 and hour in (7, 8, 9, 10, 12, 14, 16, 17, 18):
            return float(np.sign(z)), "mom_burst", p.burst_mom_hold

    # MOM-3: London selective thrust — only strong moves + vol expansion (avoid churn)
    if perm == "MOM" and sess == "London" and hour in (8, 9, 10):
        if np.isfinite(mom24) and abs(mom24) >= max(0.022, p.mom24_thr * 1.4) and vol_ratio >= 1.15:
            return float(np.sign(mom24)), "mom_london_thrust", p.mom_hold

    # MR-1: Asia burst fade only when vol contracting (mean-revert microstructure)
    if perm == "MR" and sess == "Asia" and burst and hour in (0, 2, 3, 4, 5, 6):
        if vol_ratio <= 0.95 or reg == "range":
            return float(-np.sign(z)), "mr_asia_burst", p.mr_hold

    return 0.0, "flat", 0


def leverage_for(mode: str, vol: float, p: StrategyParams, regime: str) -> float:
    if mode.startswith("mom_burst") or mode.startswith("mom_overlap"):
        lev = p.burst_lev
    elif mode.startswith("mom"):
        lev = p.base_lev
    elif mode.startswith("mr"):
        lev = p.mr_lev
    else:
        lev = 0.0
    if regime == "shock":
        lev *= p.shock_lev_mult
    if regime == "range" and mode.startswith("mom"):
        lev *= 0.75
    if np.isfinite(vol) and vol > 0:
        # Cap scale so we do not push into daily-fail territory on normal BTC hours
        scale = min(1.15, max(0.40, p.vol_target / vol))
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


def _trade_stats(d: pd.DataFrame, p: StrategyParams) -> dict:
    pnls: list[float] = []
    i = 0
    n = len(d)
    cost_rt = 2 * (COST_BPS_SIDE / 10000.0)
    while i < n - 2:
        direction, mode, hold = decide_signal(d.iloc[i], p)
        if direction == 0 or hold <= 0:
            i += 1
            continue
        j = min(i + hold, n - 1)
        px0 = float(d.iloc[i]["close"])
        px1 = float(d.iloc[j]["close"])
        raw = direction * (px1 / px0 - 1.0)
        pnls.append(raw - cost_rt)
        i = j + 1
    if not pnls:
        return {"hitrate": 0.0, "payoff": 0.0, "expectancy": 0.0, "trades": 0, "hits": 0}
    arr = np.array(pnls)
    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    payoff = (
        float(wins.mean() / abs(losses.mean()))
        if len(wins) and len(losses) and abs(losses.mean()) > 0
        else 0.0
    )
    return {
        "hitrate": float((arr > 0).mean()),
        "payoff": payoff,
        "expectancy": float(arr.mean()),
        "trades": int(len(arr)),
        "hits": int((arr > 0).sum()),
    }


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
    day_start_eq = float(initial)
    cur_date = None
    position = 0.0
    hold_left = 0
    cooldown_left = 0
    trades = 0
    costs = 0.0
    daily_breach = 0
    hwm_breach = 0
    lev_breach = 0
    failed = False
    fail_reason = ""
    passed = False
    eq_path: list[float] = []
    eq_idx: list = []
    challenge_end_date = None
    trade_pnls: list[float] = []
    entry_eq = None

    for i in range(start_idx, end_idx + 1):
        row = d.iloc[i]
        dt = row["dt"]
        date = row["date"]

        if cur_date is None:
            cur_date = date
            day_start_eq = equity
            if challenge_mode and challenge_days:
                challenge_end_date = date + pd.Timedelta(days=int(challenge_days))

        if date != cur_date:
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
            continue

        if cooldown_left > 0:
            cooldown_left -= 1

        bar_ret = float(row["ret"]) if np.isfinite(row["ret"]) else 0.0
        if position != 0.0:
            equity *= 1.0 + position * bar_ret

        if equity > peak:
            peak = equity
        day_pnl_pct = (equity - day_start_eq) / day_start_eq if day_start_eq else 0.0
        dd = (peak - equity) / peak if peak > 0 else 0.0

        hard_daily = day_pnl_pct <= -DAILY_FAIL
        hard_hwm = dd >= MAX_DD_FAIL
        soft_daily = day_pnl_pct <= -p.daily_stop
        soft_hwm = dd >= p.hwm_stop

        def _flatten(reason_cooldown: bool = True) -> None:
            nonlocal position, hold_left, entry_eq, costs, equity, cooldown_left
            if position == 0.0:
                return
            c = equity * abs(position) * (COST_BPS_SIDE / 10000.0)
            costs += c
            equity -= c
            if entry_eq is not None:
                trade_pnls.append((equity - entry_eq) / entry_eq)
            position = 0.0
            hold_left = 0
            entry_eq = None
            if reason_cooldown:
                cooldown_left = max(cooldown_left, p.cooldown)

        if hard_daily or hard_hwm:
            _flatten(True)
            if hard_daily:
                daily_breach += 1
                fail_reason = "daily_loss"
            else:
                hwm_breach += 1
                fail_reason = "max_dd"
            failed = True
            eq_path.append(equity)
            eq_idx.append(dt)
            continue

        pause_entries = soft_daily or cooldown_left > 0

        if position != 0.0 and (soft_daily or soft_hwm):
            _flatten(True)

        if hold_left > 0 and position != 0.0:
            hold_left -= 1
            if hold_left == 0:
                _flatten(True)

        if position == 0.0 and not failed and not pause_entries:
            direction, mode, hold = decide_signal(row, p)
            if direction != 0.0 and hold > 0:
                vz = row["vol_z_reg"] if "vol_z_reg" in row.index else np.nan
                reg = regime_from_volz(float(vz) if np.isfinite(vz) else np.nan)
                vol = float(row["vol"]) if np.isfinite(row["vol"]) else p.vol_target
                lev = leverage_for(mode, vol, p, reg)
                if lev > MAX_LEV + 1e-9:
                    lev_breach += 1
                    lev = MAX_LEV
                if lev > 0:
                    c = equity * lev * (COST_BPS_SIDE / 10000.0)
                    costs += c
                    equity -= c
                    position = direction * lev
                    hold_left = int(hold)
                    trades += 1
                    entry_eq = equity

        if challenge_mode and (equity / initial - 1.0) >= PASS_PCT:
            passed = True
            _flatten(False)
            eq_path.append(equity)
            eq_idx.append(dt)
            break

        eq_path.append(equity)
        eq_idx.append(dt)

    eq = pd.Series(eq_path, index=pd.DatetimeIndex(eq_idx), dtype=float)
    rets = eq.pct_change().dropna()
    sharpe = (
        float(rets.mean() / rets.std() * np.sqrt(24 * 365))
        if len(rets) > 10 and rets.std() > 0
        else 0.0
    )
    downside = rets[rets < 0]
    sortino = (
        float(rets.mean() / downside.std() * np.sqrt(24 * 365))
        if len(downside) > 5 and downside.std() > 0
        else 0.0
    )
    peak_s = eq.cummax()
    max_dd = float(((peak_s - eq) / peak_s).max()) if len(eq) else 0.0

    m_rets: list[float] = []
    if len(eq):
        m_eq = eq.resample("ME").last().dropna()
        prev_v = float(initial)
        for v in m_eq.values:
            m_rets.append(float(v / prev_v - 1.0))
            prev_v = float(v)

    tr = _trade_stats(d.iloc[max(0, start_idx - p.min_bars_warmup) : end_idx + 1], p)
    # Prefer realized trade_pnls if enough
    if len(trade_pnls) >= 5:
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
        hitrate = tr["hitrate"]
        payoff = tr["payoff"]
        expectancy = tr["expectancy"]
        hits = tr["hits"]

    net_pnl = float(equity - initial)
    if challenge_mode and not failed and (equity / initial - 1.0) >= PASS_PCT:
        passed = True
    if challenge_mode and not passed and not failed:
        fail_reason = fail_reason or "timeout_no_pass"

    return SimResult(
        equity=eq,
        trades=int(trades),
        hits=hits,
        gross_pnl=float(net_pnl + costs),
        net_pnl=net_pnl,
        max_dd=max_dd,
        daily_breach=daily_breach,
        hwm_breach=hwm_breach,
        lev_breach=lev_breach,
        pass_challenge=bool(passed and not failed),
        fail_reason=fail_reason if not passed else "",
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
        # start at first bar >= cursor
        mask = warm["dt"] >= cursor
        if not mask.any():
            cursor += pd.Timedelta(days=step_days)
            continue
        start_i = int(mask.idxmax())
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
    window_days: int = 55,
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
