# Research log

What each research run read, what it took, and what it left and why.
Newest first. A later run never takes an idea an entry here rejected
unless something new changes the reason.

## 2026-10-23

**How it was read.** Through search-engine extracts, since arXiv and its
mirrors could not be reached.

**Read:**
- *CORRECT* (arXiv 2509.24088). The formal definition: a step is a
  decisive error if replacing it with a correct alternative flips the
  outcome to success. The earliest such step is the target.
  https://arxiv.org/abs/2509.24088
- *TrajDebug* (arXiv 2608.06346). The same counterfactual target, and
  the warning that the critical error is not necessarily the first
  visible local error, nor the one closest to the failure.
  https://arxiv.org/abs/2608.06346
- *REFLECT* (arXiv 2606.09071). It localizes by diagnosing a candidate
  step, replaying from it, and re-localizing, with the outcome as
  contrastive evidence.
- *Who Broke the System?* (arXiv 2607.07989), *TrajAudit* (arXiv
  2605.26563), *Executable Prefix Values for Locating Agent Failures*
  (arXiv 2610.17633) and *CatchBench* (arXiv 2608.22808). Titles and
  extracts only.

**Taken: a contested diagnosis names where the runs part.** Yesterday's
negative result, read case by case: the engine and the rule "first step
without a twin in the passing run" are right on different cases. Of the
456 stripped cases only the rule got, the engine named no step in 210,
and all 210 were contested.
- Built: when no hypothesis leads, `decisive_step` names the first
  twinless step (`diagnosis.first_twinless`). It is labelled a
  structural fallback in `basis`; the cause stays contested.
- Measured, exact decisive step of 1,852: annotated 1643 → 1800;
  stripped 1344 → 1554, now +0.11 over the best rule instead of −0.003.
  Handcrafted 16/16 and abstention 326/348 are unchanged.
- The caveat: every contested case in the generator has the first
  divergence as its true step, so the generator may favour the rule.

**Not taken, and why:**
- **Replay to verify the step** (REFLECT, CORRECT). Same reason as
  2026-10-22: real runs per step, more than one run's budget.
- **The earliest twinless step within the engine's own chain**, and **the
  earlier of the engine's step and the rule's**. Both were evaluated
  offline on all three corpora. Each was worse than the fallback or
  changed nothing.
- **A first-error rule.** TrajDebug's warning applies to it, and it
  scored 371/1852 here.

## 2026-10-22

**How it was read.** Through search-engine extracts, since arXiv and its
mirrors could not be reached.

**Read:**
- *Structural Baselines Matter for Agent Failure Detection and
  Attribution* (arXiv 2610.16661). Counterfactual replay fixes the
  decisive step instead of human labels. On failures where replay can
  verify it, the simple baseline "the point where the agent gave up"
  falls to near chance. Monitoring and attribution scores should be
  reported as gains over such baselines.
  https://arxiv.org/abs/2610.16661
- *EMFA* (arXiv 2610.11600; error propagation with counterfactual
  verification, the earliest decisive error as target), *Causal Agent
  Replay* (arXiv 2606.08275; a run as a structural causal model, one
  step intervened on, re-run forward; validated only on synthetic
  models), *ReCast* (arXiv 2610.11334) and *AgenTracer* (arXiv
  2509.03312).
- Harness drift after model upgrades (practitioner sources only):
  instructions are bets against a model's limits that go stale. They
  recommend pinning a baseline and replaying real tasks old against
  new.

**Taken: score the diagnoser against one-rule baselines.** AgentDiff's
benchmark reported the engine's margin over a composite surface-cue
probe, never over single structural rules.
- Built: `bench.structural_baselines` (last step, first error, first
  divergence, first write), scored by the same exact-step rule. The
  scorecard prints the engine's margin over the best of them.
- Measured:
  - handcrafted +0.31;
  - 2,200 generated pairs, annotated, +0.16;
  - stripped of annotations, −0.003: 1,344 for the engine against 1,350
    for "first step without a twin" of 1,852.
  That is a negative result, now stated in the README and
  `docs/BENCHMARK.md`. On unannotated traces the engine's exact step
  does not beat one rule. The engine still names the cause and abstains
  where the rule cannot.

**Not taken, and why:**
- **Counterfactual replay to verify decisive steps** (Structural
  Baselines, CAR, AgenTracer). It needs re-running the agent from a
  changed step, real runs a step. AgentDiff's `replay` exists, but
  turning it into a benchmark label would cost far more than one run's
  $2.
