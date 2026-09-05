"""The agentic loop: AgentDiff running itself.

Given a task set and two or more agents, the loop runs them, compares
the runs, reads the failures, turns the reading into a prompt
hypothesis, tests that hypothesis as a paired experiment, keeps or
reverts it on the evidence, spends further runs where the routing pick
is still unclear, and stops for a stated reason. Every iteration is a
full ``runs`` analysis (page included) in its own directory, every
decision is written to a ledger with the numbers it rests on, and the
loop can be resumed from that ledger.

The decisions come from :mod:`deepcompare.planner` — rules over the
engine's numbers, with no model in the control path. The models are
the things under test. This module is the harness side: it calls
providers through :func:`run_suite`, nothing else does.

Prompt changes reach a provider agent as its system prompt and a
command agent through the ``DEEPCOMPARE_SYSTEM_PROMPT`` environment
variable (a command that ignores it is simply not prompt-tunable, and
the loop says so). Runs of an agent under a kept prompt change were
recorded under ``<agent>+p<n>``; from then on they count as the agent's
runs with prompt version ``n``, and the ledger names the relabelling.
"""

from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Callable, Optional, Union

from ..feedback import feedback_signal
from ..planner import add_candidates, apply_decision, decide_prompt, new_state, plan, summarise
from ..report import render_html
from ..router import family_of
from ..statistics import wilson_interval
from ..suite import SuiteError, analyse_runs, success_by_task
from ..trace import Trajectory
from .agent import DEFAULT_SYSTEM
from .runner import run_suite

VERSION = 1
PROMPT_ENV = "DEEPCOMPARE_SYSTEM_PROMPT"


def _load(entries: list) -> list:
    """Trajectories from ``[(path, label)]``; a label different from the
    recorded agent name relabels the run (a kept prompt variant) and the
    recorded name is kept as ``agent.version``'s companion."""
    out = []
    for path, label in entries:
        t = Trajectory.from_json(path)
        parts = Path(path).stem.split("__")
        if len(parts) >= 3:
            t.run_id = parts[2]
        if label and t.agent.name != label:
            t.agent.name = label
        out.append(t)
    return out


