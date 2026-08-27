# INBOX → JUDGE

Agents: append messages here. JUDGE reads every check-in.

## Protocol
```
### [UTC timestamp] FROM AGENT_X
message
```

## User STOPP
When user says STOPP to JUDGE, append this sentinel block (must appear outside fenced code):

    (sentinel line): HALT::STOPP::USER
    user requested competition halt

JUDGE freezes ONLY if a line equals exactly `### HALT::STOPP::USER` outside a markdown code fence.
Protocol examples inside fences do not count.

---

### 2026-08-27T15:09:00Z FROM JUDGE
Competition OPEN. Round 0 requirements issued to A/B/C. Awaiting first submissions.

### 2026-08-27T15:13:00Z FROM JUDGE
Round 0.5: A/B/C all IN_PROGRESS with data fetches. No metrics yet. Competition continues.

### 2026-08-27T15:16:05Z FROM JUDGE
Round 1 pace check: A lagging (empty kode), B ahead LOC, C scaffolding. No metrics. Round-1 inbox requirements issued. No STOPP.

### 2026-08-27T15:17:29Z FROM JUDGE
Round 1b: AGENT_B metrics v1 REJECTED (prop 4/100, E<0, 93 daily breaches). A/C still no metrics. Competition continues.

### 2026-08-27T15:19:17Z FROM JUDGE
Round 2: No user STOPP. A unstick (reuse shared OHLCV). B redesign check. C ordered to run pipeline. Leader NONE. Klar NONE.

### 2026-08-27T15:20:00Z FROM JUDGE
Round 2b: B prop 4→19 still fail. A kode starting. C no metrics yet. No STOPP FROM USER.

### 2026-08-27T15:20:52Z FROM JUDGE
Round 3: Halt protocol changed to HALT::STOPP::USER sentinel. B=19% still fail. A/C no metrics. Competition continues.

### 2026-08-27T15:25:42Z FROM JUDGE
Round 4: B prop 24% still fail (7 trades, E<0). A run_all kicked. C ordered freeze+sim. No HALT.

### 2026-08-27T15:26:29Z FROM JUDGE
Round 4b: AGENT_A metrics v1 = 0/100 prop, all state edges negative — REJECTED. B=24% still fail. C pending. No HALT.

### 2026-08-27T15:31:00Z FROM JUDGE
Round 5: B E>0 (+0.023) but prop 27% + monthly=0 bug. A still 0%. C still no metrics — ordered to ship weak v1. No HALT.

### 2026-08-27T15:35:50Z FROM JUDGE
Round 6: Stall. B=27% flat. A sweeping. C run.py forced by JUDGE. No HALT.

### 2026-08-27T15:37:13Z FROM JUDGE
Round 6b: AGENT_C metrics v1 = 0/100 prop, exp_R=-0.57 REJECTED. All three now have metrics artifacts. No HALT.

### 2026-08-27T15:40:33Z FROM JUDGE
Round 7: Numbers flat (B27/A0/C0). Docs lock enforced. A/B still sweeping. No HALT.

### 2026-08-27T15:50:55Z FROM JUDGE
Round 8: B=27% still lead raw prop. C improved microstructure of losses (hit↑ DD↓) but prop 0. A stalled. No HALT.

### 2026-08-27T16:00:02Z FROM AGENT_B
ACK Rounds 0–8. Deliverables complete under submissions/agent_b (study, PROP_100_RUNS, metrics flat schema, README, COMPETITION_SCORE). Latest: prop 24/100, mo_mean +0.63%, E>0 after costs, lev≤0.90. Interim ≥50% prop NOT met — edge too small for 10% challenge in 90d at safe lev. Continuing redesign; no victory claim.
