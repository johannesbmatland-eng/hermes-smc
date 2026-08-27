"""Agent A — Markov Regime Prop Bot configuration (Kraken-design BTCUSD)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
KODE_DIR = ROOT / "kode"

# Prop account
ACCOUNT_EQUITY = 100_000.0
PASS_PROFIT = 10_000.0  # +10%
DAILY_LOSS_LIMIT = 3_000.0  # -3%
MAX_DD_FROM_PEAK = 0.06  # -6% from HWM
MAX_LEVERAGE = 5.0

# Costs (Kraken-like retail maker/taker blend + slippage)
FEES_BPS = 8.0  # 0.08% round-trip half ≈ taker-ish; charged per side
SLIPPAGE_BPS = 3.0  # per side
COST_BPS_PER_SIDE = FEES_BPS + SLIPPAGE_BPS  # 11 bps/side
ROUND_TRIP_COST_BPS = 2 * COST_BPS_PER_SIDE  # 22 bps

# Markov states
STATES = ("TREND_UP", "TREND_DOWN", "RANGE", "SHOCK")
STATE_IDX = {s: i for i, s in enumerate(STATES)}

# Feature / regime thresholds (hourly)
TREND_LOOKBACK = 24  # hours
VOL_LOOKBACK = 48
SHOCK_VOL_Z = 2.25
TREND_ABS_RET = 0.012  # 24h return magnitude for trend
RANGE_ABS_RET = 0.006

# Strategy
MIN_POSTERIOR = 0.55
EDGE_FLOOR_AFTER_COST = 0.00015  # min E[r|s] after costs to allow trade
BASE_RISK_FRAC = 0.0045  # fraction of equity risked per trade (tight for prop)
ATR_STOP_MULT = 1.35
ATR_TP_MULT = 2.10
MAX_HOLD_BARS = 36
DAILY_SOFT_STOP_FRAC = 0.018  # stop new trades at -1.8% day
DD_SOFT_STOP_FRAC = 0.040  # reduce/flatten near 4% DD
TARGET_MONTHLY = 0.12

DATA_PATH = DATA_DIR / "btcusd_hourly_coinbase.csv"
DATA_SOURCE_NOTE = (
    "Coinbase Exchange public BTC-USD hourly candles 2020-01-01 → 2026-08-27 "
    "(proxy for Kraken BTCUSD; documented; fees modeled as Kraken-design)."
)


@dataclass
class SimConfig:
    account_equity: float = ACCOUNT_EQUITY
    pass_profit: float = PASS_PROFIT
    daily_loss_limit: float = DAILY_LOSS_LIMIT
    max_dd_from_peak: float = MAX_DD_FROM_PEAK
    max_leverage: float = MAX_LEVERAGE
    fees_bps: float = FEES_BPS
    slippage_bps: float = SLIPPAGE_BPS
    seed: int = 42
    prop_window_hours: int = 24 * 35  # ~35 calendar days to reach +10%
    n_prop_runs: int = 100
