"""Unit tests for long/short SMC entry mirroring."""

from hermes_smc.engine.smc_engine import PositionManager, SMCConfig, SMCEngine
from hermes_smc.engine.paper_trading import PaperTradingEngine
from hermes_smc.engine.core import MarketStructureDetector, TrendAnalyzer


def _candle(ts, o, h, l, c, v=1.0):
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}


def test_position_manager_sl_tp_long_vs_short():
    pm = PositionManager()
    entry = 100.0
    fvg_bottom = 98.0
    fvg_top = 102.0

    sl_long = pm.calculate_sl_price(entry, fvg_bottom, side="long", fvg_top=fvg_top)
    tp_long = pm.calculate_tp_price(entry, sl_long, rr_target=2.0, side="long")
    assert sl_long < fvg_bottom
    assert tp_long > entry
    # 1:2 RR: reward == 2 * risk
    assert abs((tp_long - entry) - 2 * (entry - sl_long)) < 1e-9

    sl_short = pm.calculate_sl_price(entry, fvg_bottom, side="short", fvg_top=fvg_top)
    tp_short = pm.calculate_tp_price(entry, sl_short, rr_target=2.0, side="short")
    assert sl_short > fvg_top
    assert tp_short < entry


def test_short_pnl_and_capital():
    pm = PositionManager(initial_capital=100_000)
    pm.open_position(
        trade_id="s1",
        asset="BTC/USD",
        side="short",
        entry_price=100.0,
        position_size=10.0,
        sl_price=105.0,
        tp_price=90.0,
        strategy_info={},
    )
    # Equity model: capital unchanged while position is open
    assert pm.capital == 100_000

    closed = pm.close_position("s1", exit_price=90.0, exit_reason="take_profit")
    assert closed is not None
    assert closed["pnl"] == 100.0  # (100-90)*10
    assert abs(pm.capital - (100_000 + 100.0)) < 1e-6
    assert abs(closed["pnl_account_pct"] - 0.1) < 1e-9  # $100 / $100k
    assert abs(closed["pnl_pct"] - 10.0) < 1e-9  # price move still tracked
    assert abs(closed["r_multiple"] - 2.0) < 1e-9  # $100 / ($5 risk * 10)


def test_account_pct_matches_half_percent_risk_double_rr():
    """0.5% risk at 1:2 RR ≈ +1% account — not the smaller price-move %."""
    from hermes_smc.engine.smc_engine import PositionManager

    pm = PositionManager(initial_capital=100_000)
    entry, sl, tp = 78849.0, 78641.901, 79263.198
    risk = entry - sl
    size = (100_000 * 0.005) / risk
    pm.open_position("t", "BTC/USD", "long", entry, size, sl, tp, {})
    closed = pm.close_position("t", tp, "take_profit")
    assert closed["pnl_account_pct"] > 0.99
    assert closed["pnl_account_pct"] < 1.02
    assert closed["pnl_pct"] < closed["pnl_account_pct"]  # price % is smaller on BTC
    assert abs(closed["r_multiple"] - 2.0) < 1e-6


def test_analytics_sessions_and_conditions():
    from hermes_smc.engine.analytics import build_analytics, session_from_ts

    assert session_from_ts(1787816139.0)  # any valid label
    trades = [
        {
            "pnl": 1000,
            "entry_price": 100,
            "exit_price": 102,
            "stop_loss": 99,
            "position_size": 5,
            "side": "long",
            "open_time": 1700000000,  # will map to a session
            "exit_reason": "take_profit",
            "strategy_info": {"trend": "bullish", "confirmation": "engulfing_5m"},
        },
        {
            "pnl": -500,
            "entry_price": 100,
            "exit_price": 99,
            "stop_loss": 98,
            "position_size": 5,
            "side": "long",
            "open_time": 1700000000 + 3600 * 14,
            "exit_reason": "stop_loss",
            "strategy_info": {"trend": "bullish", "confirmation": "engulfing_5m"},
        },
    ]
    a = build_analytics(trades, initial_capital=100_000)
    assert a["trade_count"] == 2
    assert abs(a["total_account_pct"] - 0.5) < 1e-9
    assert a["by_session"]
    assert a["by_trend"]
    assert a["best_session"] is not None
    """Old model left capital negative after large BTC notionals — repair on load."""
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        (state_dir / "state.json").write_text(json.dumps({
            "capital": -90365.47,
            "initial_capital": 100000,
            "open_positions": {
                "t1": {
                    "id": "t1",
                    "asset": "BTC/USD",
                    "side": "long",
                    "entry_price": 78849.0,
                    "position_size": 2.414,
                    "entry_value": 190365.47,
                    "stop_loss": 78641.0,
                    "take_profit": 79263.0,
                    "open_time": 1.0,
                    "strategy_info": {},
                    "status": "open",
                    "pnl": 0,
                    "pnl_pct": 0,
                    "current_price": 78850.0,
                }
            },
            "closed_positions": [],
            "trade_history": [],
        }))
        pm = PositionManager(initial_capital=100000, state_dir=state_dir)
        assert abs(pm.capital - 100000) < 1e-6
        assert "t1" in pm.open_positions
        closed = pm.close_position("t1", exit_price=79263.0, exit_reason="take_profit")
        assert closed["pnl"] > 0
        assert pm.capital > 100000


