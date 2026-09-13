"""Model internals as attribution evidence.

The harness can stamp each step with the SAE features its text activates
(Neuronpedia, or a scripted table offline); the engine diffs those features
across the aligned runs and, at the decisive step, reports the features that
fired only on the failing side.  These tests pin the contract: the analysis
is read from the trace as written, it never changes a score, a synthetic
source is labelled synthetic everywhere it surfaces, and an activation
difference is reported as an observation rather than a cause.
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory, compare
from deepcompare.harness.neuronpedia import (
    Neuronpedia,
    ScriptedNeuronpedia,
    attach_internals,
    feature_url,
)
from deepcompare.internals import internals_analysis

ROOT = Path(__file__).resolve().parent.parent
TELEMETRY = ROOT / "demo" / "telemetry" / "traces"
PLAIN = ROOT / "demo" / "traces"


def _load(path: Path) -> Trajectory:
    return Trajectory.from_dict(json.loads(path.read_text()))


class ScriptedClientTest(unittest.TestCase):
    TABLE = {
        "11:45": [{"index": 12480, "activation": 9.5, "max_activation": 12.0,
                   "label": "unit / time-zone confusion"}],
        "*": [{"index": 7332, "activation": 4.0, "label": "arithmetic on units"},
              {"index": 6021, "activation": 6.0, "label": "table extraction"}],
    }

    def test_exact_then_substring_then_wildcard(self):
        client = ScriptedNeuronpedia(self.TABLE, model="gemma-2-2b", source="20-gemmascope-res-16k")
        hit = client.activations("6:40 + 2:15 + 2:50 = 11:45")
        self.assertEqual([f["index"] for f in hit], [12480])
        fallback = client.activations("nothing scripted here")
        self.assertEqual([f["index"] for f in fallback], [6021, 7332],
                         "rows are sorted by activation, strongest first")

    def test_rows_carry_a_dashboard_url_for_the_model_and_sae(self):
        client = ScriptedNeuronpedia(self.TABLE, model="gemma-2-2b", source="20-gemmascope-res-16k")
        row = client.activations("11:45")[0]
        self.assertEqual(row["url"], feature_url("gemma-2-2b", "20-gemmascope-res-16k", 12480))
        self.assertIn("neuronpedia.org/gemma-2-2b/20-gemmascope-res-16k/12480", row["url"])

    def test_attach_writes_the_step_internals_block(self):
        client = ScriptedNeuronpedia(self.TABLE, model="m", source="s")
        step = {"index": 3, "type": "tool_call", "output": "... = 11:45", "model": {"confidence": 0.9}}
        block = attach_internals(step, client)
        self.assertIs(step["model"]["internals"], block)
        self.assertEqual(step["model"]["confidence"], 0.9, "existing telemetry is kept")
        self.assertEqual(block["model"], "m")
        self.assertEqual(block["sae"], "s")
        self.assertEqual(block["source"], "neuronpedia")
        self.assertEqual(block["features"][0]["index"], 12480)
        self.assertIn("observation, not a cause", block["note"])

    def test_attach_prefers_output_then_input_then_explicit_text(self):
        client = ScriptedNeuronpedia(self.TABLE)
        self.assertEqual(attach_internals({"input": "11:45"}, client)["features"][0]["index"], 12480)
        self.assertEqual(attach_internals({"input": "x", "output": "11:45"}, client)["features"][0]["index"], 12480)
        self.assertEqual(attach_internals({"output": "11:45"}, client, text="other")["features"][0]["index"], 6021)

    def test_the_real_client_reads_its_key_from_the_environment_only(self):
        import os
        client = Neuronpedia("gemma-2-2b", "20-gemmascope-res-16k", api_key_env="AGENTDIFF_TEST_NP_KEY")
        self.assertEqual(client.api_key_env, "AGENTDIFF_TEST_NP_KEY")
        self.assertNotIn("AGENTDIFF_TEST_NP_KEY", os.environ)
        request = client._build("GET", "/feature/gemma-2-2b/20-gemmascope-res-16k/1")
        self.assertNotIn("X-api-key", request.headers)
        self.assertTrue(request.full_url.startswith("https://www.neuronpedia.org/api/feature/"))
        os.environ["AGENTDIFF_TEST_NP_KEY"] = "not-a-real-key"
        try:
            request = client._build("POST", "/search-all", {"text": "x"})
            self.assertEqual(request.get_header("X-api-key"), "not-a-real-key")
        finally:
            del os.environ["AGENTDIFF_TEST_NP_KEY"]

    def test_normalise_accepts_neuronpedia_field_names(self):
        client = ScriptedNeuronpedia({"*": [
            {"featureIndex": 5, "maxValue": 3.25, "maxActApprox": 8.0,
             "explanations": [{"description": "from the API"}]},
            {"index": None},
            "not a row",
        ]})
        rows = client.activations("anything")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["index"], 5)
        self.assertEqual(rows[0]["activation"], 3.25)
        self.assertEqual(rows[0]["max_activation"], 8.0)
        self.assertEqual(rows[0]["label"], "from the API")


class AnalysisOnSyntheticDemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = _load(TELEMETRY / "t05_flight_duration__atlas-v2.json")
        cls.b = _load(TELEMETRY / "t05_flight_duration__bolt-v3.json")
        cls.report = compare(cls.a, cls.b)

    def test_section_is_present_and_labelled_synthetic(self):
        it = self.report["internals"]
        self.assertTrue(it["available"])
        self.assertTrue(it["synthetic"])
        self.assertTrue(it["note"].startswith("SYNTHETIC"))
        self.assertTrue(it["rows"])

    def test_decisive_step_reports_the_exclusive_feature_on_the_failing_side(self):
        dec = self.report["internals"]["decisive"]
        self.assertIsNotNone(dec)
        self.assertEqual(dec["side"], self.report["diagnosis"]["subject"])
        self.assertEqual(dec["step"], self.report["diagnosis"]["decisive_step"]["step"])
        labels = [f["label"] for f in dec["exclusive_features"]]
        self.assertTrue(any("time-zone" in label for label in labels), labels)
        self.assertTrue(all("(synthetic label)" in label for label in labels))
        self.assertIn("observation", dec["note"])
        self.assertIn("steering", dec["note"])

    def test_the_signature_is_cited_as_evidence_without_moving_the_score(self):
        plain = compare(_load(PLAIN / "t05_flight_duration__atlas-v2.json"),
                        _load(PLAIN / "t05_flight_duration__bolt-v3.json"))
        with_internals = self.report
        lead_plain = plain["diagnosis"]["hypotheses"][0]
        lead = with_internals["diagnosis"]["hypotheses"][0]
        self.assertEqual(lead["kind"], lead_plain["kind"])
        self.assertEqual(lead["score"], lead_plain["score"],
                         "internals are evidence for the reader, never a scoring input")
        cited = [e for e in with_internals["diagnosis"]["evidence"]
                 if e.get("path") == "internals.decisive.exclusive_features"]
        self.assertEqual(len(cited), 1)
        self.assertIn(cited[0]["id"], lead["supports"])
        self.assertIn("SYNTHETIC", cited[0]["basis"])
        self.assertEqual(cited[0]["evidence_class"], "observable")
        self.assertTrue(lead.get("internal_signature"))

    def test_interval_series_rides_on_uncertainty(self):
        u = self.report["uncertainty"]
        for side in ("a", "b"):
            bands = u[side]["interval"]
            self.assertEqual(len(bands), len(u[side]["series"]))
            for band, value in zip(bands, u[side]["series"]):
                if value is None:
                    self.assertIsNone(band)
                else:
                    self.assertLessEqual(band[0], value)
                    self.assertLessEqual(value, band[1])
            self.assertTrue(all("SYNTHETIC" in basis for basis in u[side]["interval_basis"]))

    def test_shared_feature_rows_carry_signed_deltas(self):
        for row in self.report["internals"]["rows"]:
            for shared in row["shared"]:
                self.assertAlmostEqual(shared["delta_b_minus_a"],
                                       round(shared["activation_b"] - shared["activation_a"], 3), places=3)


class AnalysisContractTest(unittest.TestCase):
    def test_without_internals_the_section_says_so(self):
        a = _load(PLAIN / "t05_flight_duration__atlas-v2.json")
        b = _load(PLAIN / "t05_flight_duration__bolt-v3.json")
        report = compare(a, b)
        self.assertFalse(report["internals"]["available"])
        self.assertIn("no step carries model internals", report["internals"]["reason"])
        self.assertFalse([e for e in report["diagnosis"]["evidence"]
                          if e.get("path") == "internals.decisive.exclusive_features"])

    def test_scripted_internals_on_a_real_pair_are_not_synthetic(self):
        a = json.loads((PLAIN / "t05_flight_duration__atlas-v2.json").read_text())
        b = json.loads((PLAIN / "t05_flight_duration__bolt-v3.json").read_text())
        client = ScriptedNeuronpedia({
            "11:45": [{"index": 12480, "activation": 9.5, "label": "unit / time-zone confusion"}],
            "*": [{"index": 7332, "activation": 4.0, "label": "arithmetic on units"}],
        }, model="gemma-2-2b", source="20-gemmascope-res-16k")
        for trace in (a, b):
            for step in trace["steps"]:
                attach_internals(step, client)
        report = compare(Trajectory.from_dict(a), Trajectory.from_dict(b))
        it = report["internals"]
        self.assertTrue(it["available"])
        self.assertFalse(it["synthetic"])
        self.assertEqual(it["provenance"]["model"], "gemma-2-2b")
        self.assertEqual(it["provenance"]["sae"], "20-gemmascope-res-16k")
        dec = it["decisive"]
        self.assertEqual([f["index"] for f in dec["exclusive_features"]], [12480])
        self.assertTrue(dec["exclusive_features"][0]["url"].startswith("https://www.neuronpedia.org/"))
        cited = [e for e in report["diagnosis"]["evidence"]
                 if e.get("path") == "internals.decisive.exclusive_features"][0]
        self.assertNotIn("SYNTHETIC", cited["basis"])

    def test_analysis_is_deterministic_and_does_not_mutate_its_inputs(self):
        a = _load(TELEMETRY / "t05_flight_duration__atlas-v2.json")
        b = _load(TELEMETRY / "t05_flight_duration__bolt-v3.json")
        report = compare(a, b)
        before = copy.deepcopy(report)
        again = internals_analysis(report, a, b)
        self.assertEqual(again, before["internals"])
        self.assertEqual(report, before)


if __name__ == "__main__":
    unittest.main()
