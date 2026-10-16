# Research log

What each research run read, what it took, and what it left and why.
Newest first. A later run never takes an idea an entry here rejected
unless something new changes the reason.

## 2026-10-16, second run

**How it was read.** As in the first run: through search-engine
extracts, since arXiv and its mirrors could not be reached.

**Read:**
- *Are "Solved Issues" in SWE-bench Really Solved Correctly?* (ICSE
  2026). Each differentiating test is run 20 times under each patch, and
  kept only if it separates them every time.
  https://software-lab.org/publications/icse2026_SWE-bench-correctness.pdf
- *SWE-rebench* (arXiv 2505.20411) and *SWE-Next* (arXiv 2603.20691):
  task pipelines mined from repositories. They name flaky tests,
  intermittently unavailable external services, and environment drift
  among the reasons execution checks fail. https://arxiv.org/abs/2505.20411
- *RuBench* (arXiv 2607.06411), and reports on SWE-Bench Pro, where most
  cheating runs read the gold commit from the shipped `.git`. RuBench
  strips history from each workspace and checks that the regression tests
  fail on the parent and pass on the fix. https://arxiv.org/abs/2607.06411
- *Failure as a Process: An Anatomy of CLI Coding Agent Trajectories*
  (arXiv 2607.09510). 1,794 annotated Terminal-Bench runs: failures are
  mostly epistemic, begin in the first few steps, and stay hidden until
  recovery is no longer possible. About half of failed runs commit their
  decisive error by step 7, become unrecoverable around step 12, and
  first show a symptom around step 16. https://arxiv.org/abs/2607.09510
- Reward hacking in coding agents: *Do Coding Agents Deceive Us?* (capped
  evaluation, arXiv 2606.07379), *SpecBench* (the gap between visible
  and held-out tests, arXiv 2605.21384), *BaitBench* (arXiv 2608.30724)
  and *HackTrace* (arXiv 2610.03055).

**Taken: a grader must agree with itself.** `--from-git` checked each
held-out grader once on the parent and once on the commit, so one lucky
pass or failure there let a flaky grader in. A flaky grader then passes
or fails the agent's runs by chance, and the evolution learns from
noise.
- Built: `gittasks.build(checks=3)`, which checks each grader 3 times on
  each side on fresh copies and stops at the first run that disagrees.
  The command gets `--grader-checks N`.
- Tested with a grader that remembers its runs and fails after the
  first.
- Measured on python-slugify: the same 6 tasks at 1 check and at 5, in
  14 s and 46 s. None of its graders is flaky, which also clears the
  graders behind the recorded slugify evolutions.

**Not taken, and why:**
- **Stripping history from workspaces** (RuBench). Already so: each
  workspace is `git archive` of the parent, with no `.git`.
- **Watching for the decisive error early** (Failure as a Process).
  AgentDiff already names the decisive step after the fact. Acting on it
  during a run would mean a model or a learned monitor deciding, mid-run,
  that a run has failed. Left for a guard rule built on counts, if one
  can be found that fires early without a judge.
- **Capped evaluation** (CapCode). It needs tests the agent can see as
  well as held-out ones. A `--from-git` task holds all of its grader out,
  so there is no visible score to cap. Revisit if visible tests are ever
  given to the agent.
- **Behavioural hack detection** (HackTrace). It reads model internal
  states, which Claude Code and Codex CLI do not expose.

## 2026-10-16

**How it was read.** arXiv and the paper mirrors could not be reached
from the run's network. The papers below were read through search-engine
extracts of their abstracts and pages, not their full text. Numbers
quoted are theirs as the extracts gave them.

**Read:**
- *How Many Tasks Are Enough for Agent Benchmark Decisions? A Replay
  Analysis of Public LLM Agent Benchmarks* (W.-J. Huang, arXiv
  2607.12338). It replays completed task-level records of SWE-bench,
  AppWorld and tau-bench to ask when a partial run already supports the
  full run's pairwise decision: AppWorld at 15% of tasks, tau-bench at
  25%, SWE-bench Verified at 90%. A raw task fraction is not a decision
  rule. https://arxiv.org/abs/2607.12338