def test_structure_break_disabled_by_default():
    engine = SMCEngine(SMCConfig())
    position = {
        "entry_price": 100.0,
        "stop_loss": 90.0,
        "take_profit": 120.0,
        "side": "long",
        "open_time": 0,
    }
    # Recent low is >0.5% under entry — would have been structure_break before
    candles = [_candle(i, 100, 101, 99.0, 100) for i in range(12)]
    candles[-3] = _candle(10, 100, 101, 99.4, 100)  # low < entry*0.995
    assert engine.check_exit_conditions(position, candles, 100.0) is None


def test_position_size_uses_positive_capital_only():
    pm = PositionManager(initial_capital=100_000)
    pm.capital = -50_000
    assert pm.calculate_position_size(100.0, 99.0, risk_pct=0.5) == 0
    pm.capital = 100_000
    size = pm.calculate_position_size(100.0, 99.0, risk_pct=0.5)
    assert abs(size - 500.0) < 1e-9  # risk $500 / $1


def test_detect_bullish_fvg_three_candles():
    # Last low does not overlap first high
    candles = [
        _candle(1, 100, 101, 99, 100.5),   # first high = 101
        _candle(2, 101, 106, 100.8, 105),  # impulse
        _candle(3, 105, 107, 102.5, 106),  # last low = 102.5 > 101
    ]
    fvgs = MarketStructureDetector.detect_fvg(candles)
    bullish = [f for f in fvgs if f["type"] == "bullish"]
    assert len(bullish) == 1
    assert bullish[0]["bottom"] == 101
    assert bullish[0]["top"] == 102.5


def test_detect_bearish_fvg():
    # Last high does not overlap first low
    candles = [
        _candle(1, 100, 101, 99, 99.5),    # first low = 99
        _candle(2, 99, 99.2, 94, 95),      # impulse
        _candle(3, 95, 97.5, 93, 94),      # last high = 97.5 < 99
    ]
    fvgs = MarketStructureDetector.detect_fvg(candles)
    bearish = [f for f in fvgs if f["type"] == "bearish"]
    assert len(bearish) == 1
    assert bearish[0]["top"] == 99
    assert bearish[0]["bottom"] == 97.5


def test_no_fvg_when_wicks_overlap():
    candles = [
        _candle(1, 100, 105, 99, 104),
        _candle(2, 104, 106, 103, 105),
        _candle(3, 105, 107, 104, 106),  # last low 104 overlaps first high 105
    ]
    fvgs = MarketStructureDetector.detect_fvg(candles)
    assert fvgs == []


def test_fvg_boxes_extend_until_fill():
    candles = [
        _candle(1000, 100, 101, 99, 100.5),
        _candle(1060, 101, 106, 100.8, 105),
        _candle(1120, 105, 107, 102.5, 106),  # bullish FVG 101-102.5 forms
        _candle(1180, 106, 108, 103, 107),
        _candle(1240, 107, 108, 100.5, 101),  # wick fills below 101
        _candle(1300, 101, 102, 100, 101.5),
    ]
    boxes = MarketStructureDetector.build_fvg_boxes(candles, max_age_candles=50, include_mitigated=True)
    assert any(b["type"] == "bullish" for b in boxes)
    filled = [b for b in boxes if b["type"] == "bullish" and not b["unmitigated"]]
    assert filled
    assert filled[0]["time_start"] == 1120
    assert filled[0]["time_end"] == 1240



def test_paper_engine_short_sl_tp_helpers():
    engine = PaperTradingEngine(SMCConfig())
    engine.config._config["exits"]["mode"] = "fixed_tp"
    fvg = {"top": 105.0, "bottom": 100.0, "mid": 102.5, "type": "bearish"}
    entry = 102.0

    sl = engine._calculate_smc_sl(entry, fvg, side="short")
    tp = engine._calculate_smc_tp(entry, sl, side="short")
    size = engine._calculate_position_size(entry, sl)

    assert sl > fvg["top"]
    assert tp < entry
    assert size > 0


