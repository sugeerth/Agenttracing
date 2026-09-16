"""The harness a self-evolving agent runs inside, and whether it moved.

`evolve` reads a lineage's ``artifacts`` — ``system_prompt``, ``rules``,
``skills``, ``tools``, ``memory``, ``config`` — and reads every change as
"the agent evolved". Two things are missing from that, and this section is
both of them.

**`tools` and `config` are scaffold, not reasoning.** A generation that
raises ``max_search_retries`` from 2 to 5, or adds a tool that does what it
used to reason about, changed the *harness it runs inside* rather than how
it thinks. Those are different claims — a reasoning change is the agent
learning something, a scaffold change is the agent buying something — and
they transfer differently. :data:`KINDS` is the classification, and it
ships in the output so a reader can check the rule rather than trust it.

**The harness that actually ran each generation is invisible.** Nothing in
the lineage records which model served the steps, at what temperature,
under which step budget, with which tools actually offered. Generations are
recorded days apart. If any of that moved between two of them, the delta
attributed to the prompt diff is confounded, and no other reading in this
repository can say so. :func:`fingerprint` reads what the traces
themselves recorded — never what the manifest claims — and
:func:`attribution` refuses the word *attributable* unless a fingerprint
exists on both sides and did not move. Where none is recorded the status is
``assumed`` and the sentence names the assumption, because an unrecorded
harness is not a constant one.

**And the failure both of those hide.** An agent can "improve" by moving
work into its scaffold: retries that retry the failures away, a step budget
that buys brute force, a tool that absorbs the reasoning. Outcome-only
grading scores that as progress. :func:`absorption` measures it — the pass
rate rising while the work each pass costs rises with it — which is the
harness analogue of gaming and the thing the co-evolving eval had no probe
for.

Pure stdlib, no network, no clock: a lineage in, a section out.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from . import sections
from ._stats import finite, mean, percentile_interval, rng, rounded
from ._text import join_names, num, plural, signed
from .evolve import TOOLISH
from .section import measurable, unmeasurable
from .rlstats import BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED

VERSION = 1

#: What each artifact kind is.  ``reasoning`` is how the agent thinks —
#: what it was told, what it remembers, what it knows how to do.
#: ``scaffold`` is what it runs inside — the tools it may call and the
#: settings the loop around it obeys.  The split is the point of this
#: section, so it is data and it is published, not a rule buried in a
#: function.
KINDS: dict = {
    "system_prompt": "reasoning",
    "rules": "reasoning",
    "skills": "reasoning",
    "memory": "reasoning",
    "tools": "scaffold",
    "config": "scaffold",
}

#: A step needs at least this many passing episodes on each side before
#: the work-per-pass comparison is made; under it the ratio is one or two
#: episodes' noise and no flag is worth raising.
MIN_PASSES = 3

#: How much the work per pass must rise, as a share of the lower side,
#: before the scaffold is said to be carrying the agent.  Anything smaller
#: is within the run-to-run variation these corpora show.
ABSORB_MARGIN = 0.10

GAP = ("a fingerprint is read from what the traces recorded, so a harness change no step wrote down is invisible "
       "here as it is everywhere else; and absorption is measured over the episodes the lineage kept, which a "
       "grader that was fooled fools too")


# ------------------------------------------------------------- fingerprint

def _step_models(traj) -> dict:
    """Every distinct model that served a step of this episode, from the
    step's own telemetry, with how many steps it served and the decoding
    parameters recorded beside it."""
    out: dict = {}
    for st in traj.steps:
        tele = st.model if isinstance(st.model, dict) else None
        if not tele:
            continue
        name = tele.get("name") or tele.get("model")
        if not isinstance(name, str) or not name:
            continue
        row = out.setdefault(name, {"name": name, "steps": 0, "temperature": None, "top_p": None})
        row["steps"] += 1
        for key in ("temperature", "top_p"):
            v = tele.get(key)
            if finite(v) and row[key] is None:
                row[key] = rounded(float(v), 4)
    return out


def fingerprint(trajectories) -> dict:
    """What actually ran one generation's episodes, read from the traces.

    Never from the manifest: a lineage's ``agent.json`` says what the
    generation *is*, and this says what *ran* it.  Six readings, each
    ``None`` when no episode recorded it —

    ``models``
        the declared model of each episode, and every model the step
        telemetry names, with the share of steps it served and the
        decoding parameters recorded with it.
    ``tools_offered``
        the tool names the runs were offered (``Trajectory.tools``), which
        is not the same as the tools the agent declared or called.
    ``caps``
        the limits the harness enforced (``Trajectory.budget``), the thing
        that tells an agent which finished from one that ran out of room.
    ``token_basis``
        how the token counts were obtained, which is a property of the
        harness and not of the agent.
    ``schema_version``
        the trace contract these episodes were written against.
    ``digest``
        SHA-256 over the canonical form of all of it, so two generations
        compare by one value.

    ``measurable: False`` with the reason when the episodes record none of
    it, which is the common case today and has to be said rather than
    quietly read as "the harness held constant".
    """
    trajectories = list(trajectories or [])
    if not trajectories:
        return unmeasurable("the generation carries no episode to read a harness from", version=VERSION,
                            models=[], declared_models=[], tools_offered=[], caps={}, token_basis=[],
                            schema_versions=[], digest=None, episodes=0, recorded=[])
    declared = sorted({(t.agent.model or "").strip() for t in trajectories if (t.agent.model or "").strip()})
    versions = sorted({(t.agent.version or "").strip() for t in trajectories if (t.agent.version or "").strip()})
    models: dict = {}
    total_steps = 0
    for traj in trajectories:
        total_steps += len(traj.steps)
        for name, row in _step_models(traj).items():
            got = models.setdefault(name, {"name": name, "steps": 0, "temperature": None, "top_p": None})
            got["steps"] += row["steps"]
            for key in ("temperature", "top_p"):
                if got[key] is None:
                    got[key] = row[key]
    for row in models.values():
        row["share"] = rounded(row["steps"] / total_steps, 4) if total_steps else None
    tools_offered = sorted({str(t.get("name")) for traj in trajectories for t in (traj.tools or [])
                            if isinstance(t, dict) and t.get("name")})
    caps: dict = {}
    for traj in trajectories:
        for key, value in (traj.budget or {}).items():
            if not isinstance(key, str):
                continue
            # a limit is a number, but a loop's switches and named settings
            # are settings too, and the whole point of this reading is that a
            # knob the actuator turns is a knob the digest sees. Dropping the
            # non-numeric ones would make two of the loop's own settings
            # invisible to the reading that judges the change they made.
            if isinstance(value, bool) or isinstance(value, str):
                caps.setdefault(key, set()).add(value)
            elif finite(value):
                caps.setdefault(key, set()).add(rounded(float(value), 4))
    caps = {k: (v.pop() if len(v) == 1 else sorted(v, key=lambda x: (str(type(x)), str(x))))
            for k, v in sorted(caps.items())}
    basis = sorted({str((traj.token_accounting or {}).get("basis")) for traj in trajectories
                    if (traj.token_accounting or {}).get("basis")})
    schemas = sorted({int(traj.schema_version) for traj in trajectories if finite(traj.schema_version)})

    recorded = [name for name, got in (("the declared model", declared), ("the model telemetry", models),
                                       ("the tools offered", tools_offered), ("the harness caps", caps),
                                       ("the token basis", basis)) if got]
    #: The digest covers the harness *proper* and deliberately leaves the
    #: model and agent *names* out of it.  A lineage that versions its model
    #: string per generation — `agent@g0`, `agent@g1` — would otherwise have
    #: every step marked confounded by the rename alone, which is true of the
    #: string and useless as a finding.  The names travel in `identity`, and
    #: the sentence about them says the thing a trace cannot resolve: a
    #: renamed model and a changed model look exactly alike from here.
    decoding = [{"name": models[k]["name"], "temperature": models[k]["temperature"], "top_p": models[k]["top_p"]}
                for k in sorted(models)]
    body = {"tools_offered": tools_offered, "caps": caps, "token_basis": basis, "schema_versions": schemas,
            "decoding": decoding}
    identity = {"declared_models": declared, "declared_versions": versions,
                "serving_models": sorted(models)}
    if not recorded:
        return unmeasurable("no episode of this generation records a model, a tool table, a harness cap or a token "
                            "basis, so what ran it is not in the traces", version=VERSION,
                            digest=None, identity_digest=None, episodes=len(trajectories), recorded=[],
                            identity=identity, models=[], **body)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    identity_digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return measurable(version=VERSION, digest=digest, identity_digest=identity_digest,
                      episodes=len(trajectories), recorded=recorded, identity=identity,
                      models=[models[k] for k in sorted(models)], **body)


def _cap_value(v) -> str:
    """One cap as a reader would say it.

    A limit is a number, but a loop also has switches and settings that
    name a tool, and those are caps too (`deepcompare.scaffold`).  A
    boolean rendered through the number formatter would come out as
    Python's own ``True``, which is not a sentence anyone writes.
    """
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, str):
        return v
    return num(v)


def _cap_text(caps: dict) -> str:
    return ", ".join(
        f"{k} " + (join_names([_cap_value(x) for x in v]) if isinstance(v, list) else _cap_value(v))
        for k, v in sorted(caps.items())) or "none recorded"


def harness_moved(a: dict, b: dict) -> dict:
    """What moved between two generations' fingerprints.

    Two answers, kept apart on purpose.

    ``moved`` is the **harness proper**: the decoding parameters, the tools
    offered, the caps the loop enforced, how tokens were counted, the trace
    contract.  A change there is a change to the thing that ran the agent.

    ``identity`` is the **names**: the declared model and version, and the
    model strings the step telemetry carries.  These are reported and never
    enter ``moved``, because a lineage that versions its model string per
    generation — ``agent@g0``, ``agent@g1`` — would otherwise have every one
    of its steps marked confounded by a rename, which is true of the string
    and worthless as a finding.  And the honest part: from a trace, a
    renamed model and a genuinely different model look exactly alike.  That
    is why an identity change makes a step ``assumed`` rather than
    ``confounded`` or ``attributable`` — it is a question the record cannot
    settle, and the reading says so instead of picking an answer.

    ``moved`` is ``None`` — not ``False`` — when either side is
    unmeasurable: an unread harness is not an unchanged one.
    """
    if not (a or {}).get("measurable") or not (b or {}).get("measurable"):
        which = [name for name, side in (("from", a), ("to", b)) if not (side or {}).get("measurable")]
        missing = a if "from" in which else b
        return {"moved": None, "changes": [],
                "reason": f"no harness fingerprint on the {join_names(which)} side"
                          f" ({(missing or {}).get('reason') or 'unreadable'})",
                "identity": {"moved": None, "changes": [], "note": None},
                "digest_from": (a or {}).get("digest"), "digest_to": (b or {}).get("digest")}
    changes = []
    if a["digest"] != b["digest"]:
        for label, key, render in (
            ("the tools offered", "tools_offered", lambda v: join_names(v) or "none"),
            ("the token basis", "token_basis", lambda v: join_names(v) or "none"),
            ("the trace schema", "schema_versions", lambda v: join_names([str(x) for x in v]) or "none"),
        ):
            if a.get(key) != b.get(key):
                changes.append({"what": label, "from": render(a.get(key) or []), "to": render(b.get(key) or [])})
        if a.get("caps") != b.get("caps"):
            changes.append({"what": "the harness caps", "from": _cap_text(a.get("caps") or {}),
                            "to": _cap_text(b.get("caps") or {})})
        frm = {row["name"]: row for row in a.get("decoding") or []}
        to = {row["name"]: row for row in b.get("decoding") or []}
        for name in sorted(set(frm) & set(to)):
            for key in ("temperature", "top_p"):
                if frm[name].get(key) != to[name].get(key):
                    changes.append({"what": f"{name} {key}", "from": num(frm[name].get(key)),
                                    "to": num(to[name].get(key))})
    ident_a, ident_b = a.get("identity") or {}, b.get("identity") or {}
    ident_changes = []
    for label, key in (("the declared model", "declared_models"), ("the agent version", "declared_versions"),
                       ("the models that served the steps", "serving_models")):
        if ident_a.get(key) != ident_b.get(key):
            ident_changes.append({"what": label, "from": join_names(ident_a.get(key) or []) or "none",
                                  "to": join_names(ident_b.get(key) or []) or "none"})
    identity = {"moved": bool(ident_changes), "changes": ident_changes,
                "note": ("the model string changed; a lineage that versions its model name per generation and one "
                         "whose model genuinely changed are indistinguishable from a trace, so this is not read as "
                         "a harness change and not read as no change either") if ident_changes else None}
    return {"moved": bool(changes), "changes": changes, "reason": None, "identity": identity,
            "digest_from": a["digest"], "digest_to": b["digest"]}


def reconcile(move: dict, diff: dict) -> dict:
    """Separate a harness change the *agent* caused from one it did not.

    ``Trajectory.tools`` is the table the runner offered, and a
    self-evolving agent that edits its own ``tools`` artifact makes that
    table move.  The fingerprint cannot tell the two apart on its own — an
    operator adding a tool and the agent adding one look identical in the
    trace — but the artifact diff can: if the tool table moved by exactly
    what the agent's own diff added and removed, the agent did it, and
    counting it as a harness confound would blame the environment for the
    agent's work.

    Anything the diff does not account for stays a harness change.  The
    accounted-for entries move to ``explained``, so nothing is dropped and
    a reader can see the reasoning.
    """
    move = dict(move or {})
    changes = list(move.get("changes") or [])
    if not changes or not isinstance(diff, dict) or not diff.get("measurable", True):
        move.setdefault("explained", [])
        return move
    tools = diff.get("tools") if isinstance(diff.get("tools"), dict) else {}
    added = {str(x) for x in (tools.get("added") or [])}
    removed = {str(x) for x in (tools.get("removed") or [])}
    kept, explained = [], []
    for change in changes:
        if change.get("what") == "the tools offered" and (added or removed):
            frm = {s.strip() for s in str(change.get("from") or "").replace(" and ", ", ").split(",") if s.strip()}
            to = {s.strip() for s in str(change.get("to") or "").replace(" and ", ", ").split(",") if s.strip()}
            if (to - frm) == added and (frm - to) == removed:
                explained.append({**change, "by": "the agent's own tools artifact",
                                  "note": "the tool table moved by exactly what this step's artifact diff added "
                                          "and removed, so the agent changed its own scaffold; this is not the "
                                          "environment moving underneath it"})
                continue
        kept.append(change)
    move["changes"] = kept
    move["explained"] = explained
    move["moved"] = bool(kept) if move.get("moved") is not None else None
    return move


# ------------------------------------------------------- reasoning or scaffold

def _touched(diff: dict, kind: str) -> bool:
    """True when this step's diff actually changed that artifact kind."""
    block = (diff or {}).get(kind)
    if not isinstance(block, dict):
        return False
    if kind == "system_prompt":
        return bool(block.get("added") or block.get("removed"))
    if kind == "config":
        return bool(block.get("changed"))
    if kind == "skills":
        return bool(block.get("added") or block.get("removed") or block.get("changed"))
    return bool(block.get("added") or block.get("removed"))


