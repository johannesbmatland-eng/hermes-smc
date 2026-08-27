#!/usr/bin/env python3
"""JUDGE scoring harness for BTCUSD prop-bot competition.

Reads metrics.json + checklist files under /competition/submissions/agent_*.
Writes/updates scoreboard numbers. Does NOT declare final winner.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/competition")
SUBS = ROOT / "submissions"
SCOREBOARD = ROOT / "scoreboard.md"
JUDGE_STATUS = ROOT / "status" / "judge.md"

WEIGHTS = {
    "prop": 0.30,
    "profit": 0.25,
    "risk": 0.20,
    "research": 0.15,
    "code": 0.10,
}

RESEARCH_SECTIONS = [
    "time-of-day",
    "day-of-week",
    "regime",
    "trigger",  # large moves
    "expectancy",
    "walk-forward",
]


@dataclass
class AgentScore:
    agent: str
    strategy: str
    prop_pass_rate: float | None
    monthly_mean: float | None
    monthly_median: float | None
    risk_ok: bool
    research_pct: float
    code_pct: float
    prop_score: float
    profit_score: float
    risk_score: float
    total: float
    klar: bool
    notes: str
    raw: dict


def _safe_float(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def profit_fit(mean: float | None) -> float:
    if mean is None or math.isnan(mean):
        return 0.0
    # Ideal band 10–15%
    if 0.10 <= mean <= 0.15:
        return 100.0
    if mean <= 0 or mean > 0.25:
        return 0.0
    if mean < 0.10:
        # taper 0→10% maps 0→100 linearly from 0 to 10, but penalize under-target
        return max(0.0, (mean / 0.10) * 70.0)
    # 15–25%: overfit suspicion
    return max(0.0, 100.0 - ((mean - 0.15) / 0.10) * 100.0)


def prop_component(rate: float | None) -> float:
    if rate is None:
        return 0.0
    # full marks at >= 0.90
    return min(rate / 0.90, 1.0) * 100.0


def research_score(path: Path) -> float:
    if not path.exists():
        return 0.0
    text = path.read_text(encoding="utf-8", errors="ignore").lower()
    hits = sum(1 for s in RESEARCH_SECTIONS if s in text)
    # also require sharpe/sortino/maxdd signals
    extras = ["sharpe", "sortino", "maxdd", "max dd", "max_dd", "hitrate", "payoff"]
    extra_hits = sum(1 for s in extras if s in text)
    base = hits / len(RESEARCH_SECTIONS)
    bonus = min(extra_hits, 3) / 3 * 0.15
    return min(1.0, base + bonus) * 100.0


def code_score(agent_dir: Path) -> float:
    kode = agent_dir / "kode"
    readme = agent_dir / "README.md"
    score = 0.0
    if kode.exists() and any(kode.rglob("*.py")):
        score += 50.0
    if readme.exists() and len(readme.read_text(encoding="utf-8", errors="ignore")) > 80:
        score += 25.0
    # hard risk mentions
    blob = ""
    for p in list(kode.rglob("*.py"))[:40]:
        try:
            blob += p.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            pass
    if any(k in blob for k in ("daily", "drawdown", "max_dd", "leverage", "3%", "0.03")):
        score += 25.0
    return score


def load_agent(letter: str) -> AgentScore | None:
    agent = f"agent_{letter}"
    d = SUBS / agent
    metrics_path = d / "reports" / "metrics.json"
    if not metrics_path.exists():
        return None
    raw = json.loads(metrics_path.read_text(encoding="utf-8"))
    rate = _safe_float(raw.get("prop_pass_rate"))
    # allow 90 meaning 90% or 0.90
    if rate is not None and rate > 1.5:
        rate = rate / 100.0
    mean = _safe_float(raw.get("monthly_profit_mean"))
    if mean is not None and abs(mean) > 1.5:
        mean = mean / 100.0
    median = _safe_float(raw.get("monthly_profit_median"))
    if median is not None and abs(median) > 1.5:
        median = median / 100.0

    breaches = raw.get("risk_breaches") or {}
    d3 = int(breaches.get("daily_3pct") or 0)
    dd6 = int(breaches.get("dd_6pct") or 0)
    lev = int(breaches.get("leverage_5x") or 0)
    max_lev = _safe_float(raw.get("max_leverage_used"), 0.0) or 0.0
    risk_ok = d3 == 0 and dd6 == 0 and lev == 0 and max_lev <= 5.0 + 1e-9
    risk_s = 100.0 if risk_ok else 0.0

    research = research_score(d / "research" / "BTCUSD_MARKET_STUDY.md")
    code = code_score(d)
    p = prop_component(rate)
    pf = profit_fit(mean)
    total = (
        WEIGHTS["prop"] * p
        + WEIGHTS["profit"] * pf
        + WEIGHTS["risk"] * risk_s
        + WEIGHTS["research"] * research
        + WEIGHTS["code"] * code
    )
    klar = bool(
        rate is not None
        and rate >= 0.90
        and mean is not None
        and 0.10 <= mean <= 0.15
        and risk_ok
        and raw.get("fees_bps") is not None
        and raw.get("slippage_bps") is not None
        and bool(raw.get("walk_forward_pass"))
        and research >= 80
        and code >= 75
    )
    notes = []
    if rate is None:
        notes.append("missing prop_pass_rate")
    elif rate < 0.90:
        notes.append(f"prop {rate:.1%} < 90%")
    if mean is None:
        notes.append("missing monthly mean")
    elif not (0.10 <= mean <= 0.15):
        notes.append(f"monthly mean {mean:.1%} outside 10–15%")
    if not risk_ok:
        notes.append(f"risk breaches d3={d3} dd6={dd6} lev={lev} max_lev={max_lev}")
    if not raw.get("walk_forward_pass"):
        notes.append("walk_forward_pass false/missing")
    return AgentScore(
        agent=letter.upper(),
        strategy=str(raw.get("strategy") or "?"),
        prop_pass_rate=rate,
        monthly_mean=mean,
        monthly_median=median,
        risk_ok=risk_ok,
        research_pct=research,
        code_pct=code,
        prop_score=p,
        profit_score=pf,
        risk_score=risk_s,
        total=total,
        klar=klar,
        notes="; ".join(notes) or "ok",
        raw=raw,
    )


def fmt_pct(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x*100:.1f}%"


def render(scores: list[AgentScore]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ranked = sorted(scores, key=lambda s: s.total, reverse=True)
    leader = ranked[0].agent if ranked else "NONE"
    klar_list = [s.agent for s in ranked if s.klar]
    lines = [
        "# SCOREBOARD — BTCUSD PROP-BOT COMPETITION",
        "",
        f"**Updated:** {now} (UTC)",
        f"**Leader:** AGENT_{leader}" if leader != "NONE" else "**Leader:** NONE",
        f"**Klar-kandidat:** {', '.join('AGENT_'+k for k in klar_list) if klar_list else 'NONE'}",
        "**Final winner:** NOT DECLARED (user has not said STOPP)",
        "",
        "## Live standings",
        "",
        "| Rank | Agent | Strategy | Prop pass | Mo mean | Mo med | Risk OK | Research | Code | Score | Klar | Notes |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    if not ranked:
        lines.append("| — | A/B/C | — | — | — | — | — | — | — | 0.0 | no | awaiting |")
    for i, s in enumerate(ranked, 1):
        lines.append(
            f"| {i} | AGENT_{s.agent} | {s.strategy} | {fmt_pct(s.prop_pass_rate)} | "
            f"{fmt_pct(s.monthly_mean)} | {fmt_pct(s.monthly_median)} | "
            f"{'YES' if s.risk_ok else 'NO'} | {s.research_pct:.0f} | {s.code_pct:.0f} | "
            f"{s.total:.1f} | {'YES' if s.klar else 'NO'} | {s.notes} |"
        )
    # include missing agents
    present = {s.agent for s in ranked}
    for letter in ("A", "B", "C"):
        if letter not in present:
            lines.append(
                f"| — | AGENT_{letter} | — | — | — | — | — | — | — | 0.0 | NO | AWAITING_SUBMISSION |"
            )
    lines += [
        "",
        "## Scoring weights",
        "- Prop pass-rate 30%",
        "- Monthly profit 10–15% fit 25%",
        "- Risk compliance 20%",
        "- Research/math 15%",
        "- Code/runnable 10%",
        "",
        "## Judge rules",
        "- NO premature winner. Final only on user STOPP.",
        "- Klar-kandidat requires ALL of A–F success criteria.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    scores = []
    for letter in ("a", "b", "c"):
        s = load_agent(letter)
        if s:
            scores.append(s)
    text = render(scores)
    SCOREBOARD.write_text(text, encoding="utf-8")
    print(text)
    print("\nWrote", SCOREBOARD)


if __name__ == "__main__":
    main()
