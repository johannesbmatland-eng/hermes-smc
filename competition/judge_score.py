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


def _normalize_metrics(raw: dict) -> dict:
    """Accept flat judge schema OR nested agent schemas (e.g. prop_100.pass_rate)."""
    out = dict(raw)
    prop = raw.get("prop_100") if isinstance(raw.get("prop_100"), dict) else {}
    full = raw.get("full_sample") if isinstance(raw.get("full_sample"), dict) else {}
    wf = raw.get("walk_forward") if isinstance(raw.get("walk_forward"), dict) else {}
    risk = raw.get("risk_rules") if isinstance(raw.get("risk_rules"), dict) else {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}

    if out.get("prop_pass_rate") is None:
        out["prop_pass_rate"] = prop.get("pass_rate")
        if out["prop_pass_rate"] is None and prop.get("passes") is not None and prop.get("n"):
            out["prop_pass_rate"] = float(prop["passes"]) / float(prop["n"])
    if out.get("prop_passes") is None:
        out["prop_passes"] = prop.get("passes")
    if out.get("prop_fails") is None and prop.get("n") is not None and prop.get("passes") is not None:
        out["prop_fails"] = int(prop["n"]) - int(prop["passes"])

    if out.get("monthly_profit_mean") is None:
        out["monthly_profit_mean"] = full.get("monthly_profit_mean")
    if out.get("monthly_profit_median") is None:
        out["monthly_profit_median"] = full.get("monthly_profit_median")

    if out.get("sharpe") is None:
        out["sharpe"] = full.get("sharpe")
    if out.get("sortino") is None:
        out["sortino"] = full.get("sortino")
    if out.get("expectancy") is None:
        out["expectancy"] = full.get("expectancy")
    if out.get("hitrate") is None:
        out["hitrate"] = full.get("hitrate")
    if out.get("payoff_ratio") is None:
        out["payoff_ratio"] = full.get("payoff") or full.get("payoff_ratio")
    if out.get("max_dd_observed") is None:
        out["max_dd_observed"] = full.get("max_dd") or full.get("max_dd_observed")
    if out.get("max_daily_loss_observed") is None:
        out["max_daily_loss_observed"] = full.get("max_daily_loss") or full.get("max_daily_loss_observed")
    if out.get("max_leverage_used") is None:
        out["max_leverage_used"] = (
            raw.get("max_leverage_used")
            or risk.get("max_leverage_used")
            or risk.get("max_leverage")
        )

    if out.get("fees_bps") is None:
        out["fees_bps"] = (
            raw.get("fees_bps")
            or data.get("fee_bps_per_side")
            or data.get("fees_bps")
            or (raw.get("costs") or {}).get("taker_fee_bps")
            or (raw.get("costs") or {}).get("fee_bps")
        )
    if out.get("slippage_bps") is None:
        out["slippage_bps"] = (
            raw.get("slippage_bps")
            or data.get("slip_bps_per_side")
            or data.get("slippage_bps")
            or (raw.get("costs") or {}).get("slippage_bps")
        )

    breaches = out.get("risk_breaches") if isinstance(out.get("risk_breaches"), dict) else {}
    if not breaches:
        breaches = {
            "daily_3pct": int(
                raw.get("daily_breach_count")
                or prop.get("daily_breach_total")
                or full.get("daily_breach")
                or wf.get("daily_breach_total")
                or 0
            ),
            "dd_6pct": int(
                raw.get("dd_breach_count")
                or prop.get("hwm_breach_total")
                or full.get("hwm_breach")
                or wf.get("hwm_breach_total")
                or 0
            ),
            "leverage_5x": int(
                raw.get("leverage_breach_count")
                or prop.get("lev_breach_total")
                or full.get("lev_breach")
                or 0
            ),
        }
    # Full-sample max DD over 6% is an automatic DD breach signal
    full_dd = _safe_float(full.get("max_dd") or out.get("max_dd_observed"))
    if full_dd is not None and full_dd > 0.06 + 1e-12:
        breaches["dd_6pct"] = max(int(breaches.get("dd_6pct") or 0), 1)
    out["risk_breaches"] = breaches

    if out.get("expectancy") is None:
        out["expectancy"] = full.get("expectancy") or full.get("expectancy_R") or full.get("expectancy_usd")
    if out.get("hitrate") is None:
        out["hitrate"] = full.get("hitrate") or full.get("hit_rate")
    if out.get("max_dd_observed") is None:
        out["max_dd_observed"] = full.get("max_dd") or full.get("max_dd_observed")
    if out.get("prop_passes") is None:
        out["prop_passes"] = (
            prop.get("passes")
            or raw.get("prop_passes")
            or raw.get("prop_pass_count")
        )

    if out.get("walk_forward_pass") is None:
        if "walk_forward_pass" in raw:
            out["walk_forward_pass"] = bool(raw.get("walk_forward_pass"))
        elif wf:
            # stable_risk true AND mean fold pnl not collapsing hard
            stable = bool(wf.get("stable_risk"))
            mean_pnl = _safe_float(wf.get("mean_pnl_pct"), -1.0) or -1.0
            out["walk_forward_pass"] = stable and mean_pnl > -0.02
        else:
            out["walk_forward_pass"] = False

    # Explicit risk_ok false from agent overrides breach zeros on full_sample only
    if risk.get("risk_ok") is False:
        # ensure risk component fails even if agent zeroed full_sample breaches
        if breaches.get("daily_3pct", 0) == 0 and breaches.get("dd_6pct", 0) == 0:
            breaches["daily_3pct"] = max(1, int(prop.get("daily_breach_total") or 1))
            out["risk_breaches"] = breaches

    return out