def classify(diff: dict) -> dict:
    """Which kinds of artifact a step changed, split by :data:`KINDS`.

    ``kind`` is ``reasoning`` when only the agent's thinking moved,
    ``scaffold`` when only what it runs inside moved, ``mixed`` when both
    and ``none`` when neither — and ``unreadable`` when the diff itself is
    unmeasurable, which is not the same as a step that changed nothing.
    """
    if not isinstance(diff, dict) or not diff.get("measurable", True):
        return {"kind": "unreadable", "reasoning": [], "scaffold": [],
                "reason": (diff or {}).get("reason") or "the artifact diff is unreadable"}
    reasoning = sorted(k for k, side in KINDS.items() if side == "reasoning" and _touched(diff, k))
    scaffold = sorted(k for k, side in KINDS.items() if side == "scaffold" and _touched(diff, k))
    kind = ("mixed" if reasoning and scaffold else "reasoning" if reasoning
            else "scaffold" if scaffold else "none")
    return {"kind": kind, "reasoning": reasoning, "scaffold": scaffold, "reason": None}


def attribution(move: dict, kind: str) -> dict:
    """Whether this step's measured delta may be handed to the artifacts.

    Four statuses, and the one never reached without evidence is
    ``attributable``:

    ``attributable``
        a fingerprint on both sides, the harness identical, and the model
        string unchanged too.  The delta is the artifacts'.
    ``confounded``
        the harness itself moved — a different temperature, a different
        tool table, a different cap.  Two things changed between the
        measurements and the delta belongs to neither.
    ``assumed``
        either no fingerprint at all, or the harness held but the model
        *string* changed.  Both rest on an assumption the traces cannot
        check, and the note says which one.
    """
    moved = (move or {}).get("moved")
    changes = list((move or {}).get("changes") or [])
    identity = (move or {}).get("identity") or {}
    if moved is None:
        return {"status": "assumed", "basis": "no fingerprint",
                "reason": (move or {}).get("reason") or "no harness fingerprint",
                "note": "the delta below is read as the artifacts' own, which holds only if the harness that ran "
                        "the two generations was the same; the traces do not record enough to check that, so it "
                        "is an assumption and not a finding"}
    if moved:
        if kind == "none":
            return {"status": "confounded", "basis": "harness moved",
                    "reason": "the harness moved and the artifacts did not",
                    "note": "the artifacts are identical across this step, so any measured delta is the harness's "
                            "or noise — it is not something the agent did: "
                            + join_names([c["what"] for c in changes])}
        return {"status": "confounded", "basis": "harness moved",
                "reason": "the harness moved as well as the artifacts",
                "note": "two things changed between these measurements — " + join_names([c["what"] for c in changes])
                        + " — so the delta cannot be handed to the artifacts; re-run the two generations under one "
                          "harness to separate them"}
    if identity.get("moved"):
        return {"status": "assumed", "basis": "model string changed",
                "reason": "the harness held, but the model string changed: "
                          + join_names([c["what"] for c in identity.get("changes") or []]),
                "note": "reading the delta as the artifacts' assumes the rename was a rename. A lineage that "
                        "versions its model name per generation and one that swapped the model underneath look "
                        "the same from a trace, and nothing recorded here separates them — record a model "
                        "snapshot id per generation and this becomes a check instead of an assumption"}
    return {"status": "attributable", "basis": "harness and model string both identical",
            "reason": "the harness fingerprint and the model string are identical on both sides", "note": None}


