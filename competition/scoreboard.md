# SCOREBOARD — Trading Bot Competition
updated: 2026-08-27T14:55:55Z
judge: JUDGE
status: OPEN — CHECK-IN #4 — PROVISIONAL (transcript) / ZERO GIT-SCORED

## Prop rules
$100k · +10% · daily≤3% · DD≤6% · lev≤5x · Kraken · no live/Hermes

## Scoring weights
compliance 30% · profit/mnd 25% · winrate/expectancy 20% · robustness 15% · code/runnable 10%

## Live standings
| Rank | Agent | Phase | compliance | profit/mnd | winrate | maxDD | worstDay | trades | Score* | Notes |
|------|-------|-------|------------|------------|---------|-------|----------|--------|--------|-------|
| 1* | C | near_submit* | ok | +$1712 | 66.7% | 1.6% | -0.53% | 6 | ~72 prov | ARB BTC/ETH; fees+slip ok; **PUSH** |
| 2* | A | tuning* | ok | -1.41%/m | 57.7% | 4.22% | -1.19% | 26 | ~38 prov | 90d loss after fees; retuning |
| 3* | B | risk* | RISK | -$312/m | 26.6% | **6.6%** | — | 64 | ~15 prov | DD breach seen in run; must hard-stop |

\*Score provisional from transcripts. **Official score = 0 until `competition/` git push.**

## Leader (provisional)
**AGENT_C** — only positive profit/mnd + highest WR + clean DD. Sample thin (6 trades / ~0.9m).

## Fail flags
- **B: FAIL RISK** — transcript showed maxDD 6.60–6.61% ≥ 6% (rule_compliance fail). Hard engine must halt before breach; resubmit with ok.
- A: negative expectancy after fees — not DQ but losing race.
- ALL: no git-visible submission yet → cannot finalize.

## Official scored
A=0.00 B=0.00 C=0.00 (no push)

## Demand
C: copy to /workspace/competition + push + phase=submitted NOW.
A: freeze a compliant run, push interim.
B: fix DD hard-stop, prove maxDD≤6%, push.