def test_be_trail_tp_defaults_to_five_r():
    engine = PaperTradingEngine(SMCConfig())
    entry, sl = 100.0, 99.0
    tp = engine._calculate_smc_tp(entry, sl, side="long")
    assert abs(tp - 105.0) < 1e-9  # 1:5 hard TP in be_trail mode


def test_rr_two_makes_one_percent_from_half_percent_risk():
    engine = PaperTradingEngine(SMCConfig())
    engine.config._config["exits"]["mode"] = "fixed_tp"
    entry, sl = 100.0, 99.0  # 1.0 price risk
    tp = engine._calculate_smc_tp(entry, sl, side="long")
    assert abs(tp - 102.0) < 1e-9  # 1:2 RR


def test_exit_conditions_short():
    engine = SMCEngine(SMCConfig())
    engine.config._config["exits"]["mode"] = "fixed_tp"
    position = {
        "entry_price": 100.0,
        "stop_loss": 105.0,
        "take_profit": 90.0,
        "side": "short",
        "initial_stop_loss": 105.0,
    }
    candles = [_candle(i, 100, 101, 99, 100) for i in range(12)]

    assert engine.check_exit_conditions(position, candles, 106.0) == "stop_loss"
    assert engine.check_exit_conditions(position, candles, 89.0) == "take_profit"


def test_be_at_two_r_then_trail():
    engine = SMCEngine(SMCConfig())
    engine.config._config["exits"]["mode"] = "be_trail"
    engine.config._config["exits"]["be_at_rr"] = 2.0
    engine.config._config["exits"]["trail_rr"] = 1.0
    engine.config._config["exits"]["tp_rr"] = 5.0

    position = {
        "entry_price": 100.0,
        "stop_loss": 99.0,
        "take_profit": 102.0,  # old 1:2 — should widen to 105
        "side": "long",
        "initial_stop_loss": 99.0,
        "open_time": 1.0,
    }
    candles = [_candle(i, 100, 101, 99.5, 100) for i in range(12)]

    # At +2R (102): move SL to BE, do not exit yet
    assert engine.check_exit_conditions(position, candles, 102.0) is None
    assert position["be_moved"] is True
    assert position["stop_loss"] >= 100.0
    assert position["take_profit"] == 105.0  # widened to 1:5

    # At +3R (103): trail SL to peak - 1R = 102
    assert engine.check_exit_conditions(position, candles, 103.0) is None
    assert abs(position["stop_loss"] - 102.0) < 1e-9
    assert position["sl_mode"] == "trailing"

    # Pullback to trailed SL
    assert engine.check_exit_conditions(position, candles, 102.0) == "trailing_stop"


def test_hard_tp_at_five_r_still_works():
    engine = SMCEngine(SMCConfig())
    position = {
        "entry_price": 100.0,
        "stop_loss": 99.0,
        "take_profit": 105.0,
        "side": "long",
        "initial_stop_loss": 99.0,
        "open_time": 1.0,
        "peak_price": 100.0,
        "be_moved": False,
        "sl_mode": "initial",
    }
    candles = [_candle(i, 100, 101, 99.5, 100) for i in range(12)]
    assert engine.check_exit_conditions(position, candles, 105.0) == "take_profit"


def test_bearish_engulfing_confirmation():
    engine = PaperTradingEngine(SMCConfig())
    # [-3] touch bull, [-2] larger bear engulf, [-1] forming
    candles = [
        _candle(1, 100, 101, 99, 100),
        _candle(2, 100, 103, 99.5, 102.5),  # touch (bull) into FVG
        _candle(3, 102.5, 103.2, 98, 98.5),  # larger bear engulf (locked)
        _candle(4, 98.5, 99, 98, 98.2),      # forming
    ]
    fvg = {"top": 103.0, "bottom": 99.0, "type": "bearish"}
    conf = engine._check_smc_confirmation(candles, None, fvg, side="short")
    assert conf == "engulfing_5m"


def test_crypto_bullish_engulfing_when_open_equals_prev_close():
    """Bear touches FVG, larger bull locks (open may equal prev close)."""
    engine = PaperTradingEngine(SMCConfig())
    fvg = {"top": 78773.3, "bottom": 78750.0, "type": "bullish", "end_candle": 0}
    candles = [
        _candle(1, 78780, 78790, 78770, 78775),
        _candle(2, 78773.3, 78773.3, 78760.0, 78765.0),  # bear touches FVG
        _candle(3, 78765.0, 78827.1, 78764.9, 78827.1),  # larger bull locks
        _candle(4, 78827.1, 78830, 78820, 78825),        # forming
    ]
    assert engine._candle_touches_fvg(candles[1], fvg) is True
    assert engine._is_body_engulfing(candles[1], candles[2], "long") is True
    assert engine._check_smc_confirmation(candles, None, fvg, side="long") == "engulfing_5m"


