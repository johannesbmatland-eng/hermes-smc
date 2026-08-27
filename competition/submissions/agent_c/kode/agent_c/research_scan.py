"""Research scans for TOD / DOW / regime / trigger statistics."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .features import build_features, true_range, wilder_atr
from .config import DEFAULT_PARAMS


def forward_abs_return(close: np.ndarray, horizon: int) -> np.ndarray:
    out = np.full_like(close, np.nan, dtype=np.float64)
    for i in range(len(close) - horizon):
        out[i] = abs(close[i + horizon] / close[i] - 1.0)
    return out


def forward_signed_return(close: np.ndarray, horizon: int) -> np.ndarray:
    out = np.full_like(close, np.nan, dtype=np.float64)
    for i in range(len(close) - horizon):
        out[i] = close[i + horizon] / close[i] - 1.0
    return out


def market_study_tables(df: pd.DataFrame, horizon: int = 24) -> dict[str, Any]:
    p = DEFAULT_PARAMS
    feat = build_features(df, p)
    c = feat["close"].to_numpy(dtype=np.float64)
    fwd = forward_abs_return(c, horizon)
    fwd_s = forward_signed_return(c, horizon)
    feat = feat.copy()
    feat["fwd_abs"] = fwd
    feat["fwd_signed"] = fwd_s

    # TOD
    tod = (
        feat.dropna(subset=["fwd_abs"])
        .groupby("hour")["fwd_abs"]
        .agg(["mean", "median", "count"])
        .reset_index()
        .sort_values("mean", ascending=False)
    )

    # DOW
    dow = (
        feat.dropna(subset=["fwd_abs"])
        .groupby("dow")["fwd_abs"]
        .agg(["mean", "median", "count"])
        .reset_index()
    )
    dow["dow_name"] = dow["dow"].map(
        {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    )

    # Regime via ER terciles
    x = feat.dropna(subset=["er", "fwd_abs"]).copy()
    x["er_bucket"] = pd.qcut(x["er"], 3, labels=["chop", "mid", "trend"])
    regime = x.groupby("er_bucket", observed=True)["fwd_abs"].agg(["mean", "median", "count"]).reset_index()

    # Triggers: ATR expansion & vol surge & range proximity
    y = feat.dropna(subset=["atr_ratio", "vol_ratio", "fwd_abs", "donch_up", "donch_dn"]).copy()
    y["near_break"] = (y["close"] >= y["donch_up"] * 0.998) | (y["close"] <= y["donch_dn"] * 1.002)
    y["atr_hi"] = y["atr_ratio"] >= 1.35
    y["vol_hi"] = y["vol_ratio"] >= 1.6
    trigger_rows = []
    for name, mask in [
        ("baseline", np.ones(len(y), dtype=bool)),
        ("atr_expand", y["atr_hi"].to_numpy()),
        ("vol_surge", y["vol_hi"].to_numpy()),
        ("near_range_edge", y["near_break"].to_numpy()),
        ("atr+vol", (y["atr_hi"] & y["vol_hi"]).to_numpy()),
        ("atr+vol+edge", (y["atr_hi"] & y["vol_hi"] & y["near_break"]).to_numpy()),
    ]:
        m = mask & np.isfinite(y["fwd_abs"].to_numpy())
        vals = y.loc[m, "fwd_abs"]
        trigger_rows.append(
            {
                "trigger": name,
                "n": int(m.sum()),
                "mean_fwd_abs": float(vals.mean()) if len(vals) else 0.0,
                "median_fwd_abs": float(vals.median()) if len(vals) else 0.0,
            }
        )

    # False break heuristic: break then close back inside within 3 bars
    false_break_rate = _false_break_stats(feat)

    return {
        "horizon_hours": horizon,
        "tod": tod.to_dict(orient="records"),
        "dow": dow.to_dict(orient="records"),
        "regime": regime.to_dict(orient="records"),
        "triggers": trigger_rows,
        "false_break": false_break_rate,
        "n_bars": len(feat),
        "start": str(feat["timestamp"].iloc[0]),
        "end": str(feat["timestamp"].iloc[-1]),
    }


def _false_break_stats(feat: pd.DataFrame) -> dict[str, Any]:
    up = feat["donch_up"].to_numpy()
    dn = feat["donch_dn"].to_numpy()
    c = feat["close"].to_numpy()
    n = len(c)
    breaks = 0
    false_b = 0
    for i in range(n - 4):
        if not np.isfinite(up[i]) or not np.isfinite(dn[i]):
            continue
        if c[i] > up[i]:
            breaks += 1
            if np.any(c[i + 1 : i + 4] < up[i]):
                false_b += 1
        elif c[i] < dn[i]:
            breaks += 1
            if np.any(c[i + 1 : i + 4] > dn[i]):
                false_b += 1
    return {
        "raw_breaks": breaks,
        "false_within_3bars": false_b,
        "false_rate": (false_b / breaks) if breaks else 0.0,
    }
