"""Tests for deriving real model telemetry from logprobs (v19).

This is the path that makes the "did the model know?" analysis run on real
data instead of synthetic demo numbers, and open-weight servers are its best
source because they return full logprobs. The tests pin the honesty
properties: never invent a confidence, label how entropy was estimated, and
never attach a turn's confidence to the wrong step.
"""

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare import Trajectory
from deepcompare.logprobs import (
    attach_telemetry,
    confidence_interval,
    extract_logprobs,
    telemetry_from_logprobs,
)
from deepcompare.registry import convert, detect_format

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestTelemetryMath(unittest.TestCase):
    def test_confidence_is_the_mean_token_probability(self):
        entries = [{"logprob": math.log(0.9)}, {"logprob": math.log(0.5)}]
        block = telemetry_from_logprobs(entries)
        self.assertAlmostEqual(block["confidence"], 0.7, places=3)
        self.assertAlmostEqual(block["min_token_confidence"], 0.5, places=3)
        self.assertEqual(block["tokens_scored"], 2)

    def test_top_k_gives_an_exact_entropy_basis(self):
        entries = [{
            "logprob": math.log(0.6),
            "top_logprobs": [{"logprob": math.log(0.6)},
                             {"logprob": math.log(0.3)},
                             {"logprob": math.log(0.1)}],
        }]
        block = telemetry_from_logprobs(entries)
        self.assertEqual(block["entropy_basis"], "top_k")
        self.assertGreater(block["entropy"], 0.0)

    def test_without_top_k_the_basis_is_labelled_a_floor(self):
        block = telemetry_from_logprobs([{"logprob": math.log(0.6)}])
        self.assertEqual(block["entropy_basis"], "binary_floor")

    def test_low_confidence_tokens_are_counted(self):
        entries = [{"logprob": math.log(0.9)}, {"logprob": math.log(0.2)},
                   {"logprob": math.log(0.3)}]
        self.assertEqual(telemetry_from_logprobs(entries)["low_confidence_tokens"], 2)

    def test_no_logprobs_returns_none_rather_than_a_guess(self):
        self.assertIsNone(telemetry_from_logprobs([]))
        self.assertIsNone(telemetry_from_logprobs([{"token": "a"}]))
        self.assertIsNone(telemetry_from_logprobs(None))

    def test_probabilities_stay_in_range(self):
        for logprob in (-0.0, -0.5, -20.0, -1000.0):
            block = telemetry_from_logprobs([{"logprob": logprob}])
            self.assertGreaterEqual(block["confidence"], 0.0)
            self.assertLessEqual(block["confidence"], 1.0)

    def test_bare_float_and_prob_forms_accepted(self):
        self.assertIsNotNone(telemetry_from_logprobs([math.log(0.8)]))
        self.assertAlmostEqual(
            telemetry_from_logprobs([{"prob": 0.75}])["confidence"], 0.75, places=3)

    def test_temperature_recorded_when_known(self):
        block = telemetry_from_logprobs([{"logprob": -0.1}], temperature=0.7)
        self.assertEqual(block["temperature"], 0.7)
        self.assertNotIn("temperature", telemetry_from_logprobs([{"logprob": -0.1}]))

    def test_deterministic(self):
        entries = [{"logprob": -0.2}, {"logprob": -1.1}]
        self.assertEqual(telemetry_from_logprobs(entries),
                         telemetry_from_logprobs(entries))


class TestConfidenceInterval(unittest.TestCase):
    def test_interval_brackets_the_mean_and_states_its_basis(self):
        probs = [0.9, 0.8, 0.95, 0.7, 0.85]
        band = confidence_interval(probs)
        mean = sum(probs) / len(probs)
        self.assertLess(band["low"], mean)
        self.assertGreater(band["high"], mean)
        self.assertEqual(band["n"], 5)
        self.assertIn("95%", band["basis"])
        self.assertIn("scored tokens", band["basis"])

    def test_fewer_than_three_tokens_is_no_interval(self):
        self.assertIsNone(confidence_interval([]))
        self.assertIsNone(confidence_interval([0.9]))
        self.assertIsNone(confidence_interval([0.9, 0.5]))

    def test_interval_is_clamped_to_probabilities(self):
        band = confidence_interval([0.999, 0.998, 0.997, 0.2])
        self.assertGreaterEqual(band["low"], 0.0)
        self.assertLessEqual(band["high"], 1.0)

    def test_identical_tokens_give_a_zero_width_band(self):
        band = confidence_interval([0.6, 0.6, 0.6, 0.6])
        self.assertAlmostEqual(band["low"], 0.6, places=4)
        self.assertAlmostEqual(band["high"], 0.6, places=4)

    def test_telemetry_block_carries_the_interval(self):
        entries = [{"logprob": math.log(p)} for p in (0.9, 0.5, 0.7, 0.8)]
        block = telemetry_from_logprobs(entries)
        self.assertEqual(block["interval"]["n"], 4)
        self.assertLessEqual(block["interval"]["low"], block["confidence"])
        self.assertLessEqual(block["confidence"], block["interval"]["high"])
        short = telemetry_from_logprobs([{"logprob": math.log(0.9)}, {"logprob": math.log(0.5)}])
        self.assertIsNone(short["interval"])