# -------------------------------------------------------------- absorption

def _work(trajectories) -> dict:
    """Per-episode work numbers for one generation: how many episodes
    passed, and what each pass cost in steps, tool calls and repeats."""
    passes, steps, calls, retries = 0, [], [], []
    for traj in trajectories or []:
        if not traj.outcome.success:
            continue
        passes += 1
        tools = [st for st in traj.steps if st.type in TOOLISH]
        seen: set = set()
        repeat = 0
        for st in tools:
            key = (st.name or st.type or "?", json.dumps(st.input, sort_keys=True, default=str)[:500])
            if key in seen:
                repeat += 1
            seen.add(key)
        steps.append(float(len(traj.steps)))
        calls.append(float(len(tools)))
        retries.append(float(repeat))
    return {"passes": passes, "steps": steps, "calls": calls, "retries": retries}


def _interval(values, samples: int, label: str) -> dict:
    """The mean with a percentile bootstrap over the episodes recorded —
    an interval over these runs, never a population claim."""
    if not values:
        return {"point": None, "lo": None, "hi": None, "n": 0}
    point = mean(values)
    if samples < 1 or len(values) < 2:
        return {"point": rounded(point), "lo": None, "hi": None, "n": len(values)}
    draw = rng(BOOTSTRAP_SEED, label, section="harnessevo")
    means = []
    for _ in range(int(samples)):
        sample = [values[draw.randrange(len(values))] for _ in values]
        m = mean(sample)
        if m is not None:
            means.append(m)
    lo, hi = percentile_interval(means) if means else (None, None)
    return {"point": rounded(point), "lo": rounded(lo), "hi": rounded(hi), "n": len(values)}


