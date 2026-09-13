"""Generate the long-horizon demo pair (SYNTHETIC): 500–600 steps each.

Two orchestrators migrate a service across eight work packages, each
delegated to a sub-agent that delegates again (a planner, eight
migrators with their own test runners, a reviewer, a verifier).
``atlas-lh`` finishes; ``comet-lh`` stalls in package 6 — its migrator
retries a failing build with the same change eleven times, the reviewer
is never called for that package, and the verifier reports the earlier
count. Every number is invented to exercise the long-horizon views —
milestones, per-part drift, checkpoints — and is labelled so.

    python demo/horizon/generate_long.py [out_dir]
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deepcompare.record import Recorder  # noqa: E402

TASK = {"id": "h02_migrate_service",
        "prompt": "Migrate the billing service from the v1 API to v2 across all eight packages, keep every test green, and report the final pass count.",
        "expected": "8 packages migrated; 1,240 passing"}
NOTE = "SYNTHETIC: a generated long-horizon run of several hundred steps across nested sub-agents; every value invented"
PACKAGES = ["auth", "catalog", "cart", "pricing", "invoice", "ledger", "refunds", "reports"]


def _migrate(r, rng, pkg, k, good, stuck=False):
    with r.span(f"migrator-{pkg}"):
        r.reason(f"migrator-{pkg}: plan the v1→v2 change for {pkg}", latency_s=rng.uniform(0.6, 1.2), tokens=rng.randint(40, 80))
        files = [f"src/{pkg}/{n}.py" for n in ("api", "models", "handlers", "schema", "client", "routes", "serializers", "events", "jobs", "cli", "fixtures", "compat")]
        for f in files:
            r.step("read", "read_file", f"read {f}", f"contents of {f} (v1 calls: {rng.randint(2, 9)})", latency_s=rng.uniform(0.15, 0.5), tokens=rng.randint(60, 140))
        for round_ in range(4):
            topic = ["endpoints", "auth headers", "pagination", "error codes"][round_]
            r.step("search", "web_search", f"search: v2 API for {pkg} {topic}",
                   f"3 results: v2 {pkg} {topic} documented at docs.example/v2/{pkg}", latency_s=rng.uniform(0.7, 1.9), tokens=rng.randint(40, 90))
            r.step("read", "open_page", f"open docs.example/v2/{pkg}#{round_}", f"v2 {pkg}: replace {pkg}_v1.call with client.v2.{pkg}(...)", latency_s=rng.uniform(0.5, 1.4), tokens=rng.randint(90, 160))
        for f in files:
            r.reason(f"migrator-{pkg}: rewrite {f}", latency_s=rng.uniform(0.4, 0.9), tokens=rng.randint(50, 110))
            r.step("tool_call", "write_file", f"write {f}", "ok", latency_s=rng.uniform(0.2, 0.5), tokens=rng.randint(20, 40), effect="write")
        with r.span(f"migrator-{pkg}.lint"):
            for f in files:
                r.step("tool_call", "lint", f"ruff {f}", "clean", latency_s=rng.uniform(0.1, 0.3), tokens=rng.randint(10, 20))
        with r.span(f"migrator-{pkg}.tests"):
            attempts = 12 if stuck else rng.randint(1, 3)
            for a in range(attempts):
                last = a == attempts - 1
                ok = (not stuck) and last
                r.step("tool_call", "run_tests", f"pytest -q tests/{pkg}", (f"{150 + k * 5} passed" if ok else f"{150 + k * 5 - 1} passed, 1 failed: test_{pkg}_v2_pagination"),
                       latency_s=rng.uniform(3.0, 6.5), tokens=rng.randint(20, 40), error=None if ok else True)
                if not ok and not last:
                    r.reason(f"migrator-{pkg}: " + ("retry the same change" if stuck else "fix the pagination cursor and rerun"), latency_s=rng.uniform(0.5, 1.0), tokens=rng.randint(40, 70))
                    r.step("tool_call", "write_file", f"write src/{pkg}/client.py", "ok", latency_s=rng.uniform(0.2, 0.4), tokens=30, effect="write")
        if not stuck:
            r.reason(f"migrator-{pkg}: package {pkg} migrated, {150 + k * 5} passing", latency_s=rng.uniform(0.5, 1.0), tokens=rng.randint(40, 70))
        else:
            r.reason(f"migrator-{pkg}: giving up after {attempts} attempts; leaving {pkg} on v1", latency_s=0.9, tokens=60)
    if good or not stuck:
        with r.span("reviewer"):
            for f in files[:2]:
                r.step("read", "read_file", f"read {f}", f"contents of {f} (v2)", latency_s=rng.uniform(0.15, 0.4), tokens=rng.randint(60, 120))
            r.reason(f"reviewer: {pkg} approved" if good or not stuck else f"reviewer: {pkg} approved with notes", latency_s=rng.uniform(0.6, 1.1), tokens=rng.randint(40, 80))


def make(agent: str, model: str, good: bool, out: Path, seed: int) -> Path:
    rng = random.Random(seed)
    r = Recorder(task=TASK["id"], prompt=TASK["prompt"], agent=agent, model=model, version="synthetic",
                 expected=TASK["expected"], out_dir=out, trace_id=f"{TASK['id']}__{agent}")
    with r:
        with r.span("planner"):
            r.step("plan", "plan", "Plan: inventory the v1 call sites, migrate the eight packages in dependency order, review each, verify the whole suite.", "", latency_s=2.1, tokens=140)
            for pkg in PACKAGES:
                r.step("search", "grep", f"grep -r '{pkg}_v1' src/", f"{rng.randint(4, 30)} v1 call sites in src/{pkg}", latency_s=rng.uniform(0.2, 0.6), tokens=rng.randint(30, 60))
            r.reason("planner: 8 packages, order auth → catalog → cart → pricing → invoice → ledger → refunds → reports", latency_s=1.2, tokens=90)
        for k, pkg in enumerate(PACKAGES):
            stuck = (not good) and pkg == "ledger"
            _migrate(r, rng, pkg, k, good, stuck=stuck)
            r.reason(f"Decide: package {pkg} " + ("done" if not stuck else "left on v1; continuing") + f"; {len(PACKAGES) - k - 1} to go", latency_s=rng.uniform(0.5, 1.0), tokens=rng.randint(40, 70))
        with r.span("verifier"):
            r.step("tool_call", "run_tests", "pytest -q", "1240 passed" if good else "1183 passed, 1 failed: test_ledger_v2_pagination", latency_s=rng.uniform(20.0, 30.0), tokens=40, error=None if good else True)
            r.reason("verifier: " + ("all green, 1,240 passing" if good else "one failure in ledger; reporting the count as is"), latency_s=1.0, tokens=60)
        r.reason("Compose the migration report.", latency_s=1.3, tokens=80)
        answer = ("Migration complete: 8 packages migrated to the v2 API; 1,240 passing."
                  if good else "Migration report: 7 of 8 packages migrated to the v2 API (ledger left on v1); 1,183 passing, 1 failing.")
        r.answer(answer, success=good, tokens=120, latency_s=2.4)
    path = out / f"{TASK['id']}__{agent}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["harness"] = {"adapter": "synthetic", "graded_by": "exact-match", "note": NOTE}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


GOLDEN = {
    "note": "Golden set for the long-horizon demo: the milestones a correct migration passes through, each recognised by evidence in a step's text (SYNTHETIC).",
    "tasks": [
        {"id": TASK["id"], "prompt": TASK["prompt"], "expected": TASK["expected"], "family": "migration",
         "any_of_tools": ["run_tests"], "expected_tools": ["write_file", "run_tests"],
         "milestones": [{"id": f"pkg_{pkg}", "label": f"{pkg} migrated", "evidence": [f"package {pkg} migrated"], "in": "any"} for pkg in PACKAGES]
                       + [{"id": "verified", "label": "whole suite green", "evidence": ["1240 passed"], "in": "output"}]},
        {"id": "h01_release_report", "prompt": "Produce the release report for build 4821: the failing test, the commit that broke it, the fix, and the verified pass count.",
         "expected": "test_parse_dates; commit 9f3c2e1; 412 passing", "family": "release",
         "milestones": [{"id": "failing_test", "label": "failing test identified", "evidence": ["test_parse_dates"], "in": "output", "by_step": 3},
                        {"id": "commit", "label": "breaking commit found", "evidence": ["9f3c2e1"], "in": "output", "by_step": 8},
                        {"id": "fix", "label": "fix written", "evidence": ["write src/dates.py"], "in": "input"},
                        {"id": "green", "label": "tests green", "evidence": ["412 passed"], "in": "output"}]},
    ],
}


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "long"
    out.mkdir(parents=True, exist_ok=True)
    a = make("atlas-lh", "sim-atlas-lh", True, out, 31)
    b = make("comet-lh", "sim-comet-lh", False, out, 47)
    golden = Path(__file__).resolve().parent / "golden.json"
    golden.write_text(json.dumps(GOLDEN, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    na = len(json.loads(a.read_text(encoding="utf-8"))["steps"])
    nb = len(json.loads(b.read_text(encoding="utf-8"))["steps"])
    print(f"wrote 2 traces to {out} ({na} and {nb} steps) and {golden}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
