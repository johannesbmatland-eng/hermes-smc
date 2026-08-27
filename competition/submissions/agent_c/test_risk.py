#!/usr/bin/env python3
"""Risk engine unit checks — run: python test_risk.py"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_c.config import PropRules
from agent_c.risk import PropRiskEngine


def test_daily_fail() -> None:
    r = PropRiskEngine(PropRules())
    r.mark_equity(100_000)
    r.day_start_equity = 100_000
    snap = r.mark_equity(96_900)
    assert snap.compliance == "fail"
    assert r.halted
    ok, why = r.can_open(10_000)
    assert not ok and "halted" in why


def test_dd_fail() -> None:
    r = PropRiskEngine(PropRules())
    r.mark_equity(100_000)
    r.peak_equity = 100_000
    snap = r.mark_equity(93_900)  # 6.1% DD
    assert snap.compliance == "fail"


def test_leverage_cap() -> None:
    r = PropRiskEngine(PropRules())
    r.mark_equity(100_000)
    ok, why = r.can_open(600_000)  # 6x
    assert not ok and "leverage" in why
    ok2, _ = r.can_open(400_000)
    assert ok2


def test_size_respects_leverage() -> None:
    r = PropRiskEngine(PropRules())
    r.mark_equity(100_000)
    size = r.size_from_risk(entry=50_000, stop=49_000, risk_pct=0.4)
    notional = size * 50_000
    assert notional <= 5 * 100_000 + 1e-6


if __name__ == "__main__":
    test_daily_fail()
    test_dd_fail()
    test_leverage_cap()
    test_size_respects_leverage()
    print("ALL RISK TESTS PASSED")
