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


class TimelineBasisTest(unittest.TestCase):
    """Is a run's clock read, or assumed?

    A trace that records only `latency_s` leaves one way to place a step on
    a clock: sum the durations before it. That is not a neutral
    convenience — it asserts the run was sequential, and for a loop that
    executes independent calls concurrently it is simply wrong with nothing
    in the record to say so. Every timeline this repository draws made that
    assumption silently until `Step.started_s` existed.

    So the basis is stated, and `overlap_s` is `None` rather than `0.0`
    when the starts are unrecorded: a run whose concurrency nothing wrote
    down is not a run that had none.
    """

    @staticmethod
    def _traj(rows):
        steps = []
        for i, row in enumerate(rows):
            step = {"index": i, "type": row.get("type", "tool_call"), "name": row.get("name", "t"),
                    "input": "", "output": "", "tokens": 1, "latency_s": row["latency_s"]}
            if "started_s" in row:
                step["started_s"] = row["started_s"]
            steps.append(step)
        steps[-1]["type"] = "answer"
        return Trajectory.from_dict({
            "trace_id": "x", "agent": {"name": "a"}, "task": {"id": "t", "prompt": "p"},
            "totals": {"latency_s": sum(r["latency_s"] for r in rows)},
            "outcome": {"answer": "done", "success": True, "termination": "agent_stop"},
            "steps": steps})

    def test_a_run_with_no_recorded_starts_says_its_clock_is_assumed(self):
        t = self._traj([{"latency_s": 1.0}, {"latency_s": 2.0}, {"latency_s": 0.5}])
        tl = time_attribution(t)["timeline"]
        self.assertFalse(tl["measurable"])
        self.assertEqual(tl["basis"], "reconstructed")
        self.assertEqual(tl["recorded_starts"], 0)
        self.assertIsNone(tl["overlap_s"], "a run whose concurrency nothing recorded is not one that had none")
        self.assertIn("reads the run as strictly sequential", tl["reading"])
        self.assertIn("Nothing here says it was", tl["reading"])

    def test_a_sequential_run_with_recorded_starts_reads_as_recorded_and_zero_overlap(self):
        t = self._traj([{"latency_s": 1.0, "started_s": 0.0},
                        {"latency_s": 2.0, "started_s": 1.0},
                        {"latency_s": 0.5, "started_s": 3.0}])
        tl = time_attribution(t)["timeline"]
        self.assertTrue(tl["measurable"])
        self.assertEqual(tl["basis"], "recorded")
        self.assertEqual(tl["span_s"], 3.5)
        self.assertEqual(tl["sum_s"], 3.5)
        self.assertEqual(tl["overlap_s"], 0.0)
        self.assertEqual(tl["concurrent_steps"], 0)
        self.assertIn("really was sequential", tl["reading"])

    def test_a_concurrent_run_is_measurable_where_a_running_sum_would_have_lied(self):
        """Two calls that overlap: the sum of the durations is 3.0s and the
        run took 2.0s. A reconstructed clock would have drawn it as 3.0s
        and placed the third step a second late."""
        t = self._traj([{"latency_s": 1.0, "started_s": 0.0},
                        {"latency_s": 1.0, "started_s": 0.5},
                        {"latency_s": 1.0, "started_s": 1.0}])
        tl = time_attribution(t)["timeline"]
        self.assertTrue(tl["measurable"])
        self.assertEqual(tl["sum_s"], 3.0)
        self.assertEqual(tl["span_s"], 2.0)
        self.assertEqual(tl["overlap_s"], 1.0)
        self.assertEqual(tl["concurrent_steps"], 2)
        self.assertIn("two or more steps running at once", tl["reading"])

    def test_a_partly_recorded_run_is_reconstructed_rather_than_mixed(self):
        """All or nothing: placing some steps on a clock and some on a
        running sum is two pictures drawn over each other, and the result
        would not say which was which."""
        t = self._traj([{"latency_s": 1.0, "started_s": 0.0},
                        {"latency_s": 2.0},
                        {"latency_s": 0.5, "started_s": 3.0}])
        tl = time_attribution(t)["timeline"]
        self.assertFalse(tl["measurable"])
        self.assertEqual(tl["basis"], "reconstructed")
        self.assertEqual(tl["recorded_starts"], 2)
        self.assertIn("only 2 of 3 steps", tl["reason"])

    def test_the_recorder_records_when_each_step_began(self):
        import time as _time

        from deepcompare.record import Recorder
        with Recorder(task="t", prompt="p", agent="a", model="m", expected="x", out_dir=None) as run:
            run.reason("first")
            _time.sleep(0.02)
            run.reason("second")
            run.answer("x", success=True)
        data = run.to_dict()
        starts = [s.get("started_s") for s in data["steps"]]
        self.assertTrue(all(isinstance(v, (int, float)) for v in starts), starts)
        self.assertEqual(starts, sorted(starts), "a step began before the one before it")
        self.assertAlmostEqual(starts[0], 0.0, places=3)
        tl = time_attribution(Trajectory.from_dict(data))["timeline"]
        self.assertEqual(tl["basis"], "recorded")
        self.assertEqual(tl["overlap_s"], 0.0)

    def test_an_unrecorded_start_is_absent_and_not_null(self):
        """Adding a null to every step of every stored trace would rewrite
        every artifact here to say nothing."""
        t = self._traj([{"latency_s": 1.0}, {"latency_s": 1.0}])
        for step in t.to_dict()["steps"]:
            self.assertNotIn("started_s", step)
        with_start = self._traj([{"latency_s": 1.0, "started_s": 0.25}, {"latency_s": 1.0, "started_s": 1.25}])
        self.assertEqual([s["started_s"] for s in with_start.to_dict()["steps"]], [0.25, 1.25])

    def test_a_negative_start_is_refused(self):
        with self.assertRaises(ValueError):
            self._traj([{"latency_s": 1.0, "started_s": -1.0}, {"latency_s": 1.0, "started_s": 0.0}])
