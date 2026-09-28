"""Lessons: learned from a corpus, tested on the half they were not drawn from,
and carried to the next corpus in a ledger that confirms or retires them."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentdiff import lessons
from agentdiff.commands._io import load_traces
from agentdiff.scorecard import load_golden

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "demo" / "horizon" / "suite"
GOLDEN = ROOT / "demo" / "horizon" / "suite_golden.json"


def _suite():
    if not SUITE.is_dir():
        raise unittest.SkipTest("the long-horizon suite is not generated")
    return load_traces(SUITE), load_golden(GOLDEN)


class SplitTest(unittest.TestCase):
    def test_the_split_is_by_task_fixed_and_never_leaves_a_half_empty(self):
        ids = [f"t{i}" for i in range(10)]
        a = lessons.split(ids)
        self.assertEqual(a, lessons.split(list(reversed(ids)) + ids))
        self.assertTrue(a[0] and a[1])
        self.assertEqual(sorted(a[0] + a[1]), sorted(ids))
        self.assertFalse(set(a[0]) & set(a[1]))
        for pair in (["x", "y"], ["only-one-hash-side-a", "only-one-hash-side-b"]):
            halves = lessons.split(pair)
            self.assertEqual(sorted(halves[0] + halves[1]), sorted(pair))
            self.assertTrue(halves[0] and halves[1], pair)

    def test_a_corpus_of_one_task_has_nothing_to_test_on(self):
        traces, gold = _suite()
        one = [t for t in traces if t.task.id == traces[0].task.id]
        out = lessons.learn(one, gold)
        self.assertFalse(out["measurable"])
        self.assertIn("second half", out["reason"])
        self.assertEqual(out["lessons"], [])


class LearnTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.traces, cls.gold = _suite()
        cls.out = lessons.learn(cls.traces, cls.gold, cls.gold.get("policy"))

    def test_wrong_is_the_golden_label_where_there_is_one(self):
        out = self.out
        self.assertEqual(out["runs"], len(self.traces))
        self.assertEqual(out["target"], {"golden": len(self.traces), "outcome": 0})
        # the golden set names twelve runs as carrying a known failure;
        # five of those were graded a failure, so the outcome alone says five
        self.assertEqual(out["wrong"], 12)
        graded = sum(1 for t in self.traces if not t.outcome.success)
        self.assertNotEqual(out["wrong"], graded)

    def test_without_a_golden_set_wrong_is_the_run_own_outcome(self):
        out = lessons.learn(self.traces)
        self.assertEqual(out["target"], {"golden": 0, "outcome": len(self.traces)})
        self.assertEqual(out["wrong"], sum(1 for t in self.traces if not t.outcome.success))

    def test_every_candidate_is_tried_and_the_count_is_stated(self):
        self.assertEqual(self.out["tried"], len(lessons.candidates()))
        self.assertIn(f"{self.out['tried']} properties were tried", self.out["narrative"])

    def test_a_lesson_holds_only_when_both_halves_agree(self):
        for lesson in self.out["lessons"]:
            h = lesson["halves"]
            if lesson["status"] == "held":
                self.assertTrue(h[0]["measurable"] and h[1]["measurable"], lesson["name"])
                self.assertEqual(h[0]["effect"] > 0, h[1]["effect"] > 0, lesson["name"])
                self.assertTrue(all(abs(x["effect"]) >= lessons.HOLDS for x in h), lesson["name"])
            else:
                self.assertIn(lesson["status"], ("did_not_hold", "reversed", "untested"))
            # a lesson was learned on at least one half, or it is not listed
            self.assertTrue(lesson["learned_on"], lesson["name"])

    def test_an_annotation_is_listed_apart_and_never_leads(self):
        names = {l["name"]: l for l in self.out["lessons"]}
        ann = names.get("annotation:poor_quality_step")
        self.assertIsNotNone(ann, "the suite annotates its bad steps, and that separates perfectly")
        self.assertEqual(ann["source"], "annotation")
        self.assertEqual(ann["status"], "held")
        # it held, and the narrative still says no behaviour is a lesson yet
        self.assertIn("Only a trace annotation held", self.out["narrative"])
        self.assertNotIn("The best supported", self.out["narrative"])

    def test_a_small_lesson_is_reported_as_too_few_not_as_held(self):
        names = {l["name"]: l for l in self.out["lessons"]}
        err = names["sign:unrecovered_error"]
        self.assertEqual(err["status"], "untested")
        self.assertFalse(all(h["measurable"] for h in err["halves"]))
        self.assertEqual(err["with"], {"runs": 4, "wrong": 4})

    def test_the_sentence_carries_the_counts_behind_it(self):
        for lesson in self.out["lessons"]:
            w, o = lesson["with"], lesson["without"]
            self.assertIn(f"{w['wrong']} of {w['runs']} wrong with it", lesson["sentence"])
            self.assertIn(f"{o['wrong']} of {o['runs']} without", lesson["sentence"])
            if lesson["within_task"]:
                self.assertIn(f"of {lesson['strata']} task", lesson["sentence"])

    def test_learning_is_deterministic(self):
        again = lessons.learn(self.traces, self.gold, self.gold.get("policy"))
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.out, sort_keys=True))


class LedgerTest(unittest.TestCase):
    """Two corpora from the suite: the tasks of each half, read as separate
    batches, so a lesson learned on one is re-tested on runs it never saw."""

    @classmethod
    def setUpClass(cls):
        cls.traces, cls.gold = _suite()
        halves = lessons.split(t.task.id for t in cls.traces)
        cls.first = [t for t in cls.traces if t.task.id in halves[0]]
        cls.second = [t for t in cls.traces if t.task.id in halves[1]]

    def test_a_fresh_ledger_records_the_corpus_and_its_held_lessons(self):
        out = lessons.learn(self.traces, self.gold)
        nxt = out["ledger"]["next"]
        self.assertFalse(out["ledger"]["had_prior"])
        self.assertEqual(nxt["corpora"], [lessons.fingerprint(self.traces)])
        held = sorted(l["name"] for l in out["lessons"] if l["status"] == "held")
        self.assertEqual(sorted(nxt["lessons"]), held)
        for name in held:
            self.assertEqual(nxt["lessons"][name]["history"][0]["status"], "learned")

    def test_reading_the_same_corpus_twice_is_not_new_evidence(self):
        first = lessons.learn(self.traces, self.gold)
        second = lessons.learn(self.traces, self.gold, ledger=first["ledger"]["next"])
        self.assertTrue(second["ledger"]["seen_before"])
        self.assertEqual(second["ledger"]["rechecked"], [])
        self.assertEqual(second["ledger"]["next"], first["ledger"]["next"])
        self.assertIn("not counted as new evidence", second["narrative"])

    def test_an_earlier_lesson_is_re_tested_on_the_next_corpus(self):
        seed = {"version": lessons.LEDGER_VERSION, "corpora": ["earlier"], "lessons": {
            "sign:unrecovered_error": {"phrasing": "x", "source": "trace sign", "direction": "more",
                                       "learned_from": "earlier", "history": [
                                           {"corpus": "earlier", "effect": 1.0, "status": "learned", "runs": 9}]},
            "sign:cycle": {"phrasing": "y", "source": "trace sign", "direction": "more",
                           "learned_from": "earlier", "history": []},
            "behaviour:gone": {"phrasing": "z", "direction": "more", "history": []},
        }}
        out = lessons.learn(self.traces, self.gold, ledger=seed)
        got = {r["name"]: r for r in out["ledger"]["rechecked"]}
        self.assertEqual(got["sign:unrecovered_error"]["status"], "held_again")
        # every run in the suite repeats a call, so there is no run without it
        self.assertEqual(got["sign:cycle"]["status"], "untestable")
        self.assertEqual(got["behaviour:gone"]["status"], "untestable")
        self.assertIn("no longer knows", got["behaviour:gone"]["why"])
        hist = out["ledger"]["next"]["lessons"]["sign:unrecovered_error"]["history"]
        self.assertEqual([h["status"] for h in hist], ["learned", "held_again"])
        self.assertEqual(out["ledger"]["by_lesson"]["sign:unrecovered_error"]["held_again"], 1)
        # the ledger passed in is not modified
        self.assertEqual(len(seed["lessons"]["sign:unrecovered_error"]["history"]), 1)
        self.assertIn("Carried in from 1 earlier corpus: 3 lessons", out["narrative"])

    def test_a_lesson_the_next_corpus_contradicts_is_marked_reversed(self):
        seed = {"version": lessons.LEDGER_VERSION, "corpora": ["earlier"], "lessons": {
            "sign:unrecovered_error": {"phrasing": "x", "direction": "less", "history": []}}}
        out = lessons.learn(self.traces, self.gold, ledger=seed)
        self.assertEqual(out["ledger"]["rechecked"][0]["status"], "reversed")

    def test_two_halves_as_two_batches(self):
        a = lessons.learn(self.first, self.gold)
        b = lessons.learn(self.second, self.gold, ledger=a["ledger"]["next"])
        self.assertNotEqual(a["corpus"], b["corpus"])
        self.assertEqual(b["ledger"]["prior_corpora"], 1)
        self.assertEqual(len(b["ledger"]["rechecked"]), len(a["ledger"]["next"]["lessons"]))
        self.assertEqual(b["ledger"]["next"]["corpora"], [a["corpus"], b["corpus"]])

    def test_a_file_that_is_not_a_ledger_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "l.json"
            self.assertIsNone(lessons.load_ledger(p))
            p.write_text(json.dumps({"version": 99}), encoding="utf-8")
            with self.assertRaises(ValueError):
                lessons.load_ledger(p)


class BatchLedgerTest(unittest.TestCase):
    def test_batch_writes_the_ledger_and_the_aggregate_carries_the_lessons(self):
        if not SUITE.is_dir():
            raise unittest.SkipTest("the long-horizon suite is not generated")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            for n in (1, 2):
                done = subprocess.run(
                    [sys.executable, "-m", "agentdiff", "batch", str(SUITE), "-o", str(Path(tmp) / f"o{n}"),
                     "--golden", str(GOLDEN), "--lessons", str(ledger)],
                    cwd=str(ROOT), capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn("Lessons:", done.stdout)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(len(data["corpora"]), 1, "the same corpus twice is one corpus")
            agg = json.loads((Path(tmp) / "o2" / "aggregate.json").read_text(encoding="utf-8"))
            self.assertTrue(agg["lessons"]["measurable"])
            self.assertTrue(agg["lessons"]["ledger"]["seen_before"])
            self.assertNotIn("next", agg["lessons"]["ledger"], "the ledger lives in its file, not the page")

    def test_a_bad_ledger_is_an_error_not_a_silent_restart(self):
        if not SUITE.is_dir():
            raise unittest.SkipTest("the long-horizon suite is not generated")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            ledger.write_text("{}", encoding="utf-8")
            done = subprocess.run(
                [sys.executable, "-m", "agentdiff", "batch", str(SUITE), "-o", str(Path(tmp) / "o"),
                 "--lessons", str(ledger)], cwd=str(ROOT), capture_output=True, text=True)
            self.assertEqual(done.returncode, 2)
            self.assertIn("not a lessons ledger", done.stderr)
            self.assertEqual(ledger.read_text(encoding="utf-8"), "{}")


if __name__ == "__main__":
    unittest.main()
