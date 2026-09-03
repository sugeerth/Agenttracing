"""Toy external agents for the harness adapter tests."""


def message_agent(task, tools):
    """Returns an OpenAI-style message list: a plain assistant answer."""
    return [
        {"role": "system", "content": "You are a refund clerk."},
        {"role": "user", "content": task["prompt"]},
        {"role": "assistant", "content": "Checking the booking.",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "get_refund",
                                      "arguments": "{\"reference\": \"BK1\"}"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "get_refund",
         "content": "{\"refund\": \"$120.00\"}"},
        {"role": "assistant", "content": "The refund for BK1 is $120.00."},
    ]


def trace_agent(task, tools):
    """Returns a SCHEMA trace dict built by hand — the wrong answer."""
    return {
        "schema_version": 1,
        "steps": [
            {"type": "plan", "name": "plan", "input": "guess", "output": ""},
            {"type": "answer", "name": "final", "input": "The refund for BK1 is $90.00.",
             "output": "The refund for BK1 is $90.00."},
        ],
    }


def crashing_agent(task, tools):
    raise RuntimeError("the agent fell over")


def no_answer_agent(task, tools):
    return [{"role": "user", "content": task["prompt"]},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "get_refund", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "name": "get_refund", "content": "timeout"}]
