"""Where the time went: latencies attributed to thinking, tools and the
answer, wasted steps named from the reading, a rationale whose numbers
are in the ledger, and unmeasurable when nothing was recorded."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare.reasoning import read_trace  # noqa: E402
from deepcompare.report import compare  # noqa: E402
from deepcompare.timing import WASTE_LABEL, compare_timing, time_attribution  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402

DEMO = ROOT / "demo" / "traces"


class TimeAttributionTest(unittest.TestCase):
    def setUp(self):
        self.a = Trajectory.from_json(DEMO / "t05_flight_duration__atlas-v2.json")
        self.b = Trajectory.from_json(DEMO / "t05_flight_duration__bolt-v3.json")

    def test_shares_sum_to_the_recorded_total_and_every_step_is_categorised(self):
        t = time_attribution(self.b, read_trace(self.b))
        self.assertTrue(t["measurable"])
        self.assertAlmostEqual(t["total_s"], sum(s.latency_s for s in self.b.steps), places=3)
        self.assertAlmostEqual(sum(c["seconds"] for c in t["by_category"].values()), t["total_s"], places=3)
        self.assertEqual(len(t["steps"]), len(self.b.steps))
        for row in t["steps"]:
            self.assertIn(row["category"], ("think", "tool", "answer"))
            st = next(s for s in self.b.steps if s.index == row["index"])
            self.assertEqual(row["category"], "answer" if st.type == "answer" else "tool" if st.type in ("tool_call", "search", "retrieve", "read") else "think")
        self.assertAlmostEqual(sum(v["seconds"] for v in t["by_tool"].values()), t["by_category"]["tool"]["seconds"], places=3)

    def test_wasted_steps_come_from_the_reading_and_the_rationale_quotes_them(self):
        reading = read_trace(self.b)
        t = time_attribution(self.b, reading)
        spent = set(reading["answer_basis"]["spent_steps"])
        dead = {w["step"] for w in reading["what_happened"] if w["role"] == "dead_end"}
        wasted = {r["index"]: r["wasted"] for r in t["steps"] if r["wasted"]}
        for i in dead:
            self.assertEqual(wasted.get(i), "dead_end")
        for i in spent - dead:
            self.assertIn(wasted.get(i), ("after_basis", "no_information", "repeat", "error"))
        self.assertAlmostEqual(t["wasted_s"], sum(r["latency_s"] for r in t["steps"] if r["wasted"]), places=3)
        self.assertIn(f"{t['wasted_s']:.1f}s", t["rationale"])
        for kind in set(wasted.values()):
            self.assertIn(WASTE_LABEL[kind], t["rationale"])
        self.assertIn(self.b.agent.name, t["rationale"])

    def test_no_latencies_is_unmeasurable_not_fast(self):
        d = self.b.to_dict()
        for s in d["steps"]:
            s["latency_s"] = 0.0
        t = time_attribution(Trajectory.from_dict(d))
        self.assertFalse(t["measurable"])
        self.assertIn("unmeasurable", t["reason"])
        self.assertIn("cannot be attributed", t["rationale"])

    def test_the_pair_narrative_names_the_slower_run_and_the_gap(self):
        rep = compare(self.a, self.b)
        tm = rep["timing"]
        slower = self.a if tm["a"]["total_s"] >= tm["b"]["total_s"] else self.b
        self.assertTrue(tm["narrative"].startswith(slower.agent.name + " took "))
        gap = abs(tm["a"]["total_s"] - tm["b"]["total_s"])
        self.assertIn(f"{gap:.1f}s longer", tm["narrative"])
        self.assertAlmostEqual(tm["delta"]["total_s"], tm["a"]["total_s"] - tm["b"]["total_s"], places=3)
        ct = compare_timing(self.a, self.b, rep["reading"])
        self.assertEqual(ct["narrative"], tm["narrative"])


if __name__ == "__main__":
    unittest.main()
