# When the code first became correct

```bash
agentdiff lag runs/          # each run rebuilt edit by edit, graded after every edit
agentdiff lag runs/ --json   # every run, every grade
```

A trace records what an agent did. It does not record whether the code
was correct between its edits, so it cannot show when a run had the fix.
`agentdiff lag` rebuilds that:
- It starts from the task's own workspace.
- It applies each recorded edit in order, as the tool did. `Write` writes
  the file; `Edit` and `MultiEdit` replace exact text.
- After each edit, it runs the task's held-out check on a copy of the
  workspace.

For each run it gives:
- **first correct:** the step after which the check first passed;
- **lag:** the steps and tokens after that step, and their share of the
  run;
- **what those steps were:** checks, edits, reads, other tool calls, and
  text;
- **undone:** whether the run had a passing fix and ended failing.

## Only rebuilds that end where the run ended

The harness's record of each run keeps the SHA-256 of every file the
agent changed, as it stood at the end. A rebuild whose final files do
not hash to those values is set aside, and the file that differed is
named. A file changed from the shell (`sed -i`, a heredoc) is not
replayed, so a run that edited that way is set aside by the same test.
The final grade must also match the run's own.

Every task's untouched workspace fails its check, so a pass after an edit
is that edit's doing. Nothing runs an agent, and nothing is judged by a
model.

## On the real runs recorded here

There are 70 runs with records under `demo/vendors` and
`demo/selfevolve/examples`. Left out are 12 runs by the scripted
stand-in, and 24 traces that have no record.

| | |
|---|---|
| rebuilt byte for byte | 68 of 70 (2 set aside: `globmatch.py` was changed from the shell) |
| graded edit by edit | 66 (2 rebuilt runs made no edit to the project) |
| passing runs correct after their first edit | **54 of 62** |
| after the code first passed, median | **3 steps, 50% of the run, 47% of its tokens** |
| the 283 steps after the fix | 50 checks, 26 edits, 58 other tool calls, 149 text |
| passing runs with no check after the code was correct | 28 of 62 |
| runs that had a passing fix and ended failing | 0 of 66 |
| failing runs that were ever correct | 0 of 4 |

These runs are short, a median of 9 steps, so a 3-step lag is half the
run. The 26 edits after the fix are mostly agents' own scratch tests;
only 4 touched the project, and none of those broke it. None of the 4
failing runs ever had a passing state: they failed by never reaching the
fix, not by losing it.

What the lag buys is checking. 34 of the 62 passing runs ran a check
after their code was right, and it showed them so. The other 28 never
checked after that point, so nothing in their run showed them they were
right. That is the
pattern in `FINDINGS.md` §1.
