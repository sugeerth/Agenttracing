"""Is the step a diagnosis names really the cause? Replay it corrected, beside controls (agentdiff.harness.replay).

Data: scripted, labelled as such. No model is called and nothing is billed: two stand-in agents read a refund
tool. The tool answered $90.00 where the task expects $120.00. Replaying the tool call with the right result is
the correction. Replaying the same prefix with nothing changed is the control: if the controls also pass, the
outcome did not need the correction.
"""
import re

import _here  # noqa: F401
from agentdiff.harness import ScriptedProvider, Tool, run_task
from agentdiff.harness.replay import replay

TASK = {"id": "t_refund", "prompt": "What refund applies to booking BK1?", "expected": "$120.00"}


def refund_tool(amount):
    return Tool("get_refund", lambda reference: {"reference": reference, "refund": amount}, "refund lookup",
                {"type": "object", "properties": {"reference": {"type": "string"}}, "required": ["reference"]},
                effect="read")


def reads_the_tool(messages, tools):
    """Answers with whatever amount the tool returned; asks the tool first."""
    for m in reversed(messages):
        if m["role"] == "tool" and "$" in m["content"]:
            return {"text": f"The refund for BK1 is {re.search(r'[$][0-9,]+[.][0-9]{2}', m['content']).group(0)}."}
    return {"tool_calls": [{"name": "get_refund", "arguments": {"reference": "BK1"}}]}


def always_right(messages, tools):
    """Knows the answer whatever the tool says: the correction cannot be what made it pass."""
    return {"text": "The refund for BK1 is $120.00."}


trace = run_task(ScriptedProvider(reads_the_tool, model="scripted"), TASK, [refund_tool("$90.00")],
                 agent="stand-in", out_dir=None)
step = next(i for i, s in enumerate(trace["steps"]) if s["type"] == "tool_call")
print(f"the recorded run failed: {trace['outcome']['success'] is False}; replaying step {step}, the tool call")
correction = {"output": "{'reference': 'BK1', 'refund': '$120.00'}"}
for name, agent in (("reads the tool", reads_the_tool), ("knows the answer anyway", always_right)):
    out = replay(trace, None, [refund_tool("$90.00")], step, correction, replays=3,
                 provider_factory=lambda agent=agent: ScriptedProvider(agent, model="scripted"))
    print(f"  agent that {name}: corrected replays passed {out['flipped']}/3, controls passed"
          f" {out['control_flipped']}/{out['controls']} -> {out['verdict']}")
print("verified needs at least half the corrected replays to pass, and half the replays more than the controls")
