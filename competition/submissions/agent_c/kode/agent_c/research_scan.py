"""Research scans for TOD / DOW / regime / trigger statistics (hourly)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .features import build_features
from .config import DEFAULT_PARAMS


def forward_abs_return(close: np.ndarray, horizon: int) -> np.ndarray:
    out = np.full_like(close, np.nan, dtype=np.float64)
    for i in range(len(close) - horizon):
        out[i] = abs(close[i + horizon] / close[i] - 1.0)
    return out


def market_study_tables(df: pd.DataFrame, horizon: int = 24) -> dict[str, Any]:
    try:
        feat = build_features(df, DEFAULT_PARAMS)
    except Exception:
        feat = df.copy()
        feat["hour"] = pd.to_datetime(feat["timestamp"]).dt.hour
        feat["dow"] = pd.to_datetime(feat["timestamp"]).dt.dayofweek
        feat["fwd_abs"] = forward_abs_return(feat["close"].to_numpy(dtype=np.float64), horizon)
        tod = feat.dropna(subset=["fwd_abs"]).groupby("hour")["fwd_abs"].agg(["mean", "median", "count"]).reset_index()
        dow = feat.dropna(subset=["fwd_abs"]).groupby("dow")["fwd_abs"].agg(["mean", "median", "count"]).reset_index()
        return {
            "horizon_hours": horizon,
            "tod": tod.to_dict(orient="records"),
            "dow": dow.to_dict(orient="records"),
            "regime": [],
            "triggers": [],
            "false_break": {},
            "n_bars": len(feat),
            "start": str(feat["timestamp"].iloc[0]),
            "end": str(feat["timestamp"].iloc[-1]),
        }

    c = feat["close"].to_numpy(dtype=np.float64)
    feat = feat.copy()
    feat["fwd_abs"] = forward_abs_return(c, horizon)

    tod = (
        feat.dropna(subset=["fwd_abs"]).groupby("hour")["fwd_abs"].agg(["mean", "median", "count"]).reset_index()
        .sort_values("mean", ascending=False)
    )
    dow = feat.dropna(subset=["fwd_abs"]).groupby("dow")["fwd_abs"].agg(["mean", "median", "count"]).reset_index()
    dow["dow_name"] = dow["dow"].map({0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"})

    x = feat.dropna(subset=["er", "fwd_abs"]).copy()
    if len(x) > 30:
        x["er_bucket"] = pd.qcut(x["er"], 3, labels=["chop", "mid", "trend"])
        regime = x.groupby("er_bucket", observed=True)["fwd_abs"].agg(["mean", "median", "count"]).reset_index()
    else:
        regime = pd.DataFrame()

    y = feat.dropna(subset=["atr_ratio", "vol_ratio", "fwd_abs", "donch_up", "donch_dn"]).copy()
    y["near_break"] = (y["close"] >= y["donch_up"] * 0.998) | (y["close"] <= y["donch_dn"] * 1.002)
    y["atr_hi"] = y["atr_ratio"] >= 1.25
    y["vol_hi"] = y["vol_ratio"] >= 1.4
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

    return {
        "horizon_hours": horizon,
        "tod": tod.to_dict(orient="records"),
        "dow": dow.to_dict(orient="records"),
        "regime": regime.to_dict(orient="records") if len(regime) else [],
        "triggers": trigger_rows,
        "n_bars": len(feat),
        "start": str(feat["timestamp"].iloc[0]),
        "end": str(feat["timestamp"].iloc[-1]),
    }
