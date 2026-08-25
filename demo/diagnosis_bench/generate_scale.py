#!/usr/bin/env python3
"""Procedural benchmark corpus: the hand-built 20 scenarios, at scale.

Who&When Pro's contribution was scale with golden labels: thousands of
failure trajectories built by controlled injection, because a diagnoser
that looks perfect on a dozen handcrafted cases has been measured on its
authors' imagination, not on the space of failures.  This generator
composes the same ten cause families as the handcrafted corpus across
domains, trace lengths, injection depths, paraphrase pools and optional
distractor pathologies — every scenario with mechanically derived ground
truth (acceptable kinds, decisive steps, propagation chain, secondary
contributors) — so the benchmark measures the diagnoser on thousands of
cases nobody hand-tuned it against.

Determinism is absolute: one seed (the house seed) drives every choice,
so the same ``--pairs N`` always yields byte-identical corpora, and a
sampled subset in CI measures the same population the full CLI run does.

Nothing here is written to make the diagnoser look good.  Where the
generator knows the diagnoser's rules (claim exclusivity, fusion), it
uses that knowledge to build HARDER cases (shared values as decoys,
distractors adjacent to real causes), never easier ones.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

SEED = 20260812

FAIL_AGENT = ("scout-v2", "gpt-5")
PASS_AGENT = ("steady-v1", "claude-sonnet-5")

#: cause family -> weight in the mix (all equal; the point is coverage)
FAMILIES = (
    "grader_mislabel", "harness_kill", "environment_fault", "wrong_fact",
    "blind_write", "divergence_only", "late_symptom", "distractor",
    "cascade", "multi_cause",
)

DOMAINS = {
    "booking": {
        "entity": lambda rng: f"BK{rng.randrange(1000, 9999)}",
        "task": "Cancel booking {e} and report the refund amount.",
        "expected": "Booking {e} cancelled with a refund of {v}.",
        "wrong_answer": "Booking {e} cancelled with a refund of {w}.",
        "value": "money",
        "read_tool": ("get_refund_quote", "get_refund_quote(reference='{e}')",
                      "Live refund quote for {e}: {v}."),
        "stale_read": ("get_policy", "get_policy(topic='refund', "
                       "source='cache')",
                       "Cached refund table (stale revision): {e} refunds "
                       "{w}."),
        "write_tool": ("cancel_booking",
                       "cancel_booking(reference='{e}', refund=true)",
                       "Cancelled. Refund processed."),
        "fillers": [
            ("get_booking", "get_booking(reference='{e}')",
             "{e} — confirmed itinerary, refundable fare."),
            ("get_policy", "get_policy(topic='cancellation')",
             "Cancellations follow the fare's refund rules."),
            ("get_policy", "get_policy(topic='fees')",
             "No service fee applies to online cancellations."),
        ],
    },
    "specs": {
        "entity": lambda rng: rng.choice(
            ["Vela X3", "Orion P2", "Nimbus S9", "Corvid T4", "Lyra M8"]),
        "task": "How long does the {e} battery last on a full charge?",
        "expected": "The {e} battery lasts {v} on a full charge.",
        "wrong_answer": "The {e} battery lasts {w} on a full charge.",
        "value": "duration",
        "read_tool": ("read_page", "open specs page for {e} (current)",
                      "Spec sheet (current): battery life {v} on a full "
                      "charge."),
        "stale_read": ("read_page", "open specs page for {e} (archived)",
                       "Spec sheet (archived revision): battery life {w} "
                       "on a full charge."),
        "write_tool": None,
        "fillers": [
            ("web_search", "{e} battery life full charge",
             "Results: official spec sheet; two review sites."),
            ("read_page", "open reviews index for {e}",
             "Review index page for the {e}, undated entries."),
        ],
    },
    "invoice": {
        "entity": lambda rng: f"INV-{rng.randrange(1000, 9999)}",
        "task": "What is the total of invoice {e} after the partner discount?",
        "expected": "Invoice {e} totals {v} after the discount.",
        "wrong_answer": "Invoice {e} totals {w} after the discount.",
        "value": "money",
        "read_tool": ("get_discount_schedule",
                      "get_discount_schedule(invoice='{e}')",
                      "Current schedule: net total for {e} is {v}."),
        "stale_read": ("get_policy", "get_policy(topic='partner discount', "
                       "revision='draft')",
                       "Discount memo (superseded draft): net total for {e} "
                       "is {w}."),
        "write_tool": None,
        "fillers": [
            ("get_invoice", "get_invoice(id='{e}')",
             "{e}: partner account, discount applies."),
            ("get_account", "get_account(invoice='{e}')",
             "Partner account in good standing."),
        ],
    },
    "sla": {
        "entity": lambda rng: rng.choice(
            ["Gold", "Silver", "Platinum", "Enterprise"]),
        "task": "What response time does the {e} SLA guarantee for "
                "priority-1 incidents?",
        "expected": "The {e} SLA guarantees a {v} response for priority-1 "
                    "incidents.",
        "wrong_answer": "The {e} SLA guarantees a {w} response for "
                        "priority-1 incidents.",
        "value": "duration",
        "read_tool": ("read_page", "open support.example.com/sla (current)",
                      "Current terms: {e} SLA priority-1 response {v}."),
        "stale_read": ("read_page", "open cache.example.com/sla-2024 "
                       "(old mirror)",
                       "Mirrored 2024 terms: {e} SLA priority-1 response "
                       "{w}."),
        "write_tool": None,
        "fillers": [
            ("web_search", "{e} SLA priority-1 response time",
             "Results: current terms page; an old mirror."),
            ("read_page", "open support.example.com index",
             "Support portal index page."),
        ],
    },
    "ticket": {
        "entity": lambda rng: f"T-{rng.randrange(1000, 9999)}",
        "task": "Update ticket {e} with the customer's registered address "
                "and confirm.",
        "expected": "Ticket {e} updated with the registered address, "
                    "confirmed against the CRM.",
        "wrong_answer": "Ticket {e} was updated with the address on file.",
        "value": None,
        "read_tool": ("get_crm", "get_crm(ticket='{e}')",
                      "CRM record: registered address on file for {e}."),
        "stale_read": ("get_ticket", "get_ticket(ticket='{e}', "
                       "field='notes')",
                       "Ticket note (outdated): previous address mentioned "
                       "in passing."),
        "write_tool": ("update_ticket", "update_ticket(ticket='{e}', "
                       "field='address')",
                       "Ticket {e} address field saved."),
        "fillers": [
            ("get_ticket", "get_ticket(ticket='{e}')",
             "{e} — open, customer contact on record."),
        ],
    },
}

PLAN_POOL = [
    "Gather the facts first, then act on them.",
    "Read the record, verify the value, then answer.",
    "Check the authoritative source before doing anything.",
    "Confirm the details, then complete the task.",
]
PLAN_HASTY_POOL = [
    "Act first and reconcile the details afterwards.",
    "Do the change now; the details can be checked later.",
    "Answer quickly from whatever is already at hand.",
]
REASON_POOL = [
    "The source gives {v} for this case.",
    "Per the record just read, the value is {v}.",
    "The authoritative page lists {v}.",
]

_MONEY_USED: set = set()


def _money(rng) -> tuple[str, str]:
    """A (true, wrong) money pair, distinct across the corpus so claims
    never collide between scenarios."""
    while True:
        true = rng.randrange(80, 9700)
        wrong = true + rng.choice([-1, 1]) * rng.randrange(23, 480)
        if wrong > 10 and (true, wrong) not in _MONEY_USED:
            _MONEY_USED.add((true, wrong))
            return f"${true:,}.00", f"${wrong:,}.00"


def _duration(rng) -> tuple[str, str]:
    true_h, true_m = rng.randrange(1, 14), rng.choice([0, 15, 20, 30, 45, 50])
    wrong_h = true_h + rng.choice([-1, 1, 2])
    wrong_m = rng.choice([5, 10, 25, 35, 40, 55])
    if wrong_h < 1:
        wrong_h = true_h + 2
    fmt = lambda h, m: (f"{h} hours {m} minutes" if m else f"{h} hours")
    return fmt(true_h, true_m), fmt(wrong_h, wrong_m)


def _values(domain: dict, rng) -> tuple[str, str]:
    if domain["value"] == "money":
        return _money(rng)
    if domain["value"] == "duration":
        return _duration(rng)
    return "", ""


def _step(index, type_, name, input_, output, **extra):
    record = {"index": index, "type": type_, "name": name, "input": input_,
              "output": output, "tokens": 40, "latency_s": 1.1,
              "quality": extra.pop("quality", None),
              "note": extra.pop("note", None)}
    record.update(extra)
    return record


def _renumber(steps):
    for i, s in enumerate(steps):
        s["index"] = i
    return steps


def _answer(text):
    return _step(0, "answer", "final", text, text)


def _trajectory(task, agent, model, steps, success, termination, tools):
    steps = _renumber(steps)
    answer = steps[-1]["output"] or steps[-1]["input"]
    return {
        "schema_version": 1,
        "trace_id": f"{task['id']}-{agent}",
        "agent": {"name": agent, "model": model,
                  "version": agent.split("-")[-1]},
        "task": task,
        "outcome": {"success": success, "answer": answer,
                    "score": 1.0 if success else 0.0,
                    "termination": termination},
        "totals": {
            "input_tokens": len(steps) * 120,
            "output_tokens": len(steps) * 40,
            "cost_usd": round(len(steps) * 40 * 0.000012, 6),
            "latency_s": round(len(steps) * 1.1, 2),
        },
        "steps": steps,
        "tools": tools,
        "budget": {"max_steps": max(12, len(steps) + 4)},
    }


def _fillers(domain, entity, rng, count):
    """Shared prefix reads, identical on both sides, no typed values."""
    steps = []
    pool = domain["fillers"]
    for i in range(count):
        name, inp, out = pool[i % len(pool)]
        suffix = "" if i < len(pool) else f" (continued {i})"
        steps.append(_step(0, "tool_call", name,
                           inp.format(e=entity) + suffix,
                           out.format(e=entity) + suffix,
                           effect="read", error=False))
    return steps


def _tools_for(domain):
    tools = []
    seen = set()
    for entry in ([domain["read_tool"], domain["stale_read"],
                   domain["write_tool"]] + domain["fillers"]):
        if entry is None:
            continue
        name = entry[0]
        if name in seen or name in ("web_search",):
            continue
        seen.add(name)
        effect = "write" if entry is domain["write_tool"] else "read"
        tools.append({"name": name, "effect": effect})
    return tools


def _passing_steps(domain, entity, true_value, rng, fillers):
    plan = rng.choice(PLAN_POOL)
    name, inp, out = domain["read_tool"]
    steps = [_step(0, "plan", "plan", plan, "")]
    steps += _fillers(domain, entity, rng, fillers)
    steps.append(_step(0, "tool_call", name, inp.format(e=entity),
                       out.format(e=entity, v=true_value),
                       effect="read", error=False))
    if domain["write_tool"]:
        wname, winp, wout = domain["write_tool"]
        steps.append(_step(0, "tool_call", wname, winp.format(e=entity),
                           wout.format(e=entity), effect="write",
                           error=False))
    steps.append(_step(0, "reason", "reason",
                       rng.choice(REASON_POOL).format(v=true_value or
                                                      "the registered value"),
                       ""))
    steps.append(_answer(domain["expected"].format(e=entity, v=true_value)))
    return steps


def _scenario(family, index, rng):
    """One generated scenario: (manifest entry, fail trajectory, pass
    trajectory).  Ground truth is derived from construction, never from
    running the diagnoser."""
    domain_name = rng.choice(sorted(DOMAINS))
    domain = DOMAINS[domain_name]
    entity = domain["entity"](rng)
    true_value, wrong_value = _values(domain, rng)
    fillers = rng.randrange(0, 4)
    sid = f"g{index:04d}_{family}_{domain_name}"
    task = {"id": sid,
            "prompt": domain["task"].format(e=entity),
            "expected": domain["expected"].format(e=entity, v=true_value)}
    tools = _tools_for(domain)
    passing = _passing_steps(domain, entity, true_value, rng, fillers)
    inj = 1 + fillers  # index of the key read in the passing skeleton
    answer_of = lambda steps: len(steps) - 1
    entry = {"id": sid, "cause": family, "fail": f"{sid}__fail.json",
             "pass": f"{sid}__pass.json", "secondary": [],
             "params": {"domain": domain_name, "fillers": fillers}}
    fail_term = "agent_stop"

    if family == "grader_mislabel":
        fail = [json.loads(json.dumps(s)) for s in passing]
        fail[-1] = _answer(task["expected"] + " Confirmed against the "
                           "primary source.")
        fail[-2]["input"] = "The source confirms it; stating the result."
        entry.update(acceptable=["grader_or_label"], decisive_steps=[],
                     chain=[],
                     note="failing answer contains the expected answer "
                          "verbatim; process clean")

    elif family == "harness_kill":
        cut = 1 + fillers
        fail = [json.loads(json.dumps(s)) for s in passing[:cut + 1]]
        fail.append(_answer("The session was cut off by the platform "
                            "before the task could finish."))
        fail_term = rng.choice(["infrastructure_error", "unexpected_error"])
        entry.update(acceptable=["harness_termination"], decisive_steps=[],
                     chain=[], note="harness-declared termination mid-run")

    elif family == "environment_fault":
        fail = [json.loads(json.dumps(s)) for s in passing[:inj]]
        name, inp, _ = domain["read_tool"]
        fail.append(_step(0, "tool_call", name, inp.format(e=entity),
                          f"Error: upstream service returned 503 for "
                          f"{entity}.", effect="read", error=True))
        fail.append(_step(0, "reason", "reason",
                          "The service is down; the value cannot be read.",
                          ""))
        fail.append(_answer(f"I could not complete the task for {entity}: "
                            f"the upstream service is unavailable."))
        entry.update(acceptable=["environment_error"], decisive_steps=[inj],
                     chain=[inj, inj + 1, inj + 2],
                     note="declared 503 on the key read; abandoned")

    elif family in ("wrong_fact", "cascade"):
        fail = [json.loads(json.dumps(s)) for s in passing[:inj]]
        sname, sinp, sout = domain["stale_read"]
        fail.append(_step(0, "tool_call", sname, sinp.format(e=entity),
                          sout.format(e=entity, w=wrong_value),
                          effect="read", error=False, quality="weak",
                          note="stale source; the wrong value enters here"))
        if domain["write_tool"]:
            wname, winp, wout = domain["write_tool"]
            fail.append(_step(0, "tool_call", wname, winp.format(e=entity),
                              wout.format(e=entity), effect="write",
                              error=False))
        fail.append(_step(0, "reason", "reason",
                          rng.choice(REASON_POOL).format(v=wrong_value),
                          ""))
        fail.append(_answer(domain["wrong_answer"].format(e=entity,
                                                          w=wrong_value)))
        reason_idx = answer_of(fail) - 1
        entry.update(acceptable=["wrong_fact_propagation", "divergence"],
                     decisive_steps=[inj],
                     chain=[inj, reason_idx, answer_of(fail)],
                     note=f"{wrong_value} enters at step {inj} from a "
                          f"stale source and reaches the answer"
                          + ("; fail replays the passing prefix exactly"
                             if family == "cascade" else ""))

    elif family == "blind_write" and domain["write_tool"]:
        wname, winp, wout = domain["write_tool"]
        fail = [_step(0, "plan", "plan", rng.choice(PLAN_HASTY_POOL), "",
                      quality="weak",
                      note="commits to acting before reading anything"),
                _step(0, "tool_call", wname, winp.format(e=entity),
                      wout.format(e=entity) + " (existing value re-saved)",
                      effect="write", error=False, quality="bad",
                      note="write before any read"),
                _step(0, "tool_call", domain["read_tool"][0],
                      domain["read_tool"][1].format(e=entity),
                      domain["read_tool"][2].format(e=entity, v=true_value),
                      effect="read", error=False),
                _step(0, "reason", "reason",
                      "The change already went through; assuming it took "
                      "the right value.", "", quality="weak"),
                _answer(f"Task for {entity} completed as requested; the "
                        f"details were not re-verified.")]
        entry.update(
            acceptable=["process_pathology:blind_write", "divergence"],
            decisive_steps=[0, 1],
            chain=[0, 1, 3, 4],
            note="write-effect call before any read; the hasty plan and "
                 "the blind write both flip on correction")

    elif family == "blind_write":
        # domain has no write tool: fall back to divergence_only shape
        family = "divergence_only"
        entry["cause"] = family

    if family == "divergence_only":
        fail = [json.loads(json.dumps(s)) for s in passing[:inj]]
        sname, sinp, sout = domain["stale_read"]
        fail.append(_step(0, "tool_call", sname, sinp.format(e=entity),
                          "Secondary source: an unofficial summary of the "
                          "record, undated.",
                          effect="read", error=False, quality="weak",
                          note="chose the unofficial source"))
        fail.append(_step(0, "reason", "reason",
                          "Going with the unofficial summary's account.",
                          ""))
        fail.append(_answer(domain["wrong_answer"].format(
            e=entity, w="the summarised value")))
        entry.update(acceptable=["divergence"], decisive_steps=[inj],
                     chain=[inj, inj + 1, inj + 2],
                     note="source-selection divergence; no typed claim, "
                          "no error, no pathology")

    elif family == "late_symptom":
        fail = [json.loads(json.dumps(s)) for s in passing[:inj]]
        sname, sinp, sout = domain["stale_read"]
        fail.append(_step(0, "tool_call", sname, sinp.format(e=entity),
                          sout.format(e=entity, w=wrong_value or
                                      "a stale identifier"),
                          effect="read", error=False, quality="weak",
                          note="quiet stale read — the real cause"))
        name, inp, _ = domain["read_tool"]
        fail.append(_step(0, "tool_call", name,
                          inp.format(e=entity) + " (per stale reference)",
                          f"Error: no matching record for the stale "
                          f"reference on {entity}.",
                          effect="read", error=True,
                          note="loud error CAUSED by the stale read"))
        fail.append(_step(0, "tool_call", name, inp.format(e=entity)
                          + " (retry, adjusted)",
                          "Partial record only; proceeding with what is "
                          "at hand.", effect="read", error=False))
        fail.append(_step(0, "reason", "reason",
                          rng.choice(REASON_POOL).format(
                              v=wrong_value or "the partial value"), ""))
        fail.append(_answer(domain["wrong_answer"].format(
            e=entity, w=wrong_value or "an incomplete value")))
        entry.update(
            acceptable=["wrong_fact_propagation", "divergence"],
            decisive_steps=[inj],
            chain=[inj, inj + 1, inj + 2, inj + 3, answer_of(fail)],
            note="quiet stale read causes a loud recovered error later; "
                 "blaming the error is the trap")

    elif family == "distractor":
        fail = [json.loads(json.dumps(s)) for s in passing[:inj]]
        sname, sinp, sout = domain["stale_read"]
        fail.append(_step(0, "tool_call", sname, sinp.format(e=entity),
                          sout.format(e=entity, w=wrong_value or
                                      "an unofficial account"),
                          effect="read", error=False, quality="weak",
                          note="the real cause"))
        # non-causal distractor: an identical repeated filler call
        dname, dinp, dout = domain["fillers"][0]
        for _ in range(2):
            fail.append(_step(0, "tool_call", dname, dinp.format(e=entity),
                              dout.format(e=entity), effect="read",
                              error=False,
                              note="identical repeated call; adds nothing"))
        fail.append(_step(0, "reason", "reason",
                          rng.choice(REASON_POOL).format(
                              v=wrong_value or "the unofficial value"), ""))
        fail.append(_answer(domain["wrong_answer"].format(
            e=entity, w=wrong_value or "the unofficial value")))
        reason_idx = answer_of(fail) - 1
        entry.update(
            acceptable=(["wrong_fact_propagation", "divergence"]
                        if wrong_value else ["divergence"]),
            decisive_steps=[inj],
            chain=[inj, reason_idx, answer_of(fail)],
            note="repeated identical call sits beside the real cause and "
                 "must not lead or join the chain")

    elif family == "multi_cause" and domain["write_tool"]:
        wname, winp, wout = domain["write_tool"]
        sname, sinp, sout = domain["stale_read"]
        fail = [_step(0, "plan", "plan", rng.choice(PLAN_HASTY_POOL), "",
                      quality="weak"),
                _step(0, "tool_call", wname, winp.format(e=entity),
                      wout.format(e=entity), effect="write", error=False,
                      quality="bad", note="blind write — the secondary"),
                _step(0, "tool_call", sname, sinp.format(e=entity),
                      sout.format(e=entity, w=wrong_value or
                                  "a stale account"),
                      effect="read", error=False,
                      note="stale read — the primary"),
                _step(0, "reason", "reason",
                      rng.choice(REASON_POOL).format(
                          v=wrong_value or "the stale value"), ""),
                _answer(domain["wrong_answer"].format(
                    e=entity, w=wrong_value or "the stale value"))]
        entry.update(
            acceptable=(["wrong_fact_propagation", "divergence"]
                        if wrong_value else ["divergence"]),
            decisive_steps=[0, 2],
            secondary=["process_pathology:blind_write"],
            chain=[0, 2, 3, 4],
            note="primary: stale read enacting a hasty plan; secondary: "
                 "the blind write, which must stay visible")

    elif family == "multi_cause":
        # no write tool: environment primary + repeated failing call
        name, inp, _ = domain["read_tool"]
        fail = [json.loads(json.dumps(s)) for s in passing[:inj]]
        for repeat in range(2):
            fail.append(_step(0, "tool_call", name, inp.format(e=entity),
                              f"Error: upstream service returned 503 for "
                              f"{entity}.", effect="read", error=True,
                              note="the same failing call, unchanged"
                              if repeat else "the environment fault"))
        fail.append(_step(0, "reason", "reason",
                          "The service is down; nothing more to try.", ""))
        fail.append(_answer(f"I could not complete the task for {entity}: "
                            f"the upstream service is unavailable."))
        entry.update(
            acceptable=["environment_error"],
            decisive_steps=[inj],
            secondary=["process_pathology:repeated_calls",
                       "process_pathology:swallowed_error"],
            chain=[inj, inj + 1, inj + 2, inj + 3],
            note="primary: the 503, abandoned; secondary: the identical "
                 "failing call repeated unchanged")

    fail_traj = _trajectory(task, FAIL_AGENT[0], FAIL_AGENT[1], fail,
                            success=False, termination=fail_term,
                            tools=tools)
    pass_traj = _trajectory(task, PASS_AGENT[0], PASS_AGENT[1], passing,
                            success=True, termination="agent_stop",
                            tools=tools)
    return entry, fail_traj, pass_traj


def generate(out_dir: Path, pairs: int) -> dict:
    rng = random.Random(SEED)
    _MONEY_USED.clear()
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.json"):
        stale.unlink()
    manifest = {"version": 3, "generated": True, "pairs": pairs,
                "seed": SEED, "scenarios": []}
    for i in range(pairs):
        family = FAMILIES[i % len(FAMILIES)]
        entry, fail_traj, pass_traj = _scenario(family, i, rng)
        (out_dir / entry["fail"]).write_text(
            json.dumps(fail_traj) + "\n", encoding="utf-8")
        (out_dir / entry["pass"]).write_text(
            json.dumps(pass_traj) + "\n", encoding="utf-8")
        manifest["scenarios"].append(entry)
    (out_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="generate the procedural diagnosis benchmark corpus")
    parser.add_argument("--pairs", type=int, default=2000,
                        help="scenario pairs to generate (default 2000)")
    parser.add_argument("-o", "--output", default=None,
                        help="output directory (default: "
                             "demo/diagnosis_bench/traces_scale)")
    args = parser.parse_args()
    out_dir = (Path(args.output) if args.output else
               Path(__file__).resolve().parent / "traces_scale")
    manifest = generate(out_dir, args.pairs)
    print(f"wrote {len(manifest['scenarios'])} scenario pair(s) to "
          f"{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