def absorption(before, after, *, samples: int = BOOTSTRAP_SAMPLES, label: str = "step") -> dict:
    """Did the scaffold carry the agent across this step?

    The reading that outcome-only grading cannot make. An agent whose pass
    rate rises while **each pass costs more of its own work** did not get
    better at the task; something around it absorbed the difficulty — more
    steps allowed, more retries tolerated, a tool doing what reasoning used
    to. The flag fires on exactly that shape: the pass rate up, and the
    work per pass up by more than :data:`ABSORB_MARGIN`.

    It is deliberately not the same as `evolve`'s ``gamed`` flag, which
    asks whether the reward moved against the outcome. This asks whether
    the outcome moved at the agent's own expense, which a reward that only
    pays for outcomes will never show.

    ``measurable: False`` with the reason when either side has fewer than
    :data:`MIN_PASSES` passing episodes: a work-per-pass ratio over one or
    two successes is noise wearing a number's clothes.
    """
    a_eps, b_eps = list(before or []), list(after or [])
    a, b = _work(a_eps), _work(b_eps)
    rates = {"from": rounded(a["passes"] / len(a_eps), 4) if a_eps else None,
             "to": rounded(b["passes"] / len(b_eps), 4) if b_eps else None}
    rates["delta"] = rounded(rates["to"] - rates["from"], 4) if finite(rates["from"]) and finite(rates["to"]) else None
    empty = {"steps_per_pass": {"from": None, "to": None, "delta": None},
             "tool_calls_per_pass": {"from": None, "to": None, "delta": None},
             "retries_per_pass": {"from": None, "to": None, "delta": None}}
    if a["passes"] < MIN_PASSES or b["passes"] < MIN_PASSES:
        return unmeasurable(
            f"work per pass needs {MIN_PASSES} passing episodes a side and this step has "
            f"{a['passes']} then {b['passes']}", version=VERSION, flag=False,
            pass_rate=rates, reading=None, **empty)
    out: dict = {"pass_rate": rates}
    for key, field_ in (("steps_per_pass", "steps"), ("tool_calls_per_pass", "calls"),
                        ("retries_per_pass", "retries")):
        frm = _interval(a[field_], samples, f"{label}:{key}:from")
        to = _interval(b[field_], samples, f"{label}:{key}:to")
        out[key] = {"from": frm, "to": to,
                    "delta": rounded(to["point"] - frm["point"], 4)
                    if finite(frm["point"]) and finite(to["point"]) else None}
    work = out["steps_per_pass"]
    rose = finite(rates["delta"]) and rates["delta"] > 0
    base = work["from"]["point"] if finite(work["from"]["point"]) else None
    heavier = (finite(work["delta"]) and base not in (None, 0)
               and work["delta"] / base > ABSORB_MARGIN)
    flag = bool(rose and heavier)
    if flag:
        reading = (f"The pass rate rose {signed(rates['delta'] * 100, 1)} points while each pass cost "
                   f"{signed(work['delta'], 2)} more steps ({num(work['from']['point'], 2)} → "
                   f"{num(work['to']['point'], 2)}). The outcome improved and each success took more of the "
                   f"agent's own work, so the gain came from what was put around the agent rather than from the "
                   f"agent needing less. That is not a fault — restoring a verifier costs steps and buys "
                   f"correctness, and so does a tool that does a job well — but it is a different claim from "
                   f"\"the agent got better\", and it travels differently: the gain stays with the scaffold, so "
                   f"a harness that drops it drops the gain. Outcome-only grading cannot tell the two apart.")
    elif rose:
        reading = (f"The pass rate rose {signed(rates['delta'] * 100, 1)} points and each pass cost "
                   f"{signed(work['delta'], 2)} steps ({num(work['from']['point'], 2)} → "
                   f"{num(work['to']['point'], 2)}), so the gain is not the scaffold carrying more work.")
    else:
        reading = (f"The pass rate moved {signed((rates['delta'] or 0) * 100, 1)} points; absorption asks about a "
                   f"rise and there is none to explain.")
    return measurable(version=VERSION, flag=flag, reading=reading, **out)


