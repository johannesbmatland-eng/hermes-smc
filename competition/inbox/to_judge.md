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
