"""The Who&When check's replay sets: every step of a converted log in the reading's order, and the held-out
coverage of that order against plain step order, written beside the pointer scores."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentdiff.reasoning import read_trace  # noqa: E402
from agentdiff.trace import Trajectory  # noqa: E402

spec = importlib.util.spec_from_file_location("whowhen", ROOT / "demo" / "external" / "whowhen.py")
whowhen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(whowhen)


def _log(i: int) -> dict:
    history = [{"name": "Orchestrator", "content": "Plan: find the population, verify it, then answer."},
               {"name": "WebSurfer", "content": f"Searched; the page says {1000 + i} residents."},
               {"name": "Coder", "content": "exitcode: 1 Traceback (most recent call last): KeyError"},
               {"name": "Assistant", "content": f"The population is {2000 + i}."}]
    return {"history": history, "question": "What is the population?", "ground_truth": str(1000 + i),
            "question_ID": f"q{i}", "mistake_step": i % 3, "mistake_agent": history[i % 3]["name"]}


class ReplayOrderTest(unittest.TestCase):
    def test_every_step_once(self):
        trace, _ = whowhen.convert(_log(1), "t1")
        traj = Trajectory.from_dict(trace)
        order = whowhen.reading_order(read_trace(traj), traj)
        self.assertEqual(sorted(order), list(range(len(traj.steps))))

    def test_the_validation_reports_replay_sets_against_step_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data" / "Hand-Crafted"
            data.mkdir(parents=True)
            for i in range(60):
                (data / f"{i}.json").write_text(json.dumps(_log(i)))
            with redirect_stdout(io.StringIO()) as out:
                whowhen.main(["--data", str(Path(tmp) / "data"), "-o", str(Path(tmp) / "out")])
            result = json.loads((Path(tmp) / "out" / "whowhen_validation.json").read_text())
        self.assertEqual([e["alpha"] for e in result["replay_sets"]], [0.5, 0.2, 0.1])
        for e in result["replay_sets"]:
            self.assertEqual(set(e), {"alpha", "target", "mean_steps", "replay_order", "step_order"})
        self.assertIn("replay set, 90% target", out.getvalue())


if __name__ == "__main__":
    unittest.main()
