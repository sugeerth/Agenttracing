"""A run that read another source names that call, not the later step that used what it read
(diagnosis._other_source).

The value a run carries enters where it read it; the decision that used it is the carrier. When the
aligner pairs a call with the other run's call of a different tool, with a different result, and the run never
made the other run's call at all, the call is where the runs part. When the run does make that call later, the
pairing was a delay, and the later decision stays the decisive step.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.report import compare  # noqa: E402
from agentdiff.trace import Trajectory  # noqa: E402


def run(name, ok, steps):
    body = [{"index": i, "type": t, "name": n, "input": inp, "output": out} for i, (t, n, inp, out) in enumerate(steps)]
    return Trajectory.from_dict({"schema_version": 1, "trace_id": name, "run_id": name, "agent": {"name": "a"},
                                 "task": {"id": "ticket", "prompt": "Update the address."},
                                 "outcome": {"success": ok, "answer": steps[-1][3]}, "totals": {}, "steps": body})


PLAN = ("plan", "plan", "Confirm the details, then complete the task.", "")
SAVE = ("tool_call", "update_ticket", "update_ticket(ticket='T-1', field='address')", "Ticket T-1 address field saved.")
STALE = ("tool_call", "get_ticket", "get_ticket(ticket='T-1', field='notes')",
         "Ticket note (outdated): previous address mentioned in passing.")
RIGHT = ("tool_call", "get_crm", "get_crm(ticket='T-1')", "CRM record: registered address on file for T-1.")


def decisive(failing, passing):
    return compare(failing, passing)["diagnosis"]["decisive_step"]["step"]


class OtherSourceTest(unittest.TestCase):
    def test_reading_another_source_is_where_the_runs_part(self):
        failing = run("f", False, [PLAN, STALE, SAVE, ("reason", "reason", "The source gives  for this case.", ""),
                                   ("answer", "final", "Ticket T-1 was updated with the address on file.",
                                    "Ticket T-1 was updated with the address on file.")])
        passing = run("p", True, [PLAN, RIGHT, SAVE, ("reason", "reason", "The authoritative page lists the registered value.", ""),
                                  ("answer", "final", "Ticket T-1 updated with the registered address.",
                                   "Ticket T-1 updated with the registered address.")])
        self.assertEqual(decisive(failing, passing), 1)

    def test_reaching_the_same_source_later_is_a_delay(self):
        check = ("tool_call", "get_ticket", "get_ticket(ticket='T-1') (cross-check)", "Cross-check complete: nothing further to note.")
        again = ("tool_call", "get_ticket", "get_ticket(ticket='T-1') (second cross-check)", "Second cross-check complete: consistent.")
        late = ("tool_call", "get_crm", "get_crm(ticket='T-1') (secondary source)", "CRM record: registered address on file for T-1.")
        failing = run("f", False, [PLAN, check, again, late, ("reason", "reason", "The authoritative page lists the secondary value.", ""),
                                   ("answer", "final", "Ticket T-1 was updated with the address on file.",
                                    "Ticket T-1 was updated with the address on file.")])
        passing = run("p", True, [PLAN, RIGHT, SAVE, ("reason", "reason", "The authoritative page lists the registered value.", ""),
                                  ("answer", "final", "Ticket T-1 updated with the registered address.",
                                   "Ticket T-1 updated with the registered address.")])
        # the failing run made the passing run's call (get_crm, same result), only later: not another source
        self.assertNotEqual(decisive(failing, passing), 1)


if __name__ == "__main__":
    unittest.main()