def test_engulf_allowed_on_second_candle_after_touch():
    """Bear touches FVG; middle candle does not engulf; 2nd next candle does."""
    engine = PaperTradingEngine(SMCConfig())
    fvg = {"top": 100.5, "bottom": 100.0, "type": "bullish", "end_candle": 0}
    candles = [
        _candle(1, 101, 102, 100.8, 101.5),           # before
        _candle(2, 101.2, 101.3, 99.9, 100.2),        # bear touches FVG
        _candle(3, 100.2, 100.4, 100.1, 100.3),       # small bull — does NOT engulf
        _candle(4, 100.2, 101.5, 100.15, 101.4),      # larger bull engulfs the bear body
        _candle(5, 101.4, 101.6, 101.3, 101.5),       # forming
    ]
    # Immediate next does not confirm
    early = candles[:4]  # locked= small bull at idx 2 — should fail
    assert engine._check_smc_confirmation(early, None, fvg, side="long") is None
    # With 2-candle lookback, locked engulf at idx 3 confirms touch at idx 1
    assert engine._check_smc_confirmation(candles, None, fvg, side="long") == "engulfing_5m"


def test_sl_is_just_under_fvg_zone_not_candle_low():
    engine = PaperTradingEngine(SMCConfig())
    # FVG zone 100-101; first candle that formed gap had low at 95 (must NOT drive SL)
    fvg = {"top": 101.0, "bottom": 100.0, "type": "bullish"}
    sl = engine._calculate_smc_sl(102.0, fvg, side="long")
    assert sl < fvg["bottom"]
    assert sl > 99.5  # tight under FVG, nowhere near a deep candle-1 low
    assert abs(sl - 100.0 * (1 - 0.0003)) < 1e-9


def test_analysis_snapshot_has_checklist_and_phase():
    engine = PaperTradingEngine(SMCConfig())
    # Synthetic rising market with enough candles
    candles = []
    price = 100.0
    for i in range(120):
        o = price
        c = price + 0.2
        candles.append(_candle(1_700_000_000 + i * 300, o, c + 0.1, o - 0.1, c))
        price = c

    snap = engine.build_analysis_snapshot(candles, candles, candles, candles[-30:], price)
    assert snap["market"] == "BTC/USD"
    assert "phase" in snap
    assert isinstance(snap["checklist"], list)
    assert len(snap["checklist"]) >= 3
    assert "ema" in snap
    assert "fvgs" in snap


def test_rsi_blocks_long_when_overbought():
    engine = PaperTradingEngine(SMCConfig())
    # Strong up-only series → RSI near 100
    candles = [_candle(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(40)]
    rsi = engine._calc_rsi(candles, 14)
    assert rsi is not None and rsi > 65
    assert engine._rsi_allows_side("long", rsi) is False
    assert engine._rsi_allows_side("short", rsi) is True


def test_rsi_blocks_short_when_oversold():
    engine = PaperTradingEngine(SMCConfig())
    candles = [_candle(i, 140 - i, 141 - i, 139 - i, 140 - i) for i in range(40)]
    rsi = engine._calc_rsi(candles, 14)
    assert rsi is not None and rsi < 35
    assert engine._rsi_allows_side("short", rsi) is False
    assert engine._rsi_allows_side("long", rsi) is True


def test_engulf_allows_bullish_touch_candle():
    """Touch candle may be bullish; next larger bull still engulfs into long."""
    engine = PaperTradingEngine(SMCConfig())
    fvg = {"top": 100.5, "bottom": 100.0, "type": "bullish", "end_candle": 0}
    candles = [
        _candle(1, 101, 102, 100.8, 101.5),
        _candle(2, 100.1, 100.4, 99.95, 100.35),  # bullish but touches FVG
        _candle(3, 100.1, 101.2, 100.05, 101.1),  # larger bull engulfs touch body
        _candle(4, 101.1, 101.3, 101.0, 101.2),   # forming
    ]
    assert engine._candle_touches_fvg(candles[1], fvg) is True
    assert candles[1]["close"] > candles[1]["open"]  # touch is bullish
    assert engine._is_body_engulfing(candles[1], candles[2], "long") is True
    assert engine._check_smc_confirmation(candles, None, fvg, side="long") == "engulfing_5m"


def test_ema_series_length():
    engine = PaperTradingEngine(SMCConfig())
    candles = [_candle(1_700_000_000 + i * 300, 100, 101, 99, 100 + i * 0.01) for i in range(80)]
    series = engine._ema_series(candles, 50)
    assert len(series) == 80 - 50 + 1
    assert "time" in series[0] and "value" in series[0]