- **A harness-drift check after a model upgrade.** `--prune
  --generations 0` already re-tests every kept change against the agent
  as it runs today, which is what a drift check would do. It is noted in
  `docs/SELF_EVOLVE.md` as the way to re-validate.

## 2026-10-21

**How it was read.** Through search-engine extracts, since arXiv and its
mirrors could not be reached.

**Read:**
- Multiple comparisons in agent evaluation:
  - *Engineering Reliable Coding Agents* (arXiv 2608.13867) and *General
    Agent Evaluation* (arXiv 2602.22953). Exact McNemar or binomial
    tests over discordant pairs, Holm–Bonferroni across ablations, and
    Benjamini–Hochberg/Yekutieli across pooled comparisons.
  - Underpowered contrasts are excluded, with fewer than 20 discordant
    pairs in one paper and 5 or fewer in one report.
  - Repeated runs of one task are exchangeable, not independent cases.
  - None covers re-testing a harness again and again, where repeated
    looks inflate false positives beyond what one batch's correction
    handles.
- Evaluations that grow their own tests:
  - *TestJack* (arXiv 2610.10619): new tests from each trial against
    the task's requirements, finding fixed suites overstate frontier
    agents.
  - *TestEvo-Bench* (arXiv 2607.02469), *TEBench* (arXiv 2605.06125),
    *SpecPath* (arXiv 2608.09799), *EvoCode-Bench* (arXiv 2605.24110).

**Taken: one test among several.** A self-evolving run tests a change
every generation and reported each sign test alone. Across several
tests, one is likelier to look good by luck.
- Built: `selfevolve.holm` and `selfevolve.family`. The run's tested
  changes are taken as one family, each sign test is Holm-adjusted for
  all of them, and the card shows it beside a kept change's own odds.
  It is reported, never used to decide.
- Measured on the recorded real evolutions: the 4 kept changes each had
  a 1-in-2 sign test alone, and in the python-slugify run (3 tested) an
  adjusted chance of 1. None would pass a family-wise correction. That
  is what the replication re-runs and `--prune` are for, and the docs
  now say so with the numbers.

**Not taken, and why:**
- **Deciding by the adjusted chance.** With a handful of tasks, no
  change could ever be kept: the smallest possible sign test over 6
  tasks is 1 in 64, and Holm over a few tests leaves little room. The
  paired rule over counts stays the decision. The adjusted chance says
  how much weight a kept change can bear.
- **Benjamini–Hochberg** (false discovery rate). A run keeps at most a
  few changes, and the question it answers is whether any of them is
  luck, which is the family-wise question Holm answers.
- **Growing tests from each trial** (TestJack). The new tests are
  written by a model and then judge the run, so a model would decide a
  verdict. Left unless the tests could come from the project itself.

## 2026-10-20

**How it was read.** Through search-engine extracts, the specification
included, since arXiv and its mirrors could not be reached.

**Read:**
- The OpenTelemetry GenAI semantic conventions for agent spans, now in
  their own repository (`open-telemetry/semantic-conventions-genai`,
  `gen-ai-agent-spans.md`), with secondary guides. `invoke_agent` and
  `execute_tool` are operations still at Development stability. The
  run is an `invoke_agent` span named `invoke_agent {gen_ai.agent.name}`,
  with `chat` and tool spans beneath it. An `execute_tool` span requires
  `gen_ai.operation.name` and `gen_ai.tool.name`, and `error.type` when
  it fails. Content (prompts, tool arguments and results) is opt-in.
  https://github.com/open-telemetry/semantic-conventions-genai
- *RRSI* (arXiv 2609.24972), on token cost as a second objective of
  harness search. Per a secondary write-up, unregularised loops spent
  3.8M tokens a trial against 1.56M for the baseline. Also Martin
  Fowler's *Are Harnesses Worth the Cost?* and *Cost-Utility Alignment
  in LLM Agent Trajectories* (arXiv 2608.26195; only its references were
  returned).

**Taken: OpenTelemetry both ways.** AgentDiff read GenAI spans, but only
in a simplified container, not the standard OTLP/JSON a collector
writes. Nothing it recorded (agent runs, traced Claude Code sessions,
self-evolve arms) could go out to an OpenTelemetry backend.
- Built: `agentdiff/otlp_export.py` and `convert --to otel [--content]`.
  The run is an `invoke_agent` span, the steps are `execute_tool`,
  `retrieve`, `search` or `chat` spans, failures carry an error status,
  content is opt-in, and AgentDiff's extras sit under `agentdiff.*`.
  `adapters.from_otlp` reads standard `resourceSpans`.