- *ParEvalLayer: When Partial LLM-Agent Evaluations Support a Decision*
  (arXiv 2608.02444). It reads paired task outcomes under a policy fixed
  in advance (improvement wanted, task order, decision-error target,
  allowed unresolved rate), and warns that a rule deciding only the easy
  pairs looks accurate while leaving many unresolved.
  https://arxiv.org/html/2608.02444
- *Self-Harness: Harnesses That Improve Themselves* (arXiv 2606.09498).
  A fixed model mines weaknesses from its own traces, proposes bounded
  edits, and accepts only edits that do not regress held-in or held-out
  splits: 40.5% → 61.9% on a 64-task Terminal-Bench 2.0 subset for one
  base model. https://arxiv.org/abs/2606.09498
- *Agentic Context Engineering* (ICLR 2026, arXiv 2510.04618), and the
  2026 follow-up on *scoped verification* of evolving contexts (arXiv
  2607.09175). Itemised delta edits, merged deterministically, instead
  of whole-prompt rewrites. https://arxiv.org/abs/2510.04618
- *Meta-Harness* (arXiv 2603.28052), *Agentic Harness Engineering*
  (arXiv 2604.25850) and *Evo²* (arXiv 2610.13025): harness search with
  an agentic proposer, a Pareto frontier of harnesses, and weight-harness
  co-evolution.
- Failure attribution: *Soap* (arXiv 2610.13105), *LongRCA Bench* (arXiv
  2608.15242), *Failure as a Process* (arXiv 2607.09510), *Conformal
  Agent Error Attribution* (arXiv 2605.06788).
- Cheaper evaluation: *EarlyEval* (arXiv 2609.02783) halts single runs
  on a learned prediction; *AV-AIVAT* (arXiv 2608.06362) stops paired
  comparisons with anytime-valid confidence sequences.

**Taken: stop a changed arm once the runs left cannot make it kept.**
The replay studies show partial runs often reach the full run's
decision, and estimate when, with an error target. AgentDiff's
keep-or-revert decision is a fixed rule over pass counts, so the same
question has an exact answer: bound each task's final count and ask the
rule. The verdict can't change, and the runs a lost cause would still
have made are skipped.
- Built: `selfevolve.could_keep`, `self_evolve(settle=True)`,
  `rounds=` on `vendor_arm`, and `--no-settle`.
- A test checks it against `decide()` on every completion of 400 random
  cases.
- Measured:
  - **Synthetic:** it skips 17–35% of a harmful change's runs, about 5%
    of a do-nothing change's, and 2–3% of a helpful one's. No verdict
    changed in 2,700 draws.
  - **Recorded:** 0 of the 8 real changes on disk would have stopped
    early. They were neutral or helpful.
- Cost: rounds wait for their slowest run, so a changed arm can take a
  little longer in wall-clock time.

**Not taken, and why:**
- **Statistical early stopping** (anytime-valid tests, EarlyEval's
  learned halting). It accepts an error rate, or lets a model's
  prediction decide when a run counts. Here no model may decide a
  number, and the exact rule above loses nothing.
- **Pareto frontier of harnesses** (Meta-Harness). It needs many
  candidate harnesses alive at once and many more real runs than a
  2-dollar run allows. A candidate for when the task sets are larger.
- **ACE-style itemised context edits.** The harness's instructions are
  already itemised and added one at a time, each kept only by a paired
  test. Nothing new to build.
- **Accept only on no regression across held-in and held-out splits**
  (Self-Harness). Already the rule in substance: "breaks no task" on the
  evolving tasks, and `--holdout` for unseen ones.
- **Spectral or conformal failure attribution** (Soap, conformal
  attribution). Both need step embeddings or a calibration set of
  labelled decisive steps that AgentDiff's traces do not carry.
  Revisit if a labelled set (LongRCA Bench) can be read here.