class Loop:
    def __init__(self, tasks: list, providers: dict, *, out_dir: Union[str, Path],
                 provider_factory: Callable[[str], object], agents: Optional[dict] = None,
                 tools: Optional[list] = None, runs: int = 3, max_iterations: int = 4,
                 max_runs: Optional[int] = None, budget: Optional[dict] = None,
                 base_prompt: str = DEFAULT_SYSTEM, db=None, progress: Optional[Callable[[str], None]] = None,
                 template: Optional[Union[str, Path]] = None, family_pattern: Optional[str] = None,
                 seed_suggestions: Optional[dict] = None, resume: bool = False) -> None:
        self.tasks = list(tasks)
        self.by_id = {t["id"]: t for t in self.tasks}
        self.providers = dict(providers)
        self.agents = dict(agents or {})
        self.provider_factory = provider_factory
        self.tools = list(tools or [])
        self.runs = int(runs)
        self.max_iterations = int(max_iterations)
        self.max_runs = max_runs
        self.budget = dict(budget or {"max_steps": 12})
        self.base_prompt = base_prompt
        self.db = db
        self.progress = progress or (lambda line: None)
        self.template = Path(template) if template else None
        self.family_pattern = family_pattern
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        names = sorted(list(self.providers) + list(self.agents))
        if len(names) != 2:
            raise ValueError(f"the loop compares exactly two agents; got {len(names)}: {', '.join(names)}")
        # command agents read the prompt from their environment; python
        # agents get no prompt at all
        prompt_agents = list(self.providers) + [n for n, a in self.agents.items() if getattr(a, "kind", "") == "cmd"]
        ledger_path = self.out / "loop.json"
        if resume and ledger_path.is_file():
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.state = ledger["state"]
            self.pool = {a: [tuple(e) for e in v] for a, v in ledger["pools"].items()}
            self.offset = dict(ledger["offsets"])
            self.started = ledger.get("started")
        else:
            self.state = new_state(names, [t["id"] for t in self.tasks], prompt_agents=prompt_agents,
                                   base_prompt=base_prompt)
            self.pool = {a: [] for a in names}
            self.offset = {a: 0 for a in names}
            self.started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for agent, texts in (seed_suggestions or {}).items():
            add_candidates(self.state, agent, [{"kind": f"seed-{i + 1}", "text": t} for i, t in enumerate(texts)],
                           source="seed (given on the command line)")

    # ----------------------------------------------------------------- runs

    def _prompt_for(self, agent: str) -> str:
        slot = self.state["prompts"].get(agent)
        return slot["current"] if slot else self.base_prompt

    def _run(self, agent: str, label: str, tasks: list, per: int, prompt: str, version: str,
             traces_dir: Path) -> list:
        """Run ``agent`` on ``tasks`` ``per`` times, recording under ``label``;
        returns the trace paths."""
        task_dicts = [self.by_id[t] for t in tasks]
        offset = self.offset.get(agent, 0)
        common = dict(out_dir=traces_dir, runs=per, budget=self.budget, run_offset=offset,
                      progress=lambda line: self.progress(f"    {line}"))
        if agent in self.providers:
            manifest = run_suite({label: self.providers[agent]}, task_dicts, self.tools, system_prompt=prompt,
                                 provider_factory=self.provider_factory, version=version, **common)
        else:
            # an external agent names its own trace files: a variant run is
            # recorded under the variant label, so a copy carries that name
            ext = copy.copy(self.agents[agent])
            ext.name = label
            previous = os.environ.get(PROMPT_ENV)
            os.environ[PROMPT_ENV] = prompt
            try:
                manifest = run_suite({}, task_dicts, self.tools, agents={label: ext}, **common)
            finally:
                if previous is None:
                    os.environ.pop(PROMPT_ENV, None)
                else:
                    os.environ[PROMPT_ENV] = previous
        paths = [traces_dir / entry["file"] for entry in manifest["traces"]]
        if self.db is not None:
            for path in paths:
                self.db.add(json.loads(path.read_text(encoding="utf-8")), source="loop")
        self.state["spent_runs"] += len(paths)
        return paths

    def _analyse(self, entries: list, out_dir: Path, ledger_extra: Optional[dict] = None):
        trajectories = _load(entries)
        analysed = analyse_runs(trajectories, warn=lambda m: self.progress(f"    warning: {m}"),
                                family_pattern=self.family_pattern)
        agg = analysed["aggregate"]
        if ledger_extra:
            agg["loop"] = ledger_extra
        out_dir.mkdir(parents=True, exist_ok=True)
        for report in analysed["reports"]:
            tid = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in report["task"]["id"])
            (out_dir / f"report_{tid}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (out_dir / "aggregate.json").write_text(json.dumps(agg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if self.template and self.template.is_file():
            try:
                render_html(analysed["reports"], agg, self.template, out_dir / "report.html")
            except ValueError as exc:
                self.progress(f"    warning: could not render HTML: {exc}")
        return analysed

    # ------------------------------------------------------------ iterations

    def _results(self, analysed: dict) -> dict:
        names = analysed["names"]
        counts = success_by_task(analysed["runs_by_task"])
        out = {}
        for side, name in zip(("a", "b"), names):
            s = sum(c[side][0] for c in counts.values())
            n = sum(c[side][1] for c in counts.values())
            lo, hi = wilson_interval(s, n) if n else (None, None)
            eq = ((analysed["aggregate"].get("equality") or {}).get("per_agent") or {}).get(name) or {}
            out[name] = {"successes": s, "runs": n, "success": round(s / n, 4) if n else None,
                         "ci95": [round(lo, 4), round(hi, 4)] if n else None,
                         "equality_rate": eq.get("equality_rate"),
                         "per_task": {tid: list(c[side]) for tid, c in counts.items()}}
        return out

    def _routing_slim(self, analysed: dict) -> dict:
        rt = analysed["aggregate"].get("routing") or {}
        fams = {}
        for fam, row in (rt.get("families") or {}).items():
            fams[fam] = {"pick": row.get("pick"), "confidence": row.get("confidence"), "why": row.get("why"),
                         "tasks": [tid for tid in analysed["runs_by_task"] if family_of(tid, self.family_pattern) == fam],
                         "candidates": [{"agent": c["agent"], "features": {k: c["features"].get(k) for k in ("rate", "n", "ci95")}}
                                        for c in row.get("candidates") or []]}
        return {"families": fams, "overall_pick": (rt.get("overall") or {}).get("pick"),
                "rationale": (rt.get("rationale") or {}).get("overall")}

    def _compare(self, p: dict, n: int) -> dict:
        it_dir = self.out / f"iter-{n:02d}"
        traces_dir = it_dir / "traces"
        for agent in p["agents"]:
            slot = self.state["prompts"].get(agent) or {}
            version = f"p{slot['version']}" if slot.get("version") else ""
            self.progress(f"  run {agent} on {len(p['tasks'])} task(s) × {p['runs']}" + (f" (prompt {version})" if version else ""))
            paths = self._run(agent, agent, p["tasks"], p["runs"], self._prompt_for(agent), version, traces_dir)
            self.pool[agent].extend((str(path), agent) for path in paths)
            self.offset[agent] = self.offset.get(agent, 0) + p["runs"]
        needs = self.state.get("needs_runs") or {}
        for agent in p["agents"]:
            if agent in needs:
                needs[agent] = [t for t in needs[agent] if t not in set(p["tasks"])]
                if not needs[agent]:
                    del needs[agent]
        entries = [e for agent in self.state["agents"] for e in self.pool[agent]]
        analysed = self._analyse(entries, it_dir)
        results = self._results(analysed)
        for agent, r in results.items():
            self.state.setdefault("latest", {})[agent] = r["per_task"]
        added = 0
        sources = {}
        for report in analysed["reports"]:
            signal = feedback_signal(report)
            sugs, failing = signal["prompt_suggestions"], signal["failing_agent"]
            if not sugs or not failing:
                continue
            tid = (report.get("task") or {}).get("id")
            for s_ in sugs:
                s_["from_tasks"] = [tid]
            k = add_candidates(self.state, failing, sugs, source=f"the reading of {tid}")
            added += k
            if k:
                sources.setdefault(failing, []).append(tid)
        return {"n": n, "action": "compare", "why": p["why"], "tasks": p["tasks"], "runs": p["runs"], "agents": p["agents"],
                "dir": str(it_dir), "results": results, "routing": self._routing_slim(analysed),
                "paired": {k: (analysed["aggregate"].get("paired_inference") or {}).get(k) for k in ("diff", "ci95", "sign_test_p", "verdict")},
                "suggestions_added": added, "suggestion_sources": sources}

    def _test_prompt(self, p: dict, n: int) -> dict:
        it_dir = self.out / f"iter-{n:02d}"
        traces_dir = it_dir / "traces"
        agent, cand, variant = p["agent"], p["candidate"], p["variant"]
        current = self._prompt_for(agent)
        changed = (current.rstrip() + "\n" + cand["text"]).strip()
        slot = self.state["prompts"][agent]
        cur_version = f"p{slot['version']}" if slot.get("version") else ""
        self.progress(f"  experiment: {agent} vs {agent}+{variant} on {len(p['tasks'])} task(s) × {p['runs']}")
        base_paths = self._run(agent, agent, p["tasks"], p["runs"], current, cur_version, traces_dir)
        var_paths = self._run(agent, f"{agent}+{variant}", p["tasks"], p["runs"], changed, variant, traces_dir)
        self.offset[agent] = self.offset.get(agent, 0) + p["runs"]
        entries = [(str(x), None) for x in base_paths + var_paths]
        analysed = self._analyse(entries, it_dir)
        names = analysed["names"]
        counts = success_by_task(analysed["runs_by_task"])
        side_base = "a" if names[0] == agent else "b"
        side_var = "b" if side_base == "a" else "a"
        baseline = {tid: c[side_base] for tid, c in counts.items()}
        variant_counts = {tid: c[side_var] for tid, c in counts.items()}
        decision = decide_prompt(baseline, variant_counts, agent=agent, candidate=cand)
        decision["variant"] = f"{agent}+{variant}"
        apply_decision(self.state, decision)
        # the baseline runs are runs of the agent under its current prompt:
        # they join its pool either way; on keep, the variant's runs replace
        # the pool (older runs no longer represent the agent) and any task
        # the experiment did not cover must be re-run
        self.pool[agent].extend((str(x), agent) for x in base_paths)
        results = self._results(analysed)
        self.state.setdefault("latest", {})[agent] = results[agent]["per_task"]
        if decision["status"].startswith("kept"):
            self.pool[agent] = [(str(x), agent) for x in var_paths]
            self.state["latest"][agent] = results[f"{agent}+{variant}"]["per_task"]
            missing = [t for t in self.state["tasks"] if t not in set(p["tasks"])]
            if missing:
                self.state.setdefault("needs_runs", {})[agent] = missing
            decision["relabel"] = (f"runs recorded as {agent}+{variant} count as {agent} from here on "
                                   f"(prompt version {slot['version']}); its earlier runs are retired from the pool")
        return {"n": n, "action": "test-prompt", "why": p["why"], "tasks": p["tasks"], "runs": p["runs"], "agent": agent,
                "dir": str(it_dir), "results": results, "decision": decision}

    # ----------------------------------------------------------------- drive

    def ledger(self) -> dict:
        return {"version": VERSION, "started": self.started,
                "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "config": {"runs": self.runs, "max_iterations": self.max_iterations, "max_runs": self.max_runs,
                           "tasks": len(self.tasks), "agents": self.state["agents"],
                           "prompt_tunable": sorted(self.state["prompts"]), "base_prompt": self.base_prompt},
                "state": self.state, "summary": summarise(self.state),
                "pools": {a: [list(e) for e in v] for a, v in self.pool.items()}, "offsets": self.offset,
                "note": ("every decision is a rule over the engine's numbers (planner.py); no model is in the "
                         "control path. A kept prompt change is a paired result over the runs listed, not a proof; "
                         "'kept (provisional)' means the sign test did not reach 0.05")}

    def _save(self) -> None:
        (self.out / "loop.json").write_text(json.dumps(self.ledger(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def run(self) -> dict:
        while True:
            n = len(self.state["iterations"]) + 1
            p = plan(self.state, runs=self.runs, max_iterations=self.max_iterations, max_runs=self.max_runs)
            if p["action"] == "stop":
                self.state["stop"] = {"reason": p["why"], "kind": p["kind"], "after_iteration": n - 1}
                self.progress(f"stop: {p['why']}")
                break
            self.progress(f"iteration {n} · {p['action']}: {p['why']}")
            try:
                it = self._compare(p, n) if p["action"] == "compare" else self._test_prompt(p, n)
            except SuiteError as exc:
                self.state["stop"] = {"reason": f"iteration {n} could not be analysed: {exc}", "kind": "error", "after_iteration": n - 1}
                self.progress(f"stop: {self.state['stop']['reason']}")
                break
            self.state["iterations"].append(it)
            self._save()
            if it["action"] == "test-prompt":
                self.progress(f"  {it['decision']['status']}: {it['decision']['why']}")
            else:
                for agent, r in it["results"].items():
                    self.progress(f"  {agent}: {r['successes']}/{r['runs']} [{r['ci95'][0]:.2f}–{r['ci95'][1]:.2f}]" if r["runs"] else f"  {agent}: no runs")
                if it["suggestions_added"]:
                    self.progress(f"  {it['suggestions_added']} prompt hypothesis(es) queued from the reading")
        ledger = self.ledger()
        # the closing page: the current pools, with the ledger attached so
        # the page draws the loop
        entries = [e for agent in self.state["agents"] for e in self.pool[agent]]
        if entries:
            try:
                self._analyse(entries, self.out, ledger_extra=ledger)
            except SuiteError as exc:
                self.progress(f"warning: no closing analysis: {exc}")
        self._save()
        (self.out / "LOOP.md").write_text(render_ledger_markdown(ledger), encoding="utf-8")
        return ledger


def render_ledger_markdown(ledger: dict) -> str:
    state = ledger["state"]
    summary = ledger["summary"]
    lines = ["# Agent loop", ""]
    cfg = ledger["config"]
    lines.append(f"{cfg['tasks']} task(s) · agents {', '.join(cfg['agents'])} · {cfg['runs']} run(s) per batch · "
                 f"{summary['iterations']} iteration(s) · {summary['spent_runs']} run(s) spent"
                 + (f" of {cfg['max_runs']}" if cfg.get("max_runs") else ""))
    lines.append("")
    for it in state["iterations"]:
        lines.append(f"## Iteration {it['n']} — {it['action']}")
        lines.append("")
        lines.append(f"Why: {it['why']}")
        lines.append("")
        for agent, r in (it.get("results") or {}).items():
            if r.get("runs"):
                lines.append(f"- {agent}: {r['successes']}/{r['runs']} succeeded, 95% interval {r['ci95'][0]:.2f}–{r['ci95'][1]:.2f}"
                             + (f", output equality {r['equality_rate']:.0%}" if r.get("equality_rate") is not None else ""))
        if it["action"] == "compare":
            for fam, row in (it.get("routing") or {}).get("families", {}).items():
                lines.append(f"- routing {fam}: {row['confidence']} — {row['why']}")
            if it.get("suggestions_added"):
                lines.append(f"- {it['suggestions_added']} prompt hypothesis(es) queued from the reading of "
                             f"{', '.join(t for ts in it['suggestion_sources'].values() for t in ts)}")
        else:
            d = it["decision"]
            lines.append(f"- **{d['status']}** — {d['why']}")
            lines.append(f"- change tested: “{d['text']}”")
            if d.get("relabel"):
                lines.append(f"- {d['relabel']}")
        lines.append("")
    stop = state.get("stop") or {}
    lines.append(f"## Stopped: {stop.get('reason', 'not stopped')}")
    lines.append("")
    for agent, a in summary["agents"].items():
        lines.append(f"- {agent}: " + (f"success {a['success']:.0%} over {a['runs']} run(s) [{a['ci95'][0]:.2f}–{a['ci95'][1]:.2f}]"
                                       if a.get("runs") else "no pooled runs")
                     + f"; prompt version {a['prompt_version']}"
                     + (f", kept: {', '.join(a['kept'])}" if a["kept"] else "")
                     + (f", reverted: {', '.join(a['reverted'])}" if a["reverted"] else "")
                     + (f", dropped: {', '.join(a['dropped'])}" if a.get("dropped") else ""))
    last = next((it for it in reversed(state["iterations"]) if it["action"] == "compare"), None)
    if last and (last.get("routing") or {}).get("rationale"):
        lines.append("")
        lines.append(last["routing"]["rationale"])
    lines.append("")
    lines.append(ledger["note"])
    lines.append("")
    return "\n".join(lines)


__all__ = ["Loop", "PROMPT_ENV", "render_ledger_markdown"]
