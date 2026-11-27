"""Reading GenAI spans as the OpenTelemetry conventions write them in November 2026.

The fixtures are written here from the published message and span definitions: structured messages
(``role`` and ``parts``), OTLP wire values (``kvlistValue`` inside ``arrayValue``), and the operation
names in the conventions' registry.
"""
from __future__ import annotations

import json
import unittest

from agentdiff.adapters import _genai_text, _is_agent_op, _otlp_value, from_otel_genai, from_otlp


def wire(value):
    """A plain value in OTLP/JSON AnyValue form."""
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [wire(v) for v in value]}}
    if isinstance(value, dict):
        return {"kvlistValue": {"values": [{"key": k, "value": wire(v)} for k, v in value.items()]}}
    raise TypeError(value)


ASK = [{"role": "system", "parts": [{"type": "text", "content": "You check the weather."}]},
       {"role": "user", "parts": [{"type": "text", "content": "Weather in Paris?"}]}]
CALL = [{"role": "assistant", "finish_reason": "tool_call",
         "parts": [{"type": "reasoning", "content": "I should look it up."},
                   {"type": "tool_call", "id": "c1", "name": "get_weather", "arguments": {"city": "Paris"}}]}]
ANSWER = [{"role": "assistant", "finish_reason": "stop", "parts": [{"type": "text", "content": "It is 21 degrees."}]}]


def span(op, start, **attrs):
    attrs = {"gen_ai.operation.name": op, **attrs}
    return {"name": op, "start_time_unix_nano": start, "end_time_unix_nano": start + 1, "attributes": attrs}


class ValuesTest(unittest.TestCase):
    def test_wire_values_unwrap(self):
        self.assertEqual(_otlp_value(wire(ASK)), ASK)
        self.assertEqual(_otlp_value({"intValue": "123"}), 123)
        self.assertEqual(_otlp_value({"bytesValue": "AAE="}), "AAE=")
        self.assertEqual(_otlp_value({"stringValue": "x"}), "x")
        # not a wrapper: left alone
        self.assertEqual(_otlp_value({"role": "user"}), {"role": "user"})


class MessageTextTest(unittest.TestCase):
    def test_messages_read_as_their_text(self):
        self.assertEqual(_genai_text(ASK), "You check the weather.\nWeather in Paris?")
        self.assertEqual(_genai_text(ANSWER), "It is 21 degrees.")

    def test_a_tool_call_reads_as_a_call_and_reasoning_is_left_out(self):
        self.assertEqual(_genai_text(CALL), 'get_weather({"city": "Paris"})')

    def test_a_tool_response_reads_as_its_result(self):
        msg = [{"role": "tool", "parts": [{"type": "tool_call_response", "id": "c1", "response": {"temp": 21}}]}]
        self.assertEqual(_genai_text(msg), '{"temp": 21}')

    def test_the_same_from_a_json_string_and_from_the_wire(self):
        self.assertEqual(_genai_text(json.dumps(ASK)), _genai_text(ASK))
        self.assertEqual(_genai_text(wire(ASK)), _genai_text(ASK))

    def test_other_values_are_kept(self):
        self.assertEqual(_genai_text("plain text"), "plain text")
        self.assertEqual(_genai_text("[1, 2"), "[1, 2")          # not JSON: as it is
        self.assertEqual(_genai_text('{"a": 1}'), '{"a": 1}')     # JSON, but not a message
        self.assertEqual(_genai_text({"b": 2, "a": 1}), '{"a": 1, "b": 2}')
        self.assertEqual(_genai_text(None), "")