class TestExtraction(unittest.TestCase):
    def test_openai_vllm_choices_layout(self):
        payload = {"choices": [{"logprobs": {"content": [{"logprob": -0.1}]}}]}
        self.assertEqual(len(extract_logprobs(payload)), 1)

    def test_bare_logprobs_content(self):
        self.assertEqual(
            len(extract_logprobs({"logprobs": {"content": [{"logprob": -0.2}]}})), 1)

    def test_tgi_details_tokens(self):
        payload = {"details": {"tokens": [{"logprob": -0.3}, {"logprob": -0.4}]}}
        self.assertEqual(len(extract_logprobs(payload)), 2)

    def test_plain_list(self):
        self.assertEqual(len(extract_logprobs([{"logprob": -0.1}])), 1)

    def test_absent_returns_none(self):
        self.assertIsNone(extract_logprobs({"choices": [{}]}))
        self.assertIsNone(extract_logprobs({}))
        self.assertIsNone(extract_logprobs(None))

    def test_attach_is_a_no_op_without_logprobs(self):
        step = {"index": 0, "type": "answer", "name": "final",
                "input": "", "output": "", "tokens": 0, "latency_s": 0.0,
                "quality": None, "note": None}
        attach_telemetry(step, {"choices": [{}]})
        self.assertNotIn("model", step)


class TestOpenWeightFixture(unittest.TestCase):
    """The shipped open-weight sample must convert with real telemetry."""

    def payload(self):
        return json.loads(
            (FIXTURES / "ollama_openweight_sample.json").read_text(encoding="utf-8"))

    def test_detected_as_ollama(self):
        self.assertEqual(detect_format(self.payload())["best"], "ollama")

    def test_converts_and_validates(self):
        result = convert(self.payload())
        Trajectory.from_json(result["trajectory"])
        self.assertEqual(result["format"], "ollama")

    def test_open_weight_model_identity_survives(self):
        trajectory = convert(self.payload())["trajectory"]
        self.assertEqual(trajectory["agent"]["model"], "llama-4-70b-instruct")

    def test_every_generated_step_carries_real_telemetry(self):
        trajectory = convert(self.payload())["trajectory"]
        scored = [s for s in trajectory["steps"] if s.get("model")]
        self.assertEqual(len(scored), len(trajectory["steps"]))
        for step in scored:
            self.assertIn(step["model"]["source"], ("ollama-logprobs",))
            self.assertGreater(step["model"]["confidence"], 0.0)

    def test_telemetry_is_matched_by_content_not_position(self):
        # The tool-result turn produces no step; a positional zip would put
        # the answer's confidence on the wrong step (or drop it).
        trajectory = convert(self.payload())["trajectory"]
        answer = trajectory["steps"][-1]
        self.assertEqual(answer["type"], "answer")
        self.assertIsNotNone(answer.get("model"))
        # The answer's own tokens include the ".82" hesitation, so its
        # weakest token must be lower than the confident search step's.
        search = next(s for s in trajectory["steps"] if s["type"] == "search")
        self.assertLess(answer["model"]["min_token_confidence"],
                        search["model"]["min_token_confidence"])

    def test_the_run_arrives_with_real_usage_and_timing(self):
        # Estimated tokens and zero latency convert cleanly and then make
        # every token- and latency-denominated comparison meaningless.
        trajectory = convert(self.payload())["trajectory"]
        self.assertEqual([s["tokens"] for s in trajectory["steps"]], [48, 36, 64])
        self.assertTrue(all(s["latency_s"] > 0 for s in trajectory["steps"]))
        self.assertEqual(trajectory["totals"]["input_tokens"], 670)

    def test_the_retrieved_evidence_survives_conversion(self):
        trajectory = convert(self.payload())["trajectory"]
        search = next(s for s in trajectory["steps"] if s["type"] == "search")
        self.assertIn("acmecorp.com", search["output"])

    def test_uncertainty_analysis_runs_on_the_converted_run(self):
        from deepcompare.uncertainty import has_telemetry
        trajectory = convert(self.payload())["trajectory"]
        self.assertTrue(has_telemetry(Trajectory.from_json(trajectory)))

    def test_missing_logprobs_produce_a_warning_not_silence(self):
        payload = self.payload()
        for turn in payload["turns"]:
            turn.pop("logprobs", None)
        result = convert(payload)
        self.assertTrue(any("logprobs" in w for w in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
