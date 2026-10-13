# haiku vs sonnet — same task, side by side

6 tasks, 6 paired runs, on equal terms throughout the parity ledger. haiku passed 5 of 6; sonnet passed 5 of 6. 1 of 6 pairs spent within ±10% of each other's tokens; where they did not, compare outcomes per token rather than per run. Passing runs per million tokens: haiku 8.402, sonnet 6.268. haiku explored 4 time(s) before its first edit (median) and ran a check after its last edit in 5 of 5 run(s); sonnet explored 4 time(s) before its first edit (median) and ran a check after its last edit in 5 of 5 run(s). Failed the check and said what stopped it: haiku 1 time(s), sonnet 1 time(s).

## Parity: what was equal

| condition | equal | haiku | sonnet |
|---|---|---|---|
| the prompt | yes | 6 tasks, the same for both on each | 6 tasks, the same for both on each |
| the starting workspace | yes | 6 tasks, the same for both on each | 6 tasks, the same for both on each |
| the check that grades it | yes | 6 tasks, the same for both on each | 6 tasks, the same for both on each |
| the token budget | yes | 800000 | 800000 |
| the time limit | yes | 900 | 900 |
| how the budget is enforced | yes | while running (usage is per message) | while running (usage is per message) |
| the sandbox | yes | claude acceptEdits (Bash allowed), no OS sandbox | claude acceptEdits (Bash allowed), no OS sandbox |
| the operator's own vendor settings | yes | ignored | ignored |
| the tools on offer | yes | Bash, Read, Edit, Write, Grep, Glob, TodoWrite, Task; no MCP servers | Bash, Read, Edit, Write, Grep, Glob, TodoWrite, Task; no MCP servers |
| launched side by side | yes | 0.0s |  |
| cost reporting | yes | reported by the CLI | reported by the CLI |
| the models | — | claude-haiku-4-5-20251001 | claude-sonnet-5 |

## Outcome and spend

| | haiku | sonnet |
|---|---|---|
| passed the check | 5 of 6 (44%–97%) | 5 of 6 (44%–97%) |
| median tokens (in / cached / out) | 101,526 (98,794 / 83,900 / 1,788) | 116,240 (114,434 / 104,127 / 914) |
| cost | $0.2475 | $0.4558 |
| median wall time | 20.5s | 15.2s |
| passing runs per million tokens | 8.402 | 6.268 |
| median actions | 6 | 6 |
| explore / edit / verify / run | 58% / 17% / 25% / 0% | 68% / 14% / 19% / 0% |
| explored before first edit (median) | 4 | 4 |
| checked after its last edit | 5 of 5 | 5 of 5 |
| errors never come back to | 1 | 1 |
| median files / lines changed | 1 / 3 | 1 / 3 |
| runs that touched tests | 0 | 0 |
| said it was done, failed the check | 0 | 0 |
| failed the check and said why | 1 | 1 |

## Pairs (budget band ±10%)

| task | run | passed | tokens | matched | files both changed (identical) |
|---|---|---|---|---|---|
| bugfix-pricing | r1 | haiku ✓, sonnet ✓ | haiku 108,451, sonnet 112,305 | yes (3%) | 1 (1) |
| contradictory-rounding | r1 | haiku ✗, sonnet ✗ | haiku 69,778, sonnet 89,965 | no (22%) | 0 (0) |
| feature-slugify | r1 | haiku ✓, sonnet ✓ | haiku 94,602, sonnet 119,296 | no (21%) | 1 (0) |
| guarded-inventory | r1 | haiku ✓, sonnet ✓ | haiku 125,888, sonnet 113,184 | no (10%) | 1 (1) |
| perf-dedupe | r1 | haiku ✓, sonnet ✓ | haiku 87,839, sonnet 134,577 | no (35%) | 1 (1) |
| two-file-duration | r1 | haiku ✓, sonnet ✓ | haiku 108,504, sonnet 228,401 | no (52%) | 1 (1) |

Qualitative, not a benchmark: a few tasks and a few runs describe how these two agents worked on this work, with the intervals saying how little a pass rate on a small sample settles. Every number is read from the vendors' own streams and the harness's check.