class SpansTest(unittest.TestCase):
    def run_spans(self, spans):
        traj, warnings = from_otel_genai(spans, agent="a", task="t")
        return traj["steps"], warnings

    def test_a_run_in_the_message_format(self):
        steps, _ = self.run_spans([
            span("chat", 1, **{"gen_ai.input.messages": ASK, "gen_ai.output.messages": CALL}),
            span("execute_tool", 3, **{"gen_ai.tool.name": "get_weather", "gen_ai.tool.call.arguments": {"city": "Paris"},
                                       "gen_ai.tool.call.result": {"temp": 21}}),
            span("chat", 5, **{"gen_ai.input.messages": ASK, "gen_ai.output.messages": ANSWER}),
        ])
        self.assertEqual(steps[0]["output"], 'get_weather({"city": "Paris"})')
        self.assertEqual(steps[1]["input"], '{"city": "Paris"}')
        self.assertEqual(steps[2]["type"], "answer")
        self.assertEqual(steps[2]["output"], "It is 21 degrees.")

    def test_registry_operations_become_steps(self):
        steps, warnings = self.run_spans([
            span("plan", 1, **{"gen_ai.agent.name": "planner"}),
            span("retrieval", 3, **{"gen_ai.retrieval.query.text": "paris weather",
                                    "gen_ai.retrieval.documents": [{"id": "d1", "score": 0.9}]}),
            span("search_memory", 5, **{"gen_ai.memory.query.text": "last city"}),
            span("realtime_inference", 7, **{"gen_ai.output.messages": ANSWER}),
            span("fetch_response", 9, **{"gen_ai.output.messages": ANSWER}),
            span("invoke_runner", 11, **{"gen_ai.agent.name": "runner"}),
            span("invoke_crew", 13, **{"gen_ai.agent.name": "crew"}),
            span("chat", 15, **{"gen_ai.output.messages": ANSWER}),
        ])
        got = [(s["type"], s["name"]) for s in steps]
        self.assertEqual(got[:3], [("plan", "planner"), ("retrieve", "retrieval"), ("retrieve", "search_memory")])
        self.assertEqual(steps[1]["input"], "paris weather")
        self.assertEqual(steps[1]["output"], '{"id": "d1", "score": 0.9}')
        self.assertEqual(steps[3]["type"], "reason")
        self.assertEqual(steps[3]["output"], "It is 21 degrees.")
        self.assertEqual(steps[4]["type"], "reason")
        self.assertEqual([s["name"] for s in steps[5:7]], ["runner", "crew"])
        self.assertEqual(warnings, [])

    def test_bookkeeping_is_skipped_and_said(self):
        steps, warnings = self.run_spans([span("user_speech", 1), span("create_agent", 3),
                                          span("chat", 5, **{"gen_ai.output.messages": ANSWER})])
        self.assertEqual(len(steps), 1)
        self.assertEqual(len(warnings), 2)

    def test_agent_ops(self):
        for op in ("invoke_agent", "invoke_workflow", "invoke_runner", "invoke_crew"):
            self.assertTrue(_is_agent_op(op), op)
        for op in ("invoke_", "invoke", "chat", None, "execute_tool"):
            self.assertFalse(_is_agent_op(op), op)


class OtlpWireTest(unittest.TestCase):
    def test_a_wire_document_reads_like_the_plain_one(self):
        spans = [
            {"traceId": "t", "spanId": "root", "name": "invoke_runner app", "startTimeUnixNano": "1",
             "endTimeUnixNano": "20", "attributes": [{"key": "gen_ai.operation.name", "value": wire("invoke_runner")},
                                                     {"key": "gen_ai.agent.name", "value": wire("app")}]},
            {"traceId": "t", "spanId": "c", "parentSpanId": "root", "name": "chat m", "startTimeUnixNano": "2",
             "endTimeUnixNano": "3",
             "attributes": [{"key": "gen_ai.operation.name", "value": wire("chat")},
                            {"key": "gen_ai.input.messages", "value": wire(ASK)},
                            {"key": "gen_ai.output.messages", "value": wire(ANSWER)},
                            {"key": "gen_ai.usage.input_tokens", "value": wire(12)}]},
        ]
        doc = {"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}
        traj, _ = from_otlp(doc)
        self.assertEqual(traj["agent"]["name"], "app")     # the runner span is the root agent
        answer = traj["steps"][-1]
        self.assertEqual(answer["output"], "It is 21 degrees.")
        self.assertIn("Weather in Paris?", answer["input"])


if __name__ == "__main__":
    unittest.main()