# ----------------------------------------------------------------- section

def _step_reading(row: dict) -> str:
    kind, attr = row["kind"], row["attribution"]
    what = {"reasoning": "changed how the agent thinks", "scaffold": "changed what it runs inside",
            "mixed": "changed both how the agent thinks and what it runs inside",
            "none": "changed none of the agent's own parts",
            "unreadable": "has an artifact diff this section cannot read"}[kind]
    names = row["changed"]["reasoning"] + row["changed"]["scaffold"]
    bits = [f"{row['from']}→{row['to']} {what}" + (f" ({join_names(names)})" if names else "") + "."]
    if attr["status"] == "confounded":
        bits.append("The harness moved too, so the delta is attributable to neither: " + (attr["note"] or "") + ".")
    elif attr["status"] == "assumed" and attr.get("basis") == "model string changed":
        bits.append("The harness held, but the model string changed (" + attr["reason"].split(": ", 1)[-1] +
                    "), and a rename cannot be told from a real model change in a trace — so reading the delta as "
                    "the agent's is an assumption, not a finding.")
    elif attr["status"] == "assumed":
        bits.append("No harness fingerprint, so any delta read here assumes the harness held constant between "
                    "the two generations — an assumption, not a finding.")
    else:
        bits.append("The harness fingerprint and the model string are identical across the step, so the delta is "
                    "the artifacts'.")
    absorbed = row["absorption"]
    if absorbed.get("flag"):
        scaffold = row["changed"]["scaffold"]
        if scaffold:
            bits.append("The scaffold this step changed was " + join_names(scaffold) + ".")
        bits.append(absorbed["reading"])
    return " ".join(b for b in bits if b)