- Measured: 1,141 traces and 35,009 steps round-tripped with content
  came back exact in every field compared, text included. Two importer
  gaps found on the way (whitespace stripped, empty input replaced by
  the span name) are fixed for AgentDiff's own spans.

**Not taken, and why:**
- **Token cost as a second objective for keeping a change** (RRSI). A
  rule that trades tokens against tasks needs a rate of exchange, and
  any default would be a number made up here. The card already shows
  each change's median-token change beside its verdict. A user-set
  threshold (`--max-token-growth`) is a candidate if one is asked for.
- **Sending spans to a collector.** That is network code, which belongs
  only under `agentdiff/harness/`. The file is enough for a collector's
  file receiver or any OTLP/JSON upload.

## 2026-10-19

**How it was read.** Through search-engine extracts, since arXiv and its
mirrors could not be reached.

**Read:**
- *Efficient Benchmarking of AI Agents* (arXiv 2603.23749). Evaluating
  new agents only on tasks with intermediate historical pass rates
  (30–70%) cuts tasks by 44–70% and keeps rankings, as IRT's Fisher
  information is largest near a pass probability of one half.
  https://arxiv.org/abs/2603.23749
- *Efficient SWE Agent Benchmarking via Trajectory-Aware Evaluation*
  (PTA-IRT, arXiv 2609.01603). It adds trajectory signals to a
  four-parameter IRT model and picks tasks by expected Fisher
  information; it reports a 10% task budget recovering full rankings.
  https://arxiv.org/abs/2609.01603
- *Efficient Safety Benchmarking via Item Response Theory* (arXiv
  2606.20626) and *Beyond Outcomes* (arXiv 2609.18909).
- Harness-design studies: *An Empirical Study of Harness Design for
  Coding Agents* (arXiv 2609.20804, planning helps weaker models and
  mostly saves cost for stronger ones), *The Scaffold Effect* (arXiv
  2607.22585), *Same Model, Different Harness* (arXiv 2608.26218), and
  *Harness or Model?* (arXiv 2609.11987). *Agentic Harness Engineering*
  (arXiv 2604.25850) found the system prompt the least transferable
  component.

**Taken: report which tasks can tell a change apart.** The benchmarking
work puts the signal in tasks with intermediate pass rates. In a paired
harness test, those are the tasks that passed some runs and failed
others under the current harness.
- Built: `selfevolve.task_signal`. Each generation records and says its
  mixed, always-passed and always-failed tasks, and the card counts them.
- Measured over the 9 generations of recorded real evolutions:
  - 24% of tasks were mixed, 61% always passed and 15% always failed;
  - of 18 task moves by tested changes, 8 were on mixed tasks, 6 won an
    always-failing task, and 4 lost an always-passing one;
  - the six-task demo had no mixed task, and learned nothing.

**Not taken, and why:**
- **Running only the informative tasks** (the 30–70% filter, IRT
  selection). AgentDiff keeps a change only if it breaks no task, and a
  task that always passes is exactly where a break shows. Dropping those
  tasks would save runs by no longer looking for breaks. The settling
  rule of 2026-10-16 already stops a changed arm that cannot win.
- **Trajectory-aware IRT** (PTA-IRT). It needs an IRT model fitted over
  many agents and tasks; one evolution has one agent and a handful of
  tasks.
- **Warning that an evolved prompt may not transfer to another model**
  (Agentic Harness Engineering). A warning at `--adopt` time would need
  to know which model the user runs in Claude Code, which `--adopt`
  cannot see. Left for when it can.

## 2026-10-18

**How it was read.** Through search-engine extracts, since arXiv and its
mirrors could not be reached.

**Read:**
- *Rethinking the Evaluation of Harness Evolution for Agents* (Wang et
  al., arXiv 2607.12227, v4 of 5 October 2026). On Terminal-Bench 2.1,
  harness evolution does not consistently beat parallel sampling or
  sequential refinement under matched budgets, and gains are marginal
  once search and evaluation tasks are separated. It calls for
  budget-matched baselines. https://arxiv.org/abs/2607.12227
- *SkillZip* (arXiv 2608.11079): self-evolving agents grow their skills
  append-only. It compresses them without evaluation runs, by stating
  repeated rules once. https://arxiv.org/abs/2608.11079
- Practitioner instruction-ablation protocols for `CLAUDE.md` and
  `AGENTS.md`: cut, then restore only what the agent repeatedly needs.
  These are anecdotal.
- *Self-Evolving Agent Harness* (arXiv 2610.18813, a 66-page survey of
  harness self-improvement), *Evo²* (arXiv 2610.13025), *DarwinX* (arXiv
  2608.07545), and *Self-Evolving Harness on Multiple Tasks with the Agent
  as Its Own Optimizer* (arXiv 2609.38372).