def load_agent(letter: str) -> AgentScore | None:
    agent = f"agent_{letter}"
    d = SUBS / agent
    metrics_path = d / "reports" / "metrics.json"
    if not metrics_path.exists():
        return None
    raw_in = json.loads(metrics_path.read_text(encoding="utf-8"))
    raw = _normalize_metrics(raw_in)
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
    # Inactive / never-trades / zero-prop losers must not farm full risk points
    inactive = (
        (rate is not None and rate == 0.0)
        and (mean is not None and mean <= 0.0)
        and (
            max_lev == 0.0
            or int(raw.get("prop_passes") or 0) == 0
        )
    )
    if inactive and risk_ok:
        risk_s = 25.0
        risk_ok = False  # not competitively risk-validated
        notes_inactive = True
    else:
        notes_inactive = False
    # Negative expectancy with 0% prop: further risk haircut
    exp = _safe_float(raw.get("expectancy"), 0.0) or 0.0
    if rate is not None and rate == 0.0 and exp < 0 and risk_s > 25.0:
        risk_s = 25.0
        risk_ok = False
        notes_inactive = True

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
    if notes_inactive:
        notes.append("inactive/no-trades — risk points capped")
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
    # Leader requires real competitive score floor (not just code stubs / failing economics)
    leader = "NONE"
    leader_why = "no agent above score floor with viable metrics"
    if ranked and ranked[0].total >= 40.0 and ranked[0].prop_pass_rate is not None:
        leader = ranked[0].agent
        leader_why = (
            f"highest score {ranked[0].total:.1f}; prop={ranked[0].prop_pass_rate:.1%}"
        )
    elif ranked:
        leader_why = (
            f"top raw score AGENT_{ranked[0].agent}={ranked[0].total:.1f} below leader floor "
            f"(need ≥40 with prop metrics)"
        )
    klar_list = [s.agent for s in ranked if s.klar]
    lines = [
        "# SCOREBOARD — BTCUSD PROP-BOT COMPETITION",
        "",
        f"**Updated:** {now} (UTC)",
        f"**Leader:** AGENT_{leader}" if leader != "NONE" else "**Leader:** NONE",
        f"**Leader why:** {leader_why}",
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