def harness_evolution(lineage: dict, evolution: dict, *, samples: int = BOOTSTRAP_SAMPLES) -> dict:
    """``aggregate["harness_evolution"]``: the harness beside the agent.

    Per generation the fingerprint of what ran it; per step whether the
    change was reasoning or scaffold, whether the harness moved underneath
    it, and whether the scaffold absorbed the work. ``measurable: False``
    with the reason when the lineage or the evolution cannot be read.
    """
    if not isinstance(lineage, dict) or not lineage.get("measurable"):
        return unmeasurable((lineage or {}).get("reason") or "the lineage cannot be read", version=VERSION,
                            kinds=KINDS, generations=[], steps=[], summary={}, gap=GAP)
    if not isinstance(evolution, dict) or not evolution.get("measurable"):
        return unmeasurable("the evolution section is unmeasurable: "
                            + str((evolution or {}).get("reason") or "absent"), version=VERSION,
                            kinds=KINDS, generations=[], steps=[], summary={}, gap=GAP)
    gens = list(lineage.get("generations") or [])
    by_id = {g["id"]: g for g in gens}
    prints: dict = {}
    rows = []
    for index, gen in enumerate(gens):
        fp = fingerprint(gen.get("trajectories"))
        prints[gen["id"]] = fp
        rows.append({"id": gen["id"], "index": index, "fingerprint": fp,
                     "episodes": len(gen.get("trajectories") or [])})
    steps = []
    for step in evolution.get("steps") or []:
        frm, to = step.get("from"), step.get("to")
        kinds = classify(step.get("diff") or {})
        move = reconcile(harness_moved(prints.get(frm) or {}, prints.get(to) or {}), step.get("diff") or {})
        absorbed = absorption((by_id.get(frm) or {}).get("trajectories"),
                              (by_id.get(to) or {}).get("trajectories"),
                              samples=samples, label=f"{frm}->{to}")
        row = {"index": step.get("index"), "from": frm, "to": to,
               "kind": kinds["kind"],
               "changed": {"reasoning": kinds["reasoning"], "scaffold": kinds["scaffold"]},
               "kind_reason": kinds["reason"],
               "harness": move,
               "attribution": attribution(move, kinds["kind"]),
               "absorption": absorbed,
               "base_verdict": step.get("verdict"), "base_flags": list(step.get("flags") or [])}
        row["reading"] = _step_reading(row)
        steps.append(row)

    counts = {k: sum(1 for s in steps if s["kind"] == k) for k in ("reasoning", "scaffold", "mixed", "none", "unreadable")}
    confounded = [s["from"] + "→" + s["to"] for s in steps if s["attribution"]["status"] == "confounded"]
    assumed = [s["from"] + "→" + s["to"] for s in steps if s["attribution"]["status"] == "assumed"]
    renamed = [s["from"] + "→" + s["to"] for s in steps
               if s["attribution"]["status"] == "assumed" and s["attribution"].get("basis") == "model string changed"]
    attributable = [s["from"] + "→" + s["to"] for s in steps if s["attribution"]["status"] == "attributable"]
    absorbed = [s["from"] + "→" + s["to"] for s in steps if (s["absorption"] or {}).get("flag")]
    fingerprinted = [r["id"] for r in rows if r["fingerprint"].get("measurable")]
    unprinted = len(fingerprinted) < len(rows)
    summary = {"steps": len(steps), "by_kind": counts,
               "generations_fingerprinted": len(fingerprinted), "generations": len(rows),
               "attributable": attributable, "confounded": confounded, "assumed": assumed,
               "assumed_on_rename": renamed, "absorbed": absorbed}
    parts = []
    if steps:
        shape = [f"{counts[k]} {k}" for k in ("reasoning", "scaffold", "mixed") if counts[k]]
        parts.append(f"Of {plural(len(steps), 'step')}, " + (join_names(shape) if shape else "none changed an artifact")
                     + " — scaffold meaning the tools and config the agent runs inside, reasoning meaning what it "
                       "was told and remembers.")
    if confounded:
        parts.append(f"{plural(len(confounded), 'step')} ran under a harness that moved ({join_names(confounded)}), "
                     f"so those deltas belong to neither the agent nor the scaffold.")
    if renamed:
        parts.append(f"On {plural(len(renamed), 'step')} the harness held but the model string changed "
                     f"({join_names(renamed)}); a per-generation rename and a real model swap are the same bytes "
                     f"in a trace, so those deltas are read as the agent's on an assumption. A model snapshot id "
                     f"recorded per generation would turn it into a check.")
    if unprinted:
        parts.append(f"{len(fingerprinted)} of {plural(len(rows), 'generation')} record enough for a harness "
                     f"fingerprint at all.")
    if not confounded and not renamed and not unprinted and steps:
        parts.append("Every generation carries a harness fingerprint, none of them moved and no model string "
                     "changed, so each step's delta is the artifacts' own.")
    if absorbed:
        parts.append(f"On {plural(len(absorbed), 'step')} the gain is the scaffold's rather than the reasoning's "
                     f"({join_names(absorbed)}): the pass rate rose while each pass cost the agent more work. "
                     f"Worth knowing because it does not transfer — the gain goes with the scaffold.")
    summary["reading"] = " ".join(parts)
    return measurable(version=VERSION, kinds=KINDS, generations=rows, steps=steps, summary=summary, gap=GAP)


# `after` names both of the other always-attached lineage sections, not
# because this one reads them but because the registry's soft order
# otherwise falls back to registration order — which is import order,
# and the key order of an aggregate may not depend on that.
@sections.register("lineage", "harness_evolution", requires=("evolution",),
                   after=("coevolution", "data_evolution"))
def _section(agg: dict, ctx: "sections.LineageContext") -> dict:
    extra = dict(getattr(ctx, "extra", None) or {})
    return harness_evolution(ctx.lineage or {}, agg.get("evolution"),
                             samples=extra.get("samples", BOOTSTRAP_SAMPLES))