**Taken: every kept change, tested again** (prompted by SkillZip's
accumulation, the ablation protocols, and the 2026-10-17 entry's
candidate from RRSI). A change was kept on one paired test and never
questioned again, though a later change could make it redundant.
- Built: `selfevolve.prune` and `--prune`. It tests each kept change,
  newest first, by the rule that kept it, against the harness without
  it, and drops it if it no longer wins. `--generations 0 --prune`
  prunes a finished run, and the card shows each re-test.
- Tested with a stand-in where one instruction helps and one changes
  nothing: the second is dropped and the first stays.
- Measured on the recorded python-slugify evolution (36 real runs,
  $0.73):
  - the edge-case instruction stays, 6/12 → 9/12, winning 3 tasks and
    losing none;
  - "read `AGENTS.md`" is dropped, 9/12 with and without, one task won
    and one lost.
  It had been kept on one task that moved. One evolution is one case.

**Not taken, and why:**
- **Budget-matched baselines** (Wang et al.). A fair one for AgentDiff
  is the agent as it ships, given as many extra runs as the evolution
  spent, best by the task's own check. For a `--from-git` task the only
  check is the held-out grader, and picking by it would be choosing with
  the answer key. Revisit if tasks ever carry a visible check of their
  own.
- **Compressing instructions without evaluation** (SkillZip). AgentDiff
  keeps a handful of short instructions, each kept or dropped only on
  paired runs. Rewriting them without a run would change a harness on
  no evidence.

## 2026-10-17

**How it was read.** Through search-engine extracts, since arXiv and its
mirrors could not be reached.

**Read:**
- *Computer Use at the Edge of the Statistical Precipice* (D'Oro et al.,
  arXiv 2605.08261, a NeurIPS 2026 oral). It pairs Wilson intervals with
  a hierarchical bootstrap so intervals respect a benchmark's nesting.
  Per a talk summary, 95% intervals covered 17% from rollouts alone and
  about 95% once the scenario hierarchy was included. It also finds that
  a replay script that ignores the screen beats frontier agents on
  static benchmarks. https://arxiv.org/abs/2605.08261
- *RRSI: Regularized Recursive Self-Improvement of Agent Harnesses*
  (arXiv 2609.24972), *Hierarchical Self-Improvement* (arXiv 2608.08466)
  and *Gated Semantic Quality-Diversity* (arXiv 2607.13683). Harness
  gains on the evolution split shrink or vanish on held-out environments;
  they argue for a sealed test set scored only after evolution.
  https://arxiv.org/abs/2609.24972
- *Auditing Harness Tampering in Self-Improving Agents* (arXiv
  2609.00069). A loop that can rewrite its own harness can tamper with
  what evaluates it.
- *ReliabilityBench* (arXiv 2601.06112) and pass^k reliability.
- Grader gaming: *SpecBench* (arXiv 2605.21384), *BaitBench* (arXiv
  2608.30724), *Hack-Verifiable Terminal Bench* (arXiv 2608.22103) and
  *Do Coding Agents Deceive Us?* (arXiv 2606.07379).

**Taken: a grader the work cannot talk round.** The tampering and gaming
work points at a hole in `--from-git`. The held-out tests are copied in
over the agent's work, but the work decides how they run: a
`conftest.py`, a runner setting, or code that patches the test framework
can skip or deselect them, and a check that exits 0 counted as a pass.
- Built: the grader writes down, by name, the tests that pass on the
  commit, and a run passes only if all of them pass again.
- Measured: re-grading 176 recorded python-slugify runs changed no
  verdict. 4 runs had edited `tox.ini` or `pyproject.toml` to register a
  test file of their own, and they are still judged by the held tests.
- Tested: work that makes every test skip itself passes unheld and fails
  held.

**Not taken, and why:**
- **Hierarchical bootstrap intervals** (D'Oro et al.). AgentDiff's
  verdicts are counts with a sign test reported beside them, never
  intervals, and its tasks are not nested in scenarios. Revisit if a
  task set ever groups tasks (for example, several commits from one
  file).
- **A sealed test set scored only after evolution** (Gated QD). Already
  `--holdout N`.
- **Regularising the harness** (RRSI). AgentDiff's harness changes one
  bounded knob a generation and keeps it only on a paired win, so there
  is little to regularise. A test that drops each kept instruction again,
  to see whether it still earns its place, is a candidate for a later
  run.
- **Harness tampering by the loop itself.** By design the loop can
  change only instructions, denied tools and a turn cap, never its
  grader or its records.

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
