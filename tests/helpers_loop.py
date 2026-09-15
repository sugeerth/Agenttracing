"""A prompt-aware fake provider for loop tests: ``sloppy`` asserts a
value it never observed unless its system prompt tells it to ground
every value; ``steady`` always looks the value up. No network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deepcompare.harness import Tool  # noqa: E402
from deepcompare.harness.agent import DEFAULT_SYSTEM  # noqa: E402
from deepcompare.harness.providers import Provider, ProviderResponse, ToolCall  # noqa: E402

FACTS = {"BK1": "$120.00", "BK2": "$45.00", "BK3": "$300.00", "BK4": "$12.00"}
TASKS = [{"id": f"refund-{k}", "prompt": f"What refund applies to booking {k}?", "expected": v}
         for k, v in FACTS.items()]
GROUNDING = "must come from an observation in this run"


def _lookup(reference: str):
    return {"reference": reference, "refund": FACTS.get(reference, "unknown")}


def tools() -> list:
    return [Tool("get_refund", _lookup, "Look up a booking's refund",
                 {"type": "object", "properties": {"reference": {"type": "string"}}, "required": ["reference"]},
                 effect="read")]


class FakeRefundProvider(Provider):
    kind = "fake"

    def __init__(self, model: str, *, good: bool, heeds: str = GROUNDING) -> None:
        super().__init__(model)
        self.good = good
        self.heeds = heeds
        self.turn = 0

    @property
    def name(self) -> str:
        return self.model

    def complete(self, messages, tools):
        system = messages[0]["content"]
        ref = messages[1]["content"].split("booking ")[1].rstrip("?")
        careful = self.heeds in system and system != DEFAULT_SYSTEM
        self.turn += 1
        usage = {"input_tokens": 40, "output_tokens": 10}
        if self.turn == 1 and not (self.good or careful):
            return ProviderResponse(text=f"The refund for {ref} is $99.00.", usage=usage, model=self.model, latency_s=0.01)
        if self.turn == 1:
            return ProviderResponse(text="Looking it up.", usage=usage, model=self.model, latency_s=0.01,
                                    tool_calls=[ToolCall(id="c1", name="get_refund", arguments={"reference": ref})])
        try:
            refund = json.loads(messages[-1]["content"])["refund"]
        except (ValueError, KeyError, TypeError):
            refund = messages[-1]["content"]
        return ProviderResponse(text=f"The refund for {ref} is {refund}.", usage=usage, model=self.model, latency_s=0.01)


def factory(spec: str) -> Provider:
    return FakeRefundProvider(spec, good=(spec == "steady"))


def run_demo_loop(out_dir, **kwargs):
    from deepcompare.harness.loop import Loop
    opts = dict(runs=2, max_iterations=4, template=ROOT / "web" / "blocks.html")
    opts.update(kwargs)
    loop = Loop(TASKS, {"steady": "steady", "sloppy": "sloppy"}, out_dir=out_dir, provider_factory=factory,
                tools=tools(), **opts)
    return loop.run()
