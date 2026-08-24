#!/usr/bin/env python3
"""Generate the diagnoser's ground-truth benchmark corpus.

The Who&When lesson: attributors that are never evaluated collapse on hard
cases, so the diagnoser must publish its own measured accuracy.  This script
deterministically builds pairs of trajectories — a failing run plus a passing
reference on the same task — each with exactly ONE implanted known cause, and
writes a MANIFEST.json mapping every scenario to the hypothesis kinds a
correct diagnosis is allowed to lead with.

Six causes, two scenarios each:

* **grader_mislabel** — the failing run's answer textually matches the
  expected answer and its process is clean.  Truth: ``grader_or_label``.
* **harness_kill** — ``outcome.termination`` is a harness reason
  (``infrastructure_error`` / ``unexpected_error``).  Truth:
  ``harness_termination``.
* **environment_fault** — a tool step errors (``error: true``) and the agent
  abandons the task.  Truth: ``environment_error``.
* **wrong_fact** — a typed numeric claim contradicting the expected answer
  (money, duration) enters at a known step and propagates into the answer.
  Truth: ``wrong_fact_propagation`` — also satisfied when the diagnosis
  leads with ``divergence`` whose ``mechanism`` is wrong_fact_propagation,
  because that is the same cause told at two depths.
* **blind_write** — a write-effect tool call before any successful read,
  plus a failure.  Truth: ``process_pathology:blind_write`` or
  ``divergence`` (the blind write *is* the divergent decision).
* **divergence_only** — a different retrieval/plan decision with no other
  anomaly.  Truth: ``divergence``.

The implants are realistic (5-8 steps, plausible text, declared tools and
budgets), not tuned until the diagnoser is trivially right: where the
diagnoser genuinely cannot separate two readings the benchmark records the
miss instead of hiding it.  Everything is deterministic — no wall-clock, no
randomness.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "traces"

BUDGET = {"max_steps": 12}

FAIL_AGENT = ("scout-v2", "gpt-5")
PASS_AGENT = ("steady-v1", "claude-sonnet-5")

# Declared tool surfaces per domain (name + effect is what the process
# checks need; parameters are omitted so nothing here trips schema checks
# that are not the scenario's implanted cause).
OPS_TOOLS = [
    {"name": "get_booking", "effect": "read"},
    {"name": "get_policy", "effect": "read"},
    {"name": "get_refund_quote", "effect": "read"},
    {"name": "cancel_booking", "effect": "write"},
    {"name": "update_seat", "effect": "write"},
]
FETCH_TOOLS = [
    {"name": "fetch_page", "effect": "read"},
]
SUPPORT_TOOLS = [
    {"name": "get_ticket", "effect": "read"},
    {"name": "get_crm", "effect": "read"},
    {"name": "update_ticket", "effect": "write"},
]
INFRA_TOOLS = [
    {"name": "get_lb", "effect": "read"},
    {"name": "set_dns_record", "effect": "write"},
    {"name": "resolve_host", "effect": "read"},
]
DB_TOOLS = [
    {"name": "get_runbook", "effect": "read"},
    {"name": "run_backup", "effect": "read"},
    {"name": "restore_snapshot", "effect": "write"},
    {"name": "promote_replica", "effect": "write"},
    {"name": "check_replication", "effect": "read"},
]


def step(index, type_, name, input_, output, **extra):
    record = {"index": index, "type": type_, "name": name, "input": input_,
              "output": output, "tokens": extra.pop("tokens", 40),
              "latency_s": extra.pop("latency_s", 1.1),
              "quality": extra.pop("quality", None),
              "note": extra.pop("note", None)}
    record.update(extra)
    return record


def trajectory(task, agent, model, steps, success, termination, tools):
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
            "input_tokens": sum(s["tokens"] for s in steps) * 3,
            "output_tokens": sum(s["tokens"] for s in steps),
            "cost_usd": round(sum(s["tokens"] for s in steps) * 0.000012, 6),
            "latency_s": round(sum(s["latency_s"] for s in steps), 2),
        },
        "steps": steps,
        "tools": tools,
        "budget": BUDGET,
    }


def answer(index, text):
    return step(index, "answer", "final", text, text)


# ==========================================================================
# grader_mislabel — failed, yet the answer matches the expected answer and
# the process is spotless.  The label is wrong, not the agent.
# ==========================================================================

GM01_TASK = {
    "id": "gm01_flex_refund",
    "prompt": ("What refund does a passenger get for cancelling a Flex fare "
               "ticket 10 days before departure?"),
    "expected": ("Flex fares cancelled more than 7 days before departure "
                 "receive a full refund."),
}
GM01_FAIL = [
    step(0, "plan", "plan",
         "Look up the Flex fare rules, then the cancellation window.", ""),
    step(1, "tool_call", "get_policy", "get_policy(topic='flex fare')",
         "Flex fare: fully flexible; see cancellation policy for refund "
         "windows.", effect="read", error=False),
    step(2, "tool_call", "get_policy", "get_policy(topic='cancellation')",
         "Cancellation policy: Flex fares cancelled more than 7 days before "
         "departure receive a full refund; within 7 days, a credit voucher.",
         effect="read", error=False),
    step(3, "reason", "reason",
         "The ticket is being cancelled 10 days out, which is more than 7 "
         "days before departure.", ""),
    answer(4, "Flex fares cancelled more than 7 days before departure "
              "receive a full refund, so a cancellation 10 days out is "
              "refunded in full."),
]
GM01_PASS = [
    step(0, "plan", "plan",
         "Check the Flex fare policy and the refund window.", ""),
    step(1, "tool_call", "get_policy", "get_policy(topic='flex fare')",
         "Flex fare: fully flexible; see cancellation policy for refund "
         "windows.", effect="read", error=False),
    step(2, "tool_call", "get_policy", "get_policy(topic='cancellation')",
         "Cancellation policy: Flex fares cancelled more than 7 days before "
         "departure receive a full refund; within 7 days, a credit voucher.",
         effect="read", error=False),
    step(3, "reason", "reason",
         "Ten days out is beyond the 7-day window, so the full refund "
         "applies.", ""),
    answer(4, "Flex fares cancelled more than 7 days before departure "
              "receive a full refund."),
]

GM02_TASK = {
    "id": "gm02_kestrel_release",
    "prompt": "When was Kestrel 3.2.0 released?",
    "expected": "Kestrel 3.2.0 was released on 12 March 2026.",
}
GM02_FAIL = [
    step(0, "search", "web_search", "Kestrel 3.2.0 release date",
         "Results: kestrel.dev/releases (official release notes); "
         "blog.kestrel.dev (announcement post)."),
    step(1, "retrieve", "kestrel.dev/releases",
         "open result kestrel.dev/releases",
         "Release notes index for Kestrel, newest first."),
    step(2, "read", "kestrel.dev/releases", "",
         "Kestrel 3.2.0 — released 12 March 2026. Adds incremental "
         "indexing and a new planner."),
    step(3, "reason", "reason",
         "The official release notes date 3.2.0 to 12 March 2026.", ""),
    answer(4, "Kestrel 3.2.0 was released on 12 March 2026."),
]
GM02_PASS = [
    step(0, "search", "web_search", "Kestrel 3.2.0 release date",
         "Results: kestrel.dev/releases (official release notes); "
         "blog.kestrel.dev (announcement post)."),
    step(1, "retrieve", "kestrel.dev/releases",
         "open result kestrel.dev/releases",
         "Release notes index for Kestrel, newest first."),
    step(2, "read", "kestrel.dev/releases", "",
         "Kestrel 3.2.0 — released 12 March 2026. Adds incremental "
         "indexing and a new planner."),
    step(3, "reason", "reason",
         "Official notes give 12 March 2026 for the 3.2.0 release.", ""),
    answer(4, "Kestrel 3.2.0 was released on 12 March 2026, per the "
              "official release notes."),
]

# ==========================================================================
# harness_kill — the harness terminated the run; no agent decision did.
# ==========================================================================

HK01_TASK = {
    "id": "hk01_seat_upgrade",
    "prompt": "Upgrade booking RX417 to a window seat and confirm the change.",
    "expected": "Booking RX417 upgraded to a window seat.",
}
HK01_FAIL = [
    step(0, "plan", "plan",
         "Read the booking, find a window seat, apply the change.", ""),
    step(1, "tool_call", "get_booking", "get_booking(reference='RX417')",
         "RX417 — SEA->DEN, seat 14C (aisle).", effect="read", error=False),
    step(2, "tool_call", "get_policy", "get_policy(topic='seat')",
         "Seat changes are free on the day of booking for all fares.",
         effect="read", error=False),
    answer(3, "The session was cut off by the platform before the seat "
              "change could be applied."),
]
HK01_PASS = [
    step(0, "plan", "plan", "Check the booking, then move the seat.", ""),
    step(1, "tool_call", "get_booking", "get_booking(reference='RX417')",
         "RX417 — SEA->DEN, seat 14C (aisle).", effect="read", error=False),
    step(2, "tool_call", "get_policy", "get_policy(topic='seat')",
         "Seat changes are free on the day of booking for all fares.",
         effect="read", error=False),
    step(3, "tool_call", "update_seat",
         "update_seat(reference='RX417', seat='12A')",
         "Seat changed to 12A (window).", effect="write", error=False),
    answer(4, "Booking RX417 upgraded to a window seat, now 12A."),
]

HK02_TASK = {
    "id": "hk02_meridian_headcount",
    "prompt": "Summarise Meridian Labs' Q2 2026 headcount change.",
    "expected": "Meridian Labs grew headcount by 12 percent in Q2 2026.",
}
HK02_FAIL = [
    step(0, "search", "web_search", "Meridian Labs Q2 2026 headcount",
         "Results: meridianlabs.com/press (Q2 update); "
         "industryweekly.com (analysis)."),
    step(1, "retrieve", "meridianlabs.com/press",
         "open result meridianlabs.com/press",
         "Meridian Labs press room, Q2 2026 update."),
    step(2, "read", "meridianlabs.com/press", "",
         "Q2 2026 update: headcount grew 12 percent quarter over quarter."),
    answer(3, "Run aborted by the harness before the summary could be "
              "written."),
]
HK02_PASS = [
    step(0, "search", "web_search", "Meridian Labs Q2 2026 headcount",
         "Results: meridianlabs.com/press (Q2 update); "
         "industryweekly.com (analysis)."),
    step(1, "retrieve", "meridianlabs.com/press",
         "open result meridianlabs.com/press",
         "Meridian Labs press room, Q2 2026 update."),
    step(2, "read", "meridianlabs.com/press", "",
         "Q2 2026 update: headcount grew 12 percent quarter over quarter."),
    step(3, "reason", "reason",
         "Headcount grew 12 percent in Q2 2026 per the press page.", ""),
    answer(4, "Meridian Labs grew headcount by 12 percent in Q2 2026."),
]

# ==========================================================================
# environment_fault — a tool errors out and the agent abandons the task.
# The fault is the environment's; the trace declares the error.
# ==========================================================================

EF01_TASK = {
    "id": "ef01_refund_gateway",
    "prompt": "Cancel booking VN882 and report the refund.",
    "expected": "Booking VN882 cancelled with a full refund.",
}
EF01_FAIL = [
    step(0, "plan", "plan", "Read the booking, then cancel with refund.", ""),
    step(1, "tool_call", "get_booking", "get_booking(reference='VN882')",
         "VN882 — LIS->MAD, Flex fare, refundable.",
         effect="read", error=False),
    step(2, "tool_call", "cancel_booking",
         "cancel_booking(reference='VN882', refund=true)",
         "Error: payment gateway returned 503 Service Unavailable; "
         "cancellation rolled back.", effect="write", error=True),
    step(3, "reason", "reason",
         "The refund gateway is down; the cancellation cannot go through "
         "right now.", ""),
    answer(4, "I could not cancel booking VN882: the payment gateway is "
              "unavailable."),
]
EF01_PASS = [
    step(0, "plan", "plan", "Read the booking, then cancel with refund.", ""),
    step(1, "tool_call", "get_booking", "get_booking(reference='VN882')",
         "VN882 — LIS->MAD, Flex fare, refundable.",
         effect="read", error=False),
    step(2, "tool_call", "cancel_booking",
         "cancel_booking(reference='VN882', refund=true)",
         "Cancelled. Full refund issued to the original card.",
         effect="write", error=False),
    answer(3, "Booking VN882 cancelled with a full refund."),
]

EF02_TASK = {
    "id": "ef02_docs_outage",
    "prompt": "What changed in AcmeCorp SDK 4.1.0 according to the changelog?",
    "expected": "SDK 4.1.0 adds async upload support and drops Python 3.8.",
}
EF02_FAIL = [
    step(0, "search", "web_search", "AcmeCorp SDK 4.1.0 changelog",
         "Top result: docs.acmecorp.com/changelog."),
    step(1, "tool_call", "fetch_page",
         "fetch_page(url='docs.acmecorp.com/changelog')",
         "Error: connection timed out fetching docs.acmecorp.com/changelog.",
         effect="read", error=True),
    step(2, "tool_call", "fetch_page",
         "fetch_page(url='docs.acmecorp.com/changelog', cache='no')",
         "Error: upstream host docs.acmecorp.com unreachable (503).",
         effect="read", error=True),
    step(3, "reason", "reason",
         "The documentation host is down; the changelog cannot be read.", ""),
    answer(4, "I could not retrieve the SDK 4.1.0 changelog: "
              "docs.acmecorp.com is unreachable."),
]
EF02_PASS = [
    step(0, "search", "web_search", "AcmeCorp SDK 4.1.0 changelog",
         "Top result: docs.acmecorp.com/changelog."),
    step(1, "tool_call", "fetch_page",
         "fetch_page(url='docs.acmecorp.com/changelog')",
         "Changelog 4.1.0: adds async upload support; drops Python 3.8.",
         effect="read", error=False),
    step(2, "reason", "reason",
         "4.1.0 adds async upload and removes Python 3.8 support.", ""),
    answer(3, "SDK 4.1.0 adds async upload support and drops Python 3.8."),
]

# ==========================================================================
# wrong_fact — a numeric claim contradicting the expected answer enters at a
# known step and propagates into the final answer.
# ==========================================================================

WF01_TASK = {
    "id": "wf01_refund_amount",
    "prompt": "Cancel booking KL8340 and report the refund amount.",
    "expected": "Booking KL8340 cancelled with a refund of $412.00.",
}
WF01_FAIL = [
    step(0, "plan", "plan",
         "Read the booking, check the refund table, then cancel.", ""),
    step(1, "tool_call", "get_booking", "get_booking(reference='KL8340')",
         "KL8340 — AMS->BOS, Flex fare.", effect="read", error=False),
    step(2, "tool_call", "get_policy", "get_policy(topic='refund')",
         "Cached refund table (stale revision): Flex fare AMS->BOS refunds "
         "$389.00.", effect="read", error=False,
         note="stale cached table — the wrong amount enters here"),
    step(3, "tool_call", "cancel_booking",
         "cancel_booking(reference='KL8340', refund=true)",
         "Cancelled. Refund processed per fare table.",
         effect="write", error=False),
    step(4, "reason", "reason",
         "The refund table shows $389.00 for this fare.", ""),
    answer(5, "Booking KL8340 cancelled with a refund of $389.00."),
]
WF01_PASS = [
    step(0, "plan", "plan",
         "Read the booking, quote the live refund, then cancel.", ""),
    step(1, "tool_call", "get_booking", "get_booking(reference='KL8340')",
         "KL8340 — AMS->BOS, Flex fare.", effect="read", error=False),
    step(2, "tool_call", "get_refund_quote",
         "get_refund_quote(reference='KL8340')",
         "Live refund quote for KL8340: $412.00.",
         effect="read", error=False),
    step(3, "tool_call", "cancel_booking",
         "cancel_booking(reference='KL8340', refund=true)",
         "Cancelled. Refund of $412.00 issued.",
         effect="write", error=False),
    answer(4, "Booking KL8340 cancelled with a refund of $412.00."),
]

WF02_TASK = {
    "id": "wf02_battery_life",
    "prompt": "How long does the Solara X2 battery last on a full charge?",
    "expected": ("The Solara X2 battery lasts 9 hours 30 minutes on a full "
                 "charge."),
}
WF02_FAIL = [
    step(0, "search", "web_search", "Solara X2 battery life full charge",
         "Results: solara.com/specs (official spec sheet); "
         "techradar.com (review)."),
    step(1, "retrieve", "solara.com/specs", "open result solara.com/specs",
         "Solara X2 specification page."),
    step(2, "read", "solara.com/specs", "",
         "Spec sheet (archived revision): battery life 7 hours 45 minutes "
         "on a full charge.",
         note="archived revision — the wrong duration enters here"),
    step(3, "reason", "reason",
         "The spec sheet lists 7 hours 45 minutes.", ""),
    answer(4, "The Solara X2 battery lasts 7 hours 45 minutes on a full "
              "charge."),
]
WF02_PASS = [
    step(0, "search", "web_search", "Solara X2 battery life full charge",
         "Results: solara.com/specs (official spec sheet); "
         "techradar.com (review)."),
    step(1, "retrieve", "solara.com/specs", "open result solara.com/specs",
         "Solara X2 specification page."),
    step(2, "read", "solara.com/specs", "",
         "Spec sheet (current): battery life 9 hours 30 minutes on a full "
         "charge."),
    step(3, "reason", "reason",
         "The current spec sheet lists 9 hours 30 minutes.", ""),
    answer(4, "The Solara X2 battery lasts 9 hours 30 minutes on a full "
              "charge."),
]

# ==========================================================================
# blind_write — external state changed before anything was read, plus a
# failure.  The blind write IS the divergent decision, so a diagnosis of
# divergence rooted there is the same cause.
# ==========================================================================

BW01_TASK = {
    "id": "bw01_ticket_address",
    "prompt": ("Update ticket T-5521 with the customer's new address from "
               "the CRM and confirm."),
    "expected": "Ticket T-5521 updated with the address 47 Mill Road.",
}
BW01_FAIL = [
    step(0, "plan", "plan",
         "Apply the address change to the ticket straight away.", "",
         quality="weak", note="commits to writing before reading anything"),
    step(1, "tool_call", "update_ticket",
         "update_ticket(ticket='T-5521', field='address')",
         "Ticket T-5521 address field re-saved with its existing value.",
         effect="write", error=False, quality="bad",
         note="write-effect call before any read"),
    step(2, "tool_call", "get_crm", "get_crm(ticket='T-5521')",
         "CRM record: new address on file is 47 Mill Road.",
         effect="read", error=False),
    step(3, "reason", "reason",
         "The ticket save already went through; the CRM value should now "
         "be on it.", "", quality="weak"),
    answer(4, "Ticket T-5521 was updated with the customer's new address."),
]
BW01_PASS = [
    step(0, "plan", "plan",
         "Read the CRM record first, then update the ticket.", ""),
    step(1, "tool_call", "get_crm", "get_crm(ticket='T-5521')",
         "CRM record: new address on file is 47 Mill Road.",
         effect="read", error=False),
    step(2, "tool_call", "get_ticket", "get_ticket(ticket='T-5521')",
         "T-5521 — address field: 9 Elm Court (outdated).",
         effect="read", error=False),
    step(3, "tool_call", "update_ticket",
         "update_ticket(ticket='T-5521', field='address', "
         "value='47 Mill Road')",
         "Ticket T-5521 updated: address set to 47 Mill Road.",
         effect="write", error=False),
    answer(4, "Ticket T-5521 updated with the address 47 Mill Road."),
]

BW02_TASK = {
    "id": "bw02_dns_cutover",
    "prompt": ("Point staging.acme.dev at the new load balancer lb-eu-2 and "
               "verify it resolves."),
    "expected": "staging.acme.dev points at lb-eu-2 and resolves correctly.",
}
BW02_FAIL = [
    step(0, "tool_call", "set_dns_record",
         "set_dns_record(host='staging.acme.dev', target='lb-eu-1')",
         "Record saved: staging.acme.dev -> lb-eu-1.",
         effect="write", error=False, quality="bad",
         note="wrote the DNS record before reading which balancer is new"),
    step(1, "tool_call", "resolve_host",
         "resolve_host(host='staging.acme.dev')",
         "staging.acme.dev currently resolves to lb-eu-1.",
         effect="read", error=False, quality="weak"),
    step(2, "reason", "reason",
         "The record points at lb-eu-1, which should be the new balancer.",
         "", quality="weak"),
    answer(3, "staging.acme.dev now points at lb-eu-1 and resolves."),
]
BW02_PASS = [
    step(0, "tool_call", "get_lb", "get_lb(env='staging')",
         "New load balancer for staging: lb-eu-2 (replaces lb-eu-1).",
         effect="read", error=False),
    step(1, "tool_call", "set_dns_record",
         "set_dns_record(host='staging.acme.dev', target='lb-eu-2')",
         "Record saved: staging.acme.dev -> lb-eu-2.",
         effect="write", error=False),
    step(2, "tool_call", "resolve_host",
         "resolve_host(host='staging.acme.dev')",
         "staging.acme.dev resolves to lb-eu-2.",
         effect="read", error=False),
    answer(3, "staging.acme.dev points at lb-eu-2 and resolves correctly."),
]

# ==========================================================================
# divergence_only — a different retrieval/plan decision, nothing else
# anomalous: no errors, no writes-before-reads, no contradicting claims.
# ==========================================================================

DV01_TASK = {
    "id": "dv01_keynote_hall",
    "prompt": "Which hall hosts the Northwind DevCon keynote this year?",
    "expected": "The keynote is in Hall B of the Riverside Centre.",
}
DV01_FAIL = [
    step(0, "search", "web_search", "Northwind DevCon keynote hall",
         "Results: northwind.dev/schedule (official programme); "
         "devcon-fans.net (community wiki)."),
    step(1, "retrieve", "devcon-fans.net",
         "open result devcon-fans.net community wiki",
         "Community wiki: venue notes, last updated before the venue "
         "change.", quality="weak",
         note="chose the unofficial source over the official programme"),
    step(2, "read", "devcon-fans.net", "",
         "Wiki venue page: keynote listed in Hall D (previous layout)."),
    step(3, "reason", "reason", "The wiki lists Hall D for the keynote.",
         ""),
    answer(4, "The keynote is in Hall D."),
]
DV01_PASS = [
    step(0, "search", "web_search", "Northwind DevCon keynote hall",
         "Results: northwind.dev/schedule (official programme); "
         "devcon-fans.net (community wiki)."),
    step(1, "retrieve", "northwind.dev/schedule",
         "open result northwind.dev/schedule official programme",
         "Official programme: session schedule with rooms."),
    step(2, "read", "northwind.dev/schedule", "",
         "Keynote — Hall B, Riverside Centre."),
    step(3, "reason", "reason",
         "The official programme lists Hall B at the Riverside Centre.", ""),
    answer(4, "The keynote is in Hall B of the Riverside Centre."),
]

DV02_TASK = {
    "id": "dv02_db_migration",
    "prompt": ("Migrate the analytics database to the new host with minimal "
               "downtime and report the approach."),
    "expected": ("Migrated by promoting the standby replica; downtime was "
                 "minimal."),
}
DV02_FAIL = [
    step(0, "plan", "plan",
         "Take a full dump and restore it onto the new host.", "",
         quality="weak", note="ignored the standby replica path"),
    step(1, "tool_call", "get_runbook", "get_runbook(topic='database')",
         "Runbook: preferred path is standby replica promotion; "
         "dump-and-restore requires an outage window.",
         effect="read", error=False),
    step(2, "tool_call", "run_backup", "run_backup(target='analytics')",
         "Full dump of analytics written to the snapshot store.",
         effect="read", error=False),
    step(3, "tool_call", "restore_snapshot",
         "restore_snapshot(target='analytics')",
         "Snapshot restored onto the new host; the database was offline "
         "while the restore ran.", effect="write", error=False),
    answer(4, "Migrated the analytics database with a dump and restore; "
              "the database was offline while the restore ran."),
]
DV02_PASS = [
    step(0, "plan", "plan",
         "Promote the standby replica per the runbook to avoid downtime.",
         ""),
    step(1, "tool_call", "get_runbook", "get_runbook(topic='database')",
         "Runbook: preferred path is standby replica promotion; "
         "dump-and-restore requires an outage window.",
         effect="read", error=False),
    step(2, "tool_call", "check_replication",
         "check_replication(target='analytics')",
         "Standby replica is in sync with the primary.",
         effect="read", error=False),
    step(3, "tool_call", "promote_replica",
         "promote_replica(target='analytics')",
         "Standby promoted to primary; cutover took under a minute.",
         effect="write", error=False),
    answer(4, "Migrated by promoting the standby replica; downtime was "
              "minimal."),
]


# ==========================================================================
# HARD MODE.  The literature's documented failure modes, reproduced:
# attributors blame the loudest downstream symptom instead of the earliest
# cause (Who&When), and they are fooled by visible-but-non-causal
# pathologies.  These scenarios exist to catch exactly those mistakes.
# ==========================================================================

# --------------------------------------------------------------------------
# late_symptom — a quiet wrong decision early, a LOUD tool error later that
# the wrong decision itself caused (and the agent recovers from).  The trap
# is diagnosing the loud error; the truth is the quiet early step.
# --------------------------------------------------------------------------

LS01_TASK = {
    "id": "ls01_delayed_segment",
    "prompt": ("Rebook the passenger on the delayed segment of booking "
               "HJ221 and confirm the new flight."),
    "expected": "Rebooked the delayed segment SEG-9 onto flight NW441.",
}
LS01_FAIL = [
    step(0, "plan", "plan",
         "Find the delayed segment, then rebook it.", ""),
    step(1, "tool_call", "get_booking",
         "get_booking(reference='HJ221', source='cache')",
         "HJ221 (cached copy): segments SEG-7 (delayed), SEG-8.",
         effect="read", error=False, quality="weak",
         note="stale cache: the live booking lists SEG-9 as delayed"),
    step(2, "tool_call", "rebook_segment",
         "rebook_segment(reference='HJ221', segment='SEG-7')",
         "Error: no such segment SEG-7 on booking HJ221.",
         effect="write", error=True),
    step(3, "tool_call", "rebook_segment",
         "rebook_segment(reference='HJ221', segment='SEG-8')",
         "Rebooked SEG-8 onto flight NW302.",
         effect="write", error=False),
    answer(4, "Rebooked segment SEG-8 of booking HJ221 onto flight NW302."),
]
LS01_PASS = [
    step(0, "plan", "plan",
         "Find the delayed segment, then rebook it.", ""),
    step(1, "tool_call", "list_segments",
         "list_segments(reference='HJ221')",
         "Live segments for HJ221: SEG-8 (on time), SEG-9 (delayed 3h).",
         effect="read", error=False),
    step(2, "tool_call", "rebook_segment",
         "rebook_segment(reference='HJ221', segment='SEG-9')",
         "Rebooked SEG-9 onto flight NW441.",
         effect="write", error=False),
    answer(3, "Rebooked the delayed segment SEG-9 onto flight NW441."),
]
LS_TOOLS = [
    {"name": "get_booking", "effect": "read"},
    {"name": "list_segments", "effect": "read"},
    {"name": "rebook_segment", "effect": "write"},
]

LS02_TASK = {
    "id": "ls02_route_duration",
    "prompt": "How long is the Auckland to Santiago flight NW7?",
    "expected": "Flight NW7 Auckland to Santiago takes 11 hours 20 minutes.",
}
LS02_FAIL = [
    step(0, "search", "web_search", "flight NW7 Auckland Santiago duration",
         "Results: northwind.com/routes (route map, archived snapshot); "
         "flightaware.com/NW7 (live tracking)."),
    step(1, "retrieve", "northwind.com/routes",
         "open result northwind.com/routes archived snapshot",
         "Archived route map, pre-schedule-change revision.", quality="weak",
         note="archived snapshot; the live tracker was also in the results"),
    step(2, "read", "northwind.com/routes", "",
         "Route table (archived): NW7 AKL-SCL block time 12 hours "
         "50 minutes."),
    step(3, "tool_call", "fetch_page",
         "fetch_page(url='northwind.com/fleet')",
         "Error: connection timed out fetching northwind.com/fleet.",
         effect="read", error=True),
    step(4, "tool_call", "fetch_page",
         "fetch_page(url='northwind.com/aircraft')",
         "Aircraft page: NW7 operates a 787-9 on the AKL-SCL route.",
         effect="read", error=False),
    step(5, "reason", "reason",
         "The route table gives 12 hours 50 minutes for NW7.", ""),
    answer(6, "Flight NW7 Auckland to Santiago takes 12 hours 50 minutes."),
]
LS02_PASS = [
    step(0, "search", "web_search", "flight NW7 Auckland Santiago duration",
         "Results: northwind.com/routes (route map, archived snapshot); "
         "flightaware.com/NW7 (live tracking)."),
    step(1, "retrieve", "flightaware.com/NW7",
         "open result flightaware.com/NW7 live tracking",
         "Live tracking page for NW7 with current schedule."),
    step(2, "read", "flightaware.com/NW7", "",
         "NW7 AKL-SCL: scheduled block time 11 hours 20 minutes."),
    step(3, "reason", "reason",
         "The live schedule gives 11 hours 20 minutes.", ""),
    answer(4, "Flight NW7 Auckland to Santiago takes 11 hours 20 minutes."),
]

# --------------------------------------------------------------------------
# distractor — the true cause plus a visible but NON-causal pathology.  A
# diagnoser fooled by visibility blames the distractor.
# --------------------------------------------------------------------------

DP01_TASK = {
    "id": "dp01_warranty_claim",
    "prompt": ("Which warranty applies to order 88410 and does it cover "
               "water damage?"),
    "expected": ("Order 88410 carries the Plus warranty, which covers "
                 "water damage."),
}
DP01_FAIL = [
    step(0, "plan", "plan",
         "Find the order's warranty tier, then check its coverage.", ""),
    step(1, "tool_call", "get_ticket", "get_ticket(order='88410')",
         "Ticket for order 88410: customer reports water damage; account "
         "note mentions the Basic tier brochure was mailed at purchase.",
         effect="read", error=False, quality="weak",
         note="took the mailed-brochure note as the warranty of record"),
    step(2, "tool_call", "get_policy", "get_policy(topic='warranty basic')",
         "Basic warranty: manufacturing defects only; excludes liquid "
         "damage.", effect="read", error=False),
    step(3, "tool_call", "get_policy", "get_policy(topic='warranty basic')",
         "Basic warranty: manufacturing defects only; excludes liquid "
         "damage.", effect="read", error=False,
         note="identical call repeated; adds nothing and changes nothing"),
    step(4, "reason", "reason",
         "Basic tier excludes liquid damage, so this claim is not covered.",
         ""),
    answer(5, "Order 88410 has the Basic warranty, which does not cover "
              "water damage."),
]
DP01_PASS = [
    step(0, "plan", "plan",
         "Look up the warranty registered on the order itself.", ""),
    step(1, "tool_call", "get_crm", "get_crm(order='88410')",
         "Order 88410: registered warranty tier is Plus (upgraded at "
         "checkout).", effect="read", error=False),
    step(2, "tool_call", "get_policy", "get_policy(topic='warranty plus')",
         "Plus warranty: covers accidental damage including liquid "
         "ingress.", effect="read", error=False),
    step(3, "reason", "reason",
         "The registered tier is Plus, which covers water damage.", ""),
    answer(4, "Order 88410 carries the Plus warranty, which covers water "
              "damage."),
]

DP02_TASK = {
    "id": "dp02_cache_flush",
    "prompt": ("Flush the CDN cache for release 5.4 the way the runbook "
               "prescribes and report what was done."),
    "expected": ("Flushed the CDN cache per runbook: soft purge of the "
                 "release-5.4 tag."),
}
DP02_FAIL = [
    step(0, "plan", "plan",
         "Purge everything to be safe, then confirm.", "", quality="weak",
         note="the runbook prescribes a tag-scoped soft purge, not a full "
              "purge"),
    step(1, "tool_call", "get_runbook", "get_runbook(topic='cdn')",
         "Runbook: releases are flushed with a soft purge of their tag; "
         "full purges are for incidents only.",
         effect="read", error=False),
    step(2, "tool_call", "purge_cache", "purge_cache(scope='all')",
         "Full cache purge started across all tags.",
         effect="write", error=False, quality="bad"),
    step(3, "tool_call", "check_cache", "check_cache(tag='release-5.4')",
         "Cache empty for all tags; origin load elevated.",
         effect="read", error=False),
    step(4, "tool_call", "check_cache", "check_cache(tag='release-5.4')",
         "Cache empty for all tags; origin load elevated.",
         effect="read", error=False,
         note="identical check repeated; no new information"),
    answer(5, "Flushed the CDN by purging the entire cache; origin load is "
              "elevated but should recover."),
]
DP02_PASS = [
    step(0, "plan", "plan",
         "Follow the runbook's release flush procedure.", ""),
    step(1, "tool_call", "get_runbook", "get_runbook(topic='cdn')",
         "Runbook: releases are flushed with a soft purge of their tag; "
         "full purges are for incidents only.",
         effect="read", error=False),
    step(2, "tool_call", "purge_cache",
         "purge_cache(tag='release-5.4', mode='soft')",
         "Soft purge queued for tag release-5.4.",
         effect="write", error=False),
    answer(3, "Flushed the CDN cache per runbook: soft purge of the "
              "release-5.4 tag."),
]
DP02_TOOLS = [
    {"name": "get_runbook", "effect": "read"},
    {"name": "purge_cache", "effect": "write"},
    {"name": "check_cache", "effect": "read"},
]

# --------------------------------------------------------------------------
# cascade — a Who&When-Pro-style injection: the failing run replays the
# passing run's prefix EXACTLY, the fault enters mid-trace, and downstream
# steps carry the symptoms.  Tests that the anchor lands at the injection
# point, not at the start and not at the symptoms.
# --------------------------------------------------------------------------

CS01_TASK = {
    "id": "cs01_invoice_total",
    "prompt": ("What is the total of invoice INV-2209 after the partner "
               "discount?"),
    "expected": "Invoice INV-2209 totals $8,940.00 after the discount.",
}
_CS01_PREFIX = [
    step(0, "plan", "plan",
         "Fetch the invoice, apply the partner discount, report the "
         "total.", ""),
    step(1, "tool_call", "get_invoice", "get_invoice(id='INV-2209')",
         "INV-2209: subtotal $9,400.00, partner account.",
         effect="read", error=False),
]
CS01_FAIL = _CS01_PREFIX + [
    step(2, "tool_call", "get_policy", "get_policy(topic='partner discount')",
         "Discount memo (superseded draft): partner discount is $940.00 "
         "flat.", effect="read", error=False,
         note="superseded draft; the current schedule is percentage-based"),
    step(3, "reason", "reason",
         "Subtotal $9,400.00 minus the $940.00 flat discount.", "",
         quality="weak"),
    answer(4, "Invoice INV-2209 totals $8,460.00 after the discount."),
]
CS01_PASS = _CS01_PREFIX + [
    step(2, "tool_call", "get_discount_schedule",
         "get_discount_schedule(account='partner')",
         "Current schedule: partner discount is $460.00 flat plus loyalty "
         "rebate; net discount for INV-2209 is $460.00.",
         effect="read", error=False),
    step(3, "reason", "reason",
         "Subtotal $9,400.00 minus the $460.00 scheduled discount.", ""),
    answer(4, "Invoice INV-2209 totals $8,940.00 after the discount."),
]
CS01_TOOLS = [
    {"name": "get_invoice", "effect": "read"},
    {"name": "get_policy", "effect": "read"},
    {"name": "get_discount_schedule", "effect": "read"},
]

CS02_TASK = {
    "id": "cs02_sla_response",
    "prompt": ("What response time does the Gold SLA guarantee for "
               "priority-1 incidents?"),
    "expected": ("The Gold SLA guarantees a 30 minutes response for "
                 "priority-1 incidents."),
}
_CS02_PREFIX = [
    step(0, "search", "web_search", "Gold SLA priority-1 response time",
         "Results: support.acme.com/sla (current terms); "
         "cache.acme.com/sla-2024 (old mirror)."),
    step(1, "retrieve", "support.acme.com/sla",
         "open result support.acme.com/sla",
         "Acme support SLA index page."),
]
CS02_FAIL = _CS02_PREFIX + [
    step(2, "read", "cache.acme.com/sla-2024", "",
         "Mirrored 2024 terms: Gold SLA priority-1 response 2 hours.",
         quality="weak",
         note="read the old mirror instead of the index's current page"),
    step(3, "reason", "reason",
         "The SLA table lists 2 hours for priority-1 under Gold.", ""),
    answer(4, "The Gold SLA guarantees a 2 hours response for priority-1 "
              "incidents."),
]
CS02_PASS = _CS02_PREFIX + [
    step(2, "read", "support.acme.com/sla", "",
         "Current terms: Gold SLA priority-1 response 30 minutes."),
    step(3, "reason", "reason",
         "Current Gold terms give 30 minutes for priority-1.", ""),
    answer(4, "The Gold SLA guarantees a 30 minutes response for "
              "priority-1 incidents."),
]


# ==========================================================================
# scenario table
# ==========================================================================

#: (id, cause, acceptable kinds, decisive steps — the earliest step(s)
#:  whose correction flips the outcome, [] when no agent step exists to
#:  correct — task, fail steps, fail termination, pass steps, tools, note)
SCENARIOS = [
    ("gm01_flex_refund", "grader_mislabel", ["grader_or_label"], [],
     GM01_TASK, GM01_FAIL, "agent_stop", GM01_PASS, OPS_TOOLS,
     "failing answer textually matches the expected answer; process clean"),
    ("gm02_kestrel_release", "grader_mislabel", ["grader_or_label"], [],
     GM02_TASK, GM02_FAIL, "agent_stop", GM02_PASS, [],
     "failing answer carries the expected date and version; process clean"),
    ("hk01_seat_upgrade", "harness_kill", ["harness_termination"], [],
     HK01_TASK, HK01_FAIL, "infrastructure_error", HK01_PASS, OPS_TOOLS,
     "harness declared infrastructure_error before the write happened"),
    ("hk02_meridian_headcount", "harness_kill", ["harness_termination"], [],
     HK02_TASK, HK02_FAIL, "unexpected_error", HK02_PASS, [],
     "harness declared unexpected_error mid-run"),
    ("ef01_refund_gateway", "environment_fault", ["environment_error"], [2],
     EF01_TASK, EF01_FAIL, "agent_stop", EF01_PASS, OPS_TOOLS,
     "declared tool error (payment gateway 503); agent abandoned after it"),
    ("ef02_docs_outage", "environment_fault", ["environment_error"], [1],
     EF02_TASK, EF02_FAIL, "agent_stop", EF02_PASS, FETCH_TOOLS,
     "two declared fetch errors (timeout, 503); agent abandoned after them"),
    ("wf01_refund_amount", "wrong_fact", ["wrong_fact_propagation"], [2],
     WF01_TASK, WF01_FAIL, "agent_stop", WF01_PASS, OPS_TOOLS,
     "$389.00 enters at step 2 from a stale table and reaches the answer; "
     "expected is $412.00 (divergence with mechanism "
     "wrong_fact_propagation counts)"),
    ("wf02_battery_life", "wrong_fact", ["wrong_fact_propagation"], [2],
     WF02_TASK, WF02_FAIL, "agent_stop", WF02_PASS, [],
     "7 hours 45 minutes enters at step 2 from an archived spec sheet; "
     "expected is 9 hours 30 minutes; structure otherwise identical"),
    ("bw01_ticket_address", "blind_write",
     ["process_pathology:blind_write", "divergence"], [0, 1],
     BW01_TASK, BW01_FAIL, "agent_stop", BW01_PASS, SUPPORT_TOOLS,
     "write-effect update at step 1 before any read; the blind write is "
     "the divergent decision, so divergence rooted there is the same cause"),
    ("bw02_dns_cutover", "blind_write",
     ["process_pathology:blind_write", "divergence"], [0],
     BW02_TASK, BW02_FAIL, "agent_stop", BW02_PASS, INFRA_TOOLS,
     "DNS write at step 0 before reading which balancer is new"),
    ("dv01_keynote_hall", "divergence_only", ["divergence"], [1],
     DV01_TASK, DV01_FAIL, "agent_stop", DV01_PASS, [],
     "retrieval decision differs (community wiki vs official programme); "
     "no other anomaly"),
    ("dv02_db_migration", "divergence_only", ["divergence"], [0],
     DV02_TASK, DV02_FAIL, "agent_stop", DV02_PASS, DB_TOOLS,
     "planning decision differs (dump-and-restore vs replica promotion); "
     "no other anomaly"),
    ("ls01_delayed_segment", "late_symptom", ["divergence"], [1],
     LS01_TASK, LS01_FAIL, "agent_stop", LS01_PASS, LS_TOOLS,
     "quiet stale-cache read at step 1 caused the loud rebook error at "
     "step 2, which the agent recovered from; the trap is blaming the "
     "error"),
    ("ls02_route_duration", "late_symptom",
     ["wrong_fact_propagation", "divergence"], [1, 2],
     LS02_TASK, LS02_FAIL, "agent_stop", LS02_PASS, FETCH_TOOLS,
     "wrong duration enters quietly from an archived snapshot at steps "
     "1-2; a loud unrelated fetch timeout at step 3 is the trap"),
    ("dp01_warranty_claim", "distractor", ["divergence"], [0, 1],
     DP01_TASK, DP01_FAIL, "agent_stop", DP01_PASS, SUPPORT_TOOLS,
     "true cause: consulted the ticket note instead of the order of "
     "record — a choice already made in the step-0 plan and executed at "
     "step 1, so correcting either flips the outcome; distractor: an "
     "identical repeated policy call at step 3 that changed nothing"),
    ("dp02_cache_flush", "distractor", ["divergence"], [0],
     DP02_TASK, DP02_FAIL, "agent_stop", DP02_PASS, DP02_TOOLS,
     "true cause: the full-purge plan at step 0 against the runbook; "
     "distractor: a repeated identical cache check at step 4"),
    ("cs01_invoice_total", "cascade",
     ["wrong_fact_propagation", "divergence"], [2],
     CS01_TASK, CS01_FAIL, "agent_stop", CS01_PASS, CS01_TOOLS,
     "fail replays the passing prefix exactly (steps 0-1); the fault (a "
     "superseded discount memo) is injected at step 2 and cascades"),
    ("cs02_sla_response", "cascade",
     ["wrong_fact_propagation", "divergence"], [2],
     CS02_TASK, CS02_FAIL, "agent_stop", CS02_PASS, [],
     "fail replays the passing prefix exactly (steps 0-1); the old-mirror "
     "read is injected at step 2"),
]


#: the fault's true propagation path per scenario — decisive step through
#: the answer, distractor and unrelated steps excluded.  [] where no agent
#: fault exists to propagate.
CHAINS = {'gm01_flex_refund': [], 'gm02_kestrel_release': [], 'hk01_seat_upgrade': [], 'hk02_meridian_headcount': [], 'ef01_refund_gateway': [2, 3, 4], 'ef02_docs_outage': [1, 2, 3, 4], 'wf01_refund_amount': [2, 4, 5], 'wf02_battery_life': [2, 3, 4], 'bw01_ticket_address': [0, 1, 3, 4], 'bw02_dns_cutover': [0, 1, 2, 3], 'dv01_keynote_hall': [1, 2, 3, 4], 'dv02_db_migration': [0, 2, 3, 4], 'ls01_delayed_segment': [1, 2, 3, 4], 'ls02_route_duration': [1, 2, 5, 6], 'dp01_warranty_claim': [0, 1, 2, 4, 5], 'dp02_cache_flush': [0, 2, 3, 5], 'cs01_invoice_total': [2, 3, 4], 'cs02_sla_response': [2, 3, 4]}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for path in OUT.glob("*.json"):
        path.unlink()
    manifest = {"version": 2, "scenarios": []}
    for (sid, cause, acceptable, decisive_steps, task, fail_steps,
         fail_term, pass_steps, tools, note) in SCENARIOS:
        fail_name, fail_model = FAIL_AGENT
        pass_name, pass_model = PASS_AGENT
        fail = trajectory(task, fail_name, fail_model, fail_steps,
                          success=False, termination=fail_term, tools=tools)
        passing = trajectory(task, pass_name, pass_model, pass_steps,
                             success=True, termination="agent_stop",
                             tools=tools)
        fail_file = f"{sid}__fail.json"
        pass_file = f"{sid}__pass.json"
        (OUT / fail_file).write_text(
            json.dumps(fail, indent=2) + "\n", encoding="utf-8")
        (OUT / pass_file).write_text(
            json.dumps(passing, indent=2) + "\n", encoding="utf-8")
        manifest["scenarios"].append({
            "id": sid,
            "cause": cause,
            "acceptable": acceptable,
            "decisive_steps": decisive_steps,
            "chain": CHAINS[sid],
            "fail": fail_file,
            "pass": pass_file,
            "note": note,
        })
    (OUT / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(SCENARIOS)} scenario pair(s) to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
