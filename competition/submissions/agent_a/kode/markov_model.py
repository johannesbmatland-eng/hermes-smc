"""Discrete Markov regime model for BTCUSD with Bayes posterior updates."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import (
    RANGE_ABS_RET,
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
    transition: np.ndarray  # P[i,j] = P(s'=j | s=i)
    emission_mean: np.ndarray  # E[r | s] next-bar mean
    emission_std: np.ndarray
    edge_after_cost: np.ndarray  # signed edge for directional trade
    prior: np.ndarray
    counts: np.ndarray
    label_series: np.ndarray  # hard labels on fit sample


def classify_regimes(df: pd.DataFrame) -> np.ndarray:
    """Map trend/range/shock to hard Markov states via emissions features."""
    close = df["close"].values.astype(float)
    ret = df["ret"].fillna(0.0).values.astype(float)
    n = len(df)
    labels = np.full(n, STATE_IDX["RANGE"], dtype=int)

    # rolling vol
    vol = pd.Series(ret).rolling(VOL_LOOKBACK, min_periods=24).std().values
    vol_med = pd.Series(vol).rolling(24 * 14, min_periods=24).median().values
    vol_mad = (
        pd.Series(np.abs(vol - vol_med))
        .rolling(24 * 14, min_periods=24)
        .median()
        .values
    )
    vol_z = (vol - vol_med) / np.maximum(1.4826 * vol_mad, 1e-8)

    # trend return over lookback
    lag = np.roll(close, TREND_LOOKBACK)
    lag[:TREND_LOOKBACK] = np.nan
    trend_ret = (close - lag) / lag

    for i in range(n):
        if not np.isfinite(trend_ret[i]) or not np.isfinite(vol_z[i]):
            labels[i] = STATE_IDX["RANGE"]
            continue
        if vol_z[i] >= SHOCK_VOL_Z or abs(ret[i]) > 0.02:
            labels[i] = STATE_IDX["SHOCK"]
        elif trend_ret[i] >= TREND_ABS_RET:
            labels[i] = STATE_IDX["TREND_UP"]
        elif trend_ret[i] <= -TREND_ABS_RET:
            labels[i] = STATE_IDX["TREND_DOWN"]
        elif abs(trend_ret[i]) <= RANGE_ABS_RET:
            labels[i] = STATE_IDX["RANGE"]
        else:
            # weak trend zone → assign by sign
            labels[i] = (
                STATE_IDX["TREND_UP"] if trend_ret[i] > 0 else STATE_IDX["TREND_DOWN"]
            )
    return labels


def fit_markov(
    df: pd.DataFrame,
    cost_bps_roundtrip: float = 22.0,
) -> MarkovFit:
    labels = classify_regimes(df)
    k = len(STATES)
    counts = np.ones((k, k), dtype=float)  # Laplace
    for t in range(len(labels) - 1):
        counts[labels[t], labels[t + 1]] += 1.0
    transition = counts / counts.sum(axis=1, keepdims=True)

    # next-bar returns conditional on current state
    next_ret = df["ret"].shift(-1).values.astype(float)
    emission_mean = np.zeros(k)
    emission_std = np.ones(k) * 0.005
    for s in range(k):
        mask = (labels == s) & np.isfinite(next_ret)
        if mask.sum() > 30:
            emission_mean[s] = float(np.nanmean(next_ret[mask]))
            emission_std[s] = float(np.nanstd(next_ret[mask]) + 1e-8)

    cost = cost_bps_roundtrip / 10_000.0
    # Directional edge: long in UP, short in DOWN; RANGE/SHOCK flat edge 0
    edge = np.zeros(k)
    edge[STATE_IDX["TREND_UP"]] = emission_mean[STATE_IDX["TREND_UP"]] - cost
    edge[STATE_IDX["TREND_DOWN"]] = (-emission_mean[STATE_IDX["TREND_DOWN"]]) - cost
    # RANGE: mild mean-reversion edge from opposing next move after costs
    # estimate using sign-flip of short-horizon return
    edge[STATE_IDX["RANGE"]] = abs(emission_mean[STATE_IDX["RANGE"]]) - cost
    edge[STATE_IDX["SHOCK"]] = -abs(cost)  # negative: do not trade

    prior = np.bincount(labels, minlength=k).astype(float)
    prior = prior / prior.sum()

    return MarkovFit(
        states=STATES,
        transition=transition,
        emission_mean=emission_mean,
        emission_std=emission_std,
        edge_after_cost=edge,
        prior=prior,
        counts=counts,
        label_series=labels,
    )


def bayes_update(
    posterior: np.ndarray,
    transition: np.ndarray,
    emission_mean: np.ndarray,
    emission_std: np.ndarray,
    observed_ret: float,
) -> np.ndarray:
    """Predict with P, then update with Gaussian emission likelihood of observed ret."""
    predictive = posterior @ transition
    # likelihood under each state's emission
    ll = np.exp(-0.5 * ((observed_ret - emission_mean) / emission_std) ** 2)
    ll = ll / (emission_std * np.sqrt(2 * np.pi))
    unnorm = predictive * np.maximum(ll, 1e-300)
    s = unnorm.sum()
    if s <= 0 or not np.isfinite(s):
        return predictive / predictive.sum()
    return unnorm / s


def hard_state(posterior: np.ndarray) -> int:
    return int(np.argmax(posterior))


def format_transition(fit: MarkovFit) -> pd.DataFrame:
    return pd.DataFrame(fit.transition, index=list(STATES), columns=list(STATES))
