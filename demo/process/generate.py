#!/usr/bin/env python3
"""Generate traces that exercise process integrity (SCHEMA.md v22).

The flagship demo is a research task where both agents behave reasonably and
only their retrieval quality differs.  It is a poor showcase for process
analysis, because nothing in it loops, errors, or writes anything — running
``process`` over it correctly reports "both runs are clean", which
demonstrates the check works but not what it is for.

These runs are the other case: an action-taking domain where the interesting
failures are in *how* the work was done.  Each pair is built around one
finding, and in particular around the two verdicts that outcome-only
evaluation cannot produce:

* **passed but pathological** — the oracle is satisfied by a run that
  looped, ignored an error, or wrote before it looked.  A leaderboard scores
  this run identically to a clean one.
* **failed but clean** — the run did everything visible right and was still
  marked wrong.  That is evidence about the grader, not only the agent.

The traces declare what a real harness knows and a log usually omits: the
tool list with read/write effects and parameter schemas, the step budget,
the termination reason, and per-step ``error``/``effect`` flags.  Everything
here is deterministic — no randomness, no wall-clock.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "traces"

#: The declared tool surface. Without this a log cannot be checked for
#: unauthorised calls, blind writes, or argument validity — the analysis
#: reports those as unmeasurable rather than assuming they were fine.
TOOLS = [
    {"name": "search_flights", "effect": "read",
     "parameters": {"properties": {"origin": {"type": "string"},
                                   "destination": {"type": "string"},
                                   "date": {"type": "string"}},
                    "required": ["origin", "destination", "date"]}},
    {"name": "get_booking", "effect": "read",
     "parameters": {"properties": {"reference": {"type": "string"}},
                    "required": ["reference"]}},
    {"name": "get_policy", "effect": "read",
     "parameters": {"properties": {"topic": {"type": "string"}},
                    "required": ["topic"]}},
    {"name": "cancel_booking", "effect": "write",
     "parameters": {"properties": {"reference": {"type": "string"},
                                   "refund": {"type": "boolean"}},
                    "required": ["reference"]}},
    {"name": "book_flight", "effect": "write",
     "parameters": {"properties": {"flight": {"type": "string"},
                                   "seats": {"type": "integer"}},
                    "required": ["flight", "seats"]}},
]

BUDGET = {"max_steps": 10}


def step(index, type_, name, input_, output, **extra):
    record = {"index": index, "type": type_, "name": name, "input": input_,
              "output": output, "tokens": extra.pop("tokens", 40),
              "latency_s": extra.pop("latency_s", 1.2),
              "quality": extra.pop("quality", None),
              "note": extra.pop("note", None)}
    record.update(extra)
    return record


def trajectory(task, agent, model, steps, success, answer, termination,
               expected=None):
    return {
        "schema_version": 1,
        "trace_id": f"{task['id']}-{agent}",
        "agent": {"name": agent, "model": model, "version": agent.split("-")[-1]},
        "task": task,
        "outcome": {"success": success, "answer": answer,
                    "score": 1.0 if success else 0.0,
                    "termination": termination},
        "totals": {
            "input_tokens": sum(s["tokens"] for s in steps) * 3,
            "output_tokens": sum(s["tokens"] for s in steps),
            "cost_usd": round(sum(s["tokens"] for s in steps) * 0.000012, 6),
            "latency_s": round(sum(s["latency_s"] for s in steps), 2),
        },
        "steps": steps,
        "tools": TOOLS,
        "budget": BUDGET,
    }


# --------------------------------------------------------------------------
# p01 — the headline: a run that passes while looping, versus one that fails
#       with a spotless process.
# --------------------------------------------------------------------------
P01 = {"id": "p01_cancel_booking",
       "prompt": "Cancel booking QX7T2 and confirm the refund policy applies.",
       "expected": "Booking QX7T2 cancelled; refund issued under the 24-hour policy."}

P01_CLEAN = [
    step(0, "plan", "plan", "Read the booking, check the policy, then cancel.", ""),
    step(1, "tool_call", "get_booking", "get_booking(reference='QX7T2')",
         "QX7T2 — LHR->JFK, booked 3 hours ago, refundable.", effect="read", error=False),
    step(2, "tool_call", "get_policy", "get_policy(topic='refund')",
         "Bookings cancelled within 24 hours are refunded in full.",
         effect="read", error=False),
    step(3, "tool_call", "cancel_booking",
         "cancel_booking(reference='QX7T2', refund=true)",
         "Cancelled. Refund of $412.00 issued.", effect="write", error=False),
    step(4, "answer", "final",
         "Booking QX7T2 is cancelled and a full refund of $412.00 was issued "
         "under the 24-hour policy.",
         "Booking QX7T2 is cancelled and a full refund of $412.00 was issued "
         "under the 24-hour policy."),
]

# Passes the oracle — the booking really is cancelled — but hammers a failing
# call four times and never reads the policy it cites.
P01_LOOPING = [
    step(0, "plan", "plan", "Cancel it, then confirm.", ""),
    step(1, "tool_call", "get_booking", "get_booking(reference='QX7T2 ')",
         "Error: no booking found for reference 'QX7T2 '.",
         effect="read", error=True, quality="bad",
         note="trailing space in the reference"),
    step(2, "tool_call", "get_booking", "get_booking(reference='QX7T2 ')",
         "Error: no booking found for reference 'QX7T2 '.",
         effect="read", error=True, quality="bad",
         note="identical retry — no adaptation"),
    step(3, "tool_call", "get_booking", "get_booking(reference='QX7T2 ')",
         "Error: no booking found for reference 'QX7T2 '.",
         effect="read", error=True, quality="bad"),
    step(4, "reason", "reason", "The lookup keeps failing. I will cancel directly.",
         "", quality="weak"),
    step(5, "tool_call", "cancel_booking",
         "cancel_booking(reference='QX7T2', refund=true)",
         "Cancelled. Refund of $412.00 issued.", effect="write", error=False,
         note="wrote without ever successfully reading the booking"),
    step(6, "answer", "final",
         "Done — booking QX7T2 has been cancelled and refunded under the "
         "24-hour policy.",
         "Done — booking QX7T2 has been cancelled and refunded under the "
         "24-hour policy."),
]

# --------------------------------------------------------------------------
# p02 — false success: confident closing language, nothing written.
# --------------------------------------------------------------------------
P02 = {"id": "p02_book_flight",
       "prompt": "Book flight BA117 from LHR to JFK on 2026-09-01 for 2 passengers.",
       "expected": "BA117 booked for 2 passengers."}

P02_REAL = [
    step(0, "tool_call", "search_flights",
         "search_flights(origin='LHR', destination='JFK', date='2026-09-01')",
         "BA117 LHR->JFK 09:35, 2 seats available.", effect="read", error=False),
    step(1, "tool_call", "book_flight", "book_flight(flight='BA117', seats=2)",
         "Booked. Reference LM94K.", effect="write", error=False),
    step(2, "answer", "final", "Booked BA117 for 2 passengers, reference LM94K.",
         "Booked BA117 for 2 passengers, reference LM94K."),
]

P02_CLAIMED = [
    step(0, "tool_call", "search_flights",
         "search_flights(origin='LHR', destination='JFK', date='2026-09-01')",
         "BA117 LHR->JFK 09:35, 2 seats available.", effect="read", error=False),
    step(1, "reason", "reason",
         "BA117 has availability, so the booking will go through.", "",
         quality="bad", note="assumed the write instead of making it"),
    step(2, "answer", "final",
         "Task completed successfully — BA117 has been booked for 2 passengers.",
         "Task completed successfully — BA117 has been booked for 2 passengers."),
]

# --------------------------------------------------------------------------
# p03 — invalid arguments against a declared schema, and a call to a tool
#       the agent was never offered.
# --------------------------------------------------------------------------
P03 = {"id": "p03_change_seats",
       "prompt": "Change booking QX7T2 to 3 seats on flight BA117.",
       "expected": "BA117 rebooked with 3 seats."}

P03_VALID = [
    step(0, "tool_call", "get_booking", "get_booking(reference='QX7T2')",
         "QX7T2 — BA117, 2 seats.", effect="read", error=False),
    step(1, "tool_call", "book_flight", "book_flight(flight='BA117', seats=3)",
         "Booked. Reference PP31Z, 3 seats.", effect="write", error=False),
    step(2, "answer", "final", "Rebooked BA117 with 3 seats, reference PP31Z.",
         "Rebooked BA117 with 3 seats, reference PP31Z."),
]

P03_INVALID = [
    step(0, "tool_call", "get_booking", "get_booking(reference='QX7T2')",
         "QX7T2 — BA117, 2 seats.", effect="read", error=False),
    step(1, "tool_call", "book_flight",
         "book_flight(flight='BA117', seats='three', cabin='economy')",
         "Error: 'seats' must be an integer; unknown parameter 'cabin'.",
         effect="write", error=True, quality="bad",
         note="string where an integer is declared, plus an argument the "
              "schema does not have"),
    step(2, "tool_call", "modify_seats",
         "modify_seats(reference='QX7T2', seats=3)",
         "Error: unknown tool 'modify_seats'.", error=True, quality="bad",
         note="tool was never offered"),
    step(3, "answer", "final", "I was unable to change the seat count.",
         "I was unable to change the seat count."),
]

# --------------------------------------------------------------------------
# p04 — the budget case: one run finishes comfortably, the other only just.
# --------------------------------------------------------------------------
P04 = {"id": "p04_policy_lookup",
       "prompt": "What is the baggage allowance for a basic economy fare?",
       "expected": "One personal item; no cabin bag."}

P04_DIRECT = [
    step(0, "tool_call", "get_policy", "get_policy(topic='baggage')",
         "Basic economy: one personal item. No cabin bag.",
         effect="read", error=False),
    step(1, "answer", "final",
         "Basic economy includes one personal item and no cabin bag.",
         "Basic economy includes one personal item and no cabin bag."),
]

P04_SCENIC = [
    step(0, "tool_call", "get_policy", "get_policy(topic='fares')",
         "Fare families: basic economy, economy, premium.",
         effect="read", error=False),
    step(1, "tool_call", "get_policy", "get_policy(topic='cabin')",
         "Cabin bag rules vary by fare family.", effect="read", error=False),
    step(2, "tool_call", "get_policy", "get_policy(topic='allowances')",
         "See baggage policy.", effect="read", error=False),
    step(3, "tool_call", "get_policy", "get_policy(topic='checked')",
         "Checked baggage is charged separately.", effect="read", error=False),
    step(4, "tool_call", "get_policy", "get_policy(topic='personal item')",
         "One personal item is included on all fares.",
         effect="read", error=False),
    step(5, "tool_call", "get_policy", "get_policy(topic='cabin')",
         "Cabin bag rules vary by fare family.", effect="read", error=False,
         note="already asked at step 1 — same answer, no new information"),
    step(6, "tool_call", "get_policy", "get_policy(topic='baggage')",
         "Basic economy: one personal item. No cabin bag.",
         effect="read", error=False),
    step(7, "reason", "reason", "That is the answer.", ""),
    step(8, "answer", "final",
         "Basic economy includes one personal item and no cabin bag.",
         "Basic economy includes one personal item and no cabin bag."),
]

RUNS = [
    (P01, "steady-v1", "claude-sonnet-5", P01_CLEAN, False,
     "Booking QX7T2 is cancelled and a full refund of $412.00 was issued "
     "under the 24-hour policy.", "agent_stop"),
    (P01, "hasty-v2", "gpt-5", P01_LOOPING, True,
     "Done — booking QX7T2 has been cancelled and refunded under the "
     "24-hour policy.", "agent_stop"),
    (P02, "steady-v1", "claude-sonnet-5", P02_REAL, True,
     "Booked BA117 for 2 passengers, reference LM94K.", "agent_stop"),
    (P02, "hasty-v2", "gpt-5", P02_CLAIMED, False,
     "Task completed successfully — BA117 has been booked for 2 passengers.",
     "agent_stop"),
    (P03, "steady-v1", "claude-sonnet-5", P03_VALID, True,
     "Rebooked BA117 with 3 seats, reference PP31Z.", "agent_stop"),
    (P03, "hasty-v2", "gpt-5", P03_INVALID, False,
     "I was unable to change the seat count.", "agent_stop"),
    (P04, "steady-v1", "claude-sonnet-5", P04_DIRECT, True,
     "Basic economy includes one personal item and no cabin bag.", "agent_stop"),
    (P04, "hasty-v2", "gpt-5", P04_SCENIC, True,
     "Basic economy includes one personal item and no cabin bag.", "max_steps"),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for path in OUT.glob("*.json"):
        path.unlink()
    for task, agent, model, steps, success, answer, termination in RUNS:
        data = trajectory(task, agent, model, steps, success, answer, termination)
        path = OUT / f"{task['id']}__{agent}.json"
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(RUNS)} trace(s) to {OUT}")
    print("p01 is the headline: hasty-v2 PASSES while looping and writing blind; "
          "steady-v1 FAILS with a spotless process.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
