"""Discrete Markov regime model for BTCUSD with Bayes posterior updates."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import (
    RANGE_ABS_RET,
    ROUND_TRIP_COST_BPS,
    SHOCK_BAR_RET,
    SHOCK_CUM3,
    SHOCK_VOL_Z,
    STATE_IDX,
    STATES,
    TREND_ABS_RET,
    TREND_LOOKBACK,
    VOL_LOOKBACK,
)


@dataclass
class MarkovFit:
    states: tuple[str, ...]
    transition: np.ndarray
    emission_mean: np.ndarray
    emission_std: np.ndarray
    edge_after_cost: np.ndarray  # directional long-edge expectancy over hold
    edge_hold_bars: int
    prior: np.ndarray
    counts: np.ndarray
    label_series: np.ndarray
    # transition-conditional edge E[r_hold | s→s']
    transition_edge: np.ndarray


def classify_regimes(df: pd.DataFrame) -> np.ndarray:
    """Hard labels: trend/range/shock from causal (lagged) features."""
    close = df["close"].values.astype(float)
    ret = df["ret"].fillna(0.0).values.astype(float)
    n = len(df)
    labels = np.full(n, STATE_IDX["RANGE"], dtype=int)

    vol = pd.Series(ret).rolling(VOL_LOOKBACK, min_periods=24).std().values
    vol_med = pd.Series(vol).rolling(24 * 14, min_periods=24).median().values
    vol_mad = (
        pd.Series(np.abs(vol - vol_med))
        .rolling(24 * 14, min_periods=24)
        .median()
        .values
    )
    vol_z = (vol - vol_med) / np.maximum(1.4826 * vol_mad, 1e-8)

    lag = np.roll(close, TREND_LOOKBACK)
    lag[:TREND_LOOKBACK] = np.nan
    trend_ret = (close - lag) / lag

    cum3 = np.ones(n) * np.nan
    for i in range(3, n):
        cum3[i] = close[i] / close[i - 3] - 1.0

    for i in range(n):
        if not np.isfinite(trend_ret[i]) or not np.isfinite(vol_z[i]):
            labels[i] = STATE_IDX["RANGE"]
            continue
        shock = (
            vol_z[i] >= SHOCK_VOL_Z
            or abs(ret[i]) >= SHOCK_BAR_RET
            or (np.isfinite(cum3[i]) and abs(cum3[i]) >= SHOCK_CUM3)
        )
        if shock:
            labels[i] = STATE_IDX["SHOCK"]
        elif trend_ret[i] >= TREND_ABS_RET:
            labels[i] = STATE_IDX["TREND_UP"]
        elif trend_ret[i] <= -TREND_ABS_RET:
            labels[i] = STATE_IDX["TREND_DOWN"]
        elif abs(trend_ret[i]) <= RANGE_ABS_RET:
            labels[i] = STATE_IDX["RANGE"]
        else:
            labels[i] = (
                STATE_IDX["TREND_UP"] if trend_ret[i] > 0 else STATE_IDX["TREND_DOWN"]
            )
    return labels


def fit_markov(
    df: pd.DataFrame,
    cost_bps_roundtrip: float = ROUND_TRIP_COST_BPS,
    hold: int = 36,
) -> MarkovFit:
    labels = classify_regimes(df)
    k = len(STATES)
    counts = np.ones((k, k), dtype=float)
    for t in range(len(labels) - 1):
        counts[labels[t], labels[t + 1]] += 1.0
    transition = counts / counts.sum(axis=1, keepdims=True)

    close = df["close"].values.astype(float)
    n = len(df)
    cost = cost_bps_roundtrip / 10_000.0

    # Hold-horizon forward returns from each state (LONG bias for BTC recovery)
    fwd = np.full(n, np.nan)
    for i in range(n - hold):
        fwd[i] = close[i + hold] / close[i] - 1.0

    emission_mean = np.zeros(k)
    emission_std = np.ones(k) * 0.01
    edge = np.zeros(k)
    for s in range(k):
        mask = (labels == s) & np.isfinite(fwd)
        if mask.sum() > 40:
            emission_mean[s] = float(np.nanmean(fwd[mask]))
            emission_std[s] = float(np.nanstd(fwd[mask]) + 1e-8)
        # Long-only edge after costs (BTCUSD structural long bias in shock/recovery)
        edge[s] = emission_mean[s] - cost

    # Transition-conditional edges
    te = np.zeros((k, k))
    te_counts = np.zeros((k, k))
    for i in range(1, n - hold):
        a, b = labels[i - 1], labels[i]
        if np.isfinite(fwd[i]):
            te[a, b] += fwd[i] - cost
            te_counts[a, b] += 1
    te = np.where(te_counts > 20, te / np.maximum(te_counts, 1), 0.0)

    # Refine state edges: SHOCK uses dump-conditional long edge
    ret = df["ret"].fillna(0.0).values
    cum3 = pd.Series(close).pct_change(3).values
    shock_mask = (labels == STATE_IDX["SHOCK"]) & (cum3 <= -SHOCK_CUM3) & np.isfinite(fwd)
    if shock_mask.sum() > 30:
        edge[STATE_IDX["SHOCK"]] = float(np.nanmean(fwd[shock_mask]) - cost)

    # TREND_DOWN: weak / negative for long; set near 0 after cost unless positive
    # Prefer transition edges for trading decisions
    prior = np.bincount(labels, minlength=k).astype(float)
    prior = prior / prior.sum()

    return MarkovFit(
        states=STATES,
        transition=transition,
        emission_mean=emission_mean,
        emission_std=emission_std,
        edge_after_cost=edge,
        edge_hold_bars=hold,
        prior=prior,
        counts=counts,
        label_series=labels,
        transition_edge=te,
    )


def bayes_update(
    posterior: np.ndarray,
    transition: np.ndarray,
    emission_mean: np.ndarray,
    emission_std: np.ndarray,
    observed_fwd_proxy: float,
) -> np.ndarray:
    """Predict with P, update with Gaussian emission on observed return proxy."""
    predictive = posterior @ transition
    ll = np.exp(-0.5 * ((observed_fwd_proxy - emission_mean) / emission_std) ** 2)
    ll = ll / (emission_std * np.sqrt(2 * np.pi))
    unnorm = predictive * np.maximum(ll, 1e-300)
    s = unnorm.sum()
    if s <= 0 or not np.isfinite(s):
        return predictive / max(predictive.sum(), 1e-12)
    return unnorm / s


def hard_state(posterior: np.ndarray) -> int:
    return int(np.argmax(posterior))


def format_transition(fit: MarkovFit) -> pd.DataFrame:
    return pd.DataFrame(fit.transition, index=list(STATES), columns=list(STATES))
