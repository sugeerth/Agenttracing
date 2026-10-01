"""In-band agent telemetry: the vector, the probe, the carriers, the sink.

The claims under test: a vector is small and fixed-size per hop; a reader
skips fields it does not know; a hop records what it was asked to and
nothing else (never content); hops cross processes and services and come
back without losing a sibling's; every limit (hop budget, word width) is
stated rather than hidden; and the sink's trajectory is one every other
command reads.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentdiff.telemetry import (FIELDS, Field, NullProbe, Probe, Registry, WireError, attach, decode, encode,
                                 from_text, from_trajectory, inbound, outbound, reply, rows, subprocess_env,
                                 summary, to_text, to_trajectory)
from agentdiff.telemetry.fields import DEFAULT_INSTRUCTIONS
from agentdiff.telemetry.wire import MAX_BYTES, Vector
from agentdiff.trace import Trajectory

ROOT = Path(__file__).resolve().parent.parent


class FakeClock:
    def __init__(self, wall: float = 1_800_000_000.0) -> None:
        self.t, self.w = 100.0, wall

    def monotonic(self) -> float:
        return self.t

    def wall(self) -> float:
        return self.w + (self.t - 100.0)

    def advance(self, s: float) -> None:
        self.t += s


def _env():
    return {**os.environ, "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}


class WireTest(unittest.TestCase):
    def test_round_trip_through_bytes_and_text(self):
        p = Probe(agent="a", task="t", clock=FakeClock())
        with p.hop("search", kind="search", args="q") as h:
            h.result("r" * 10)
        v = p.vector
        again = decode(encode(v))
        self.assertEqual(again.hops, v.hops)
        self.assertEqual(again.names, v.names)
        self.assertEqual(decode(from_text(to_text(encode(v)))).trace_id, v.trace_id)
        self.assertTrue(to_text(encode(v)).startswith("adi1."))

    def test_a_hop_is_one_word_per_field_asked_for(self):
        p = Probe(clock=FakeClock(), names=False)
        empty = len(encode(p.vector))
        with p.hop("x"):
            pass
        self.assertEqual(len(encode(p.vector)) - empty, 4 * len(DEFAULT_INSTRUCTIONS))

    def test_what_is_not_a_vector_is_refused(self):
        good = encode(Probe(clock=FakeClock()).vector)
        for bad, why in ((b"XX" + good[2:], "magic"), (good[:2] + bytes([9]) + good[3:], "version"),
                         (good[:10], "short"), (good + b"\x00", "trailing")):
            with self.subTest(why=why), self.assertRaises(WireError):
                decode(bad)
        with self.assertRaises(WireError):
            decode(b"\x00" * (MAX_BYTES + 1))
        with self.assertRaises(WireError):
            from_text("not-a-vector")
        # a hop count that promises more bytes than there are
        with self.assertRaises(WireError):
            decode(good[:10] + b"\xff\xff" + good[12:])

    def test_a_reader_skips_a_field_it_does_not_know(self):
        newer = Registry()
        for f in FIELDS:
            newer.register(f)
        newer.register(Field(20, "gpu_ms", "a field this reader has never heard of", lambda v: int(v or 0), int))
        p = Probe(clock=FakeClock(), registry=newer, instructions=("tool", "gpu_ms", "status"))
        with p.hop("render") as h:
            h.set("gpu_ms", 77)
            h.fail("timeout")
        old_rows = rows(p.text())          # read with the shipped registry
        self.assertEqual(old_rows[0]["tool"], "render")
        self.assertEqual(old_rows[0]["status"], "timeout", "the word after the unknown one is read right")
        self.assertEqual(old_rows[0]["unknown"], {20: 77})
        self.assertEqual(rows(p.text(), newer)[0]["gpu_ms"], 77)


class RegistryTest(unittest.TestCase):
    def test_a_field_is_added_by_registering_it_and_refused_when_it_collides(self):
        r = Registry()
        r.register(Field(0, "tool", "", int, int))
        with self.assertRaises(ValueError):
            r.register(Field(0, "other", "", int, int))
        with self.assertRaises(ValueError):
            r.register(Field(1, "tool", "", int, int))
        with self.assertRaises(ValueError):
            r.register(Field(32, "far", "", int, int))
        with self.assertRaises(KeyError):
            r.get("missing")

    def test_every_shipped_field_has_a_unique_bit_and_a_description(self):
        bits = [f.bit for f in FIELDS]
        self.assertEqual(len(bits), len(set(bits)))
        self.assertTrue(all(f.doc for f in FIELDS))


class ProbeTest(unittest.TestCase):
    def test_a_hop_records_its_times_sizes_and_outcome_on_the_probe_clock(self):
        clock = FakeClock()
        p = Probe(agent="mine", task="t1", clock=clock)
        clock.advance(0.5)
        with p.hop("search", kind="search", args="flights") as h:
            clock.advance(0.25)
            h.result("x" * 300).tokens(prompt=800, completion=40)
        r = rows(p.text())[0]
        self.assertEqual((r["tool"], r["kind"], r["status"]), ("search", "search", "ok"))
        self.assertAlmostEqual(r["start"], 0.5, places=3)
        self.assertAlmostEqual(r["latency"], 0.25, places=5)
        self.assertEqual((r["bytes_in"], r["bytes_out"], r["tokens_in"], r["tokens_out"]), (7, 300, 800, 40))
        info = summary(p.text())
        self.assertEqual((info["agent"], info["task"]), ("mine", "t1"))

    def test_an_exception_is_recorded_and_still_raised(self):
        p = Probe(clock=FakeClock())
        with self.assertRaises(ZeroDivisionError):
            with p.hop("calc"):
                1 / 0
        with self.assertRaises(TimeoutError):
            with p.hop("slow"):
                raise TimeoutError
        self.assertEqual([r["status"] for r in rows(p.text())], ["error", "timeout"])

    def test_the_decorator_names_the_hop_and_measures_both_sides(self):
        p = Probe(clock=FakeClock())

        @p.tool
        def read_file(path):
            return "contents"
        self.assertEqual(read_file("a.py"), "contents")
        r = rows(p.text())[0]
        self.assertEqual(r["tool"], "read_file")
        self.assertEqual(r["bytes_out"], 8)

    def test_the_hop_budget_refuses_and_says_so(self):
        p = Probe(clock=FakeClock(), max_hops=2)
        for i in range(5):
            with p.hop(f"t{i}"):
                pass
        info = summary(p.text())
        self.assertEqual((info["hops"], info["dropped"], info["overflowed"]), (2, 3, True))
        traj = to_trajectory(p.text())
        self.assertEqual(traj["source"]["dropped"], 3)

    def test_a_value_too_large_for_a_word_is_clamped_and_flagged(self):
        clock = FakeClock()
        p = Probe(clock=clock)
        with p.hop("forever"):
            clock.advance(10_000.0)       # 10,000 s is past a 32-bit microsecond count
        self.assertTrue(summary(p.text())["clamped"])

    def test_hops_from_many_threads_are_all_kept(self):
        p = Probe()

        def work(i):
            with p.hop(f"t{i % 3}") as h:
                h.result("x")
        threads = [threading.Thread(target=work, args=(i,)) for i in range(64)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(summary(p.text())["hops"], 64)

    def test_a_span_marks_a_sub_agents_hops(self):
        p = Probe(clock=FakeClock(), instructions=DEFAULT_INSTRUCTIONS + ("span",))
        with p.span("researcher"):
            with p.hop("search"):
                pass
        with p.hop("answer-check"):
            pass
        spans = [r.get("span") for r in rows(p.text())]
        self.assertEqual(spans, ["researcher", None])

    def test_the_null_probe_and_hop_offer_every_public_method_the_real_ones_do(self):
        # a tool written against Probe must run unchanged against NullProbe
        from agentdiff.telemetry.probe import Hop, _NullHop
        public = lambda cls: {n for n in dir(cls) if not n.startswith("_") and callable(getattr(cls, n))}  # noqa: E731
        for real, null in ((Probe, NullProbe), (Hop, _NullHop)):
            with self.subTest(real=real.__name__):
                missing = public(real) - public(null) - {"resume"}
                self.assertEqual(missing, set())

    def test_the_null_probe_has_the_same_shape_and_records_nothing(self):
        n = NullProbe()
        with n.hop("x", kind="read", args="a") as h:
            h.result("y").tokens(1, 2).cost(0.1).fail("error").set("tool", 1)

        @n.tool
        def f():
            return 3
        self.assertEqual(f(), 3)
        self.assertEqual(n.text(), "")
        self.assertEqual(n.absorb("", n.handover()), 0)


class CarrierTest(unittest.TestCase):
    CHILD = textwrap.dedent('''
        from agentdiff.telemetry import attach
        probe = attach()
        with probe.hop("{name}", args="x") as h:
            h.result("y" * {n})
    ''')

    def test_hops_cross_into_subprocesses_and_come_back_without_losing_a_siblings(self):
        p = Probe(agent="parent", task="t")
        with p.hop("plan", kind="plan"):
            pass
        with subprocess_env(p, _env()) as env:
            # two callees share one hand-off, as a shell that exports the variable once would
            for name, n in (("lint", 10), ("pytest", 20)):
                subprocess.run([sys.executable, "-c", self.CHILD.format(name=name, n=n)], env=env, check=True)
        with p.hop("answer-draft"):
            pass
        got = rows(p.text())
        self.assertEqual(sorted(r["tool"] for r in got), ["answer-draft", "lint", "plan", "pytest"])
        self.assertEqual(len({r["node"] for r in got}), 3, "each process stamps its own node")

    def test_a_wrapped_command_is_a_hop_and_an_instrumented_one_nests_inside_it(self):
        p = Probe()
        inner = self.CHILD.format(name="inner", n=5)
        with subprocess_env(p, _env()) as env:
            done = subprocess.run([sys.executable, "-m", "agentdiff", "telemetry", "wrap", "--name", "tool", "--",
                                   sys.executable, "-c", inner], env=env, capture_output=True, text=True)
            failed = subprocess.run([sys.executable, "-m", "agentdiff", "telemetry", "wrap", "--", "false"],
                                    env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(failed.returncode, 1, "wrap returns the command's own exit code")
        got = {r["tool"]: r for r in rows(p.text())}
        self.assertEqual(set(got), {"tool", "inner", "false"})
        self.assertEqual(got["false"]["status"], "error")
        outer, nested = got["tool"], got["inner"]
        self.assertLessEqual(outer["start"], nested["start"])
        self.assertGreaterEqual(outer["start"] + outer["latency"], nested["start"] + nested["latency"])

    def test_without_a_vector_a_tool_gets_the_null_probe_and_runs_the_same(self):
        self.assertIsInstance(attach({}), NullProbe)
        self.assertIsInstance(attach({"AGENTDIFF_INT": "adi1.garbage!!"}), NullProbe, "a bad vector never breaks a tool")
        done = subprocess.run([sys.executable, "-m", "agentdiff", "telemetry", "wrap", "--", "echo", "hi"],
                              env={k: v for k, v in _env().items() if not k.startswith("AGENTDIFF_INT")},
                              capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stdout), (0, "hi\n"))

    def test_a_service_continues_the_vector_and_returns_it_in_its_reply(self):
        p = Probe(agent="client")
        headers, handoff = outbound(p)
        service = inbound(headers, node="search-service")
        with service.hop("vector-db", kind="retrieve") as h:
            h.result("hits")
        with p.hop("meanwhile"):          # the client kept working while the service ran
            pass
        self.assertEqual(p.absorb(reply(service)["AgentDiff-INT"], handoff), 1)
        nodes = {r["tool"]: r["node"] for r in rows(p.text())}
        self.assertEqual(nodes["vector-db"], "search-service")
        self.assertIn("meanwhile", nodes)

    def test_a_vector_from_another_run_is_refused(self):
        a, b = Probe(), Probe()
        with self.assertRaises(WireError):
            a.absorb(b.text(), a.handover())


class SinkTest(unittest.TestCase):
    def test_the_trajectory_is_valid_ordered_and_carries_no_content(self):
        clock = FakeClock()
        p = Probe(agent="mine", task="t1", clock=clock)
        with p.hop("read", kind="read", args="secret file contents") as h:
            clock.advance(0.1)
            h.result("the file's secret text").tokens(10, 2)
        with p.hop("pytest") as h:
            clock.advance(0.2)
            h.fail()
        traj = to_trajectory(p.text(), prompt="fix it", success=True, answer="done")
        Trajectory.from_dict(traj)
        self.assertEqual([s["type"] for s in traj["steps"]], ["read", "tool_call", "answer"])
        self.assertTrue(traj["steps"][1]["error"])
        self.assertEqual((traj["totals"]["input_tokens"], traj["totals"]["output_tokens"]), (10, 2))
        self.assertNotIn("secret", json.dumps(traj["steps"][:2]), "in-band carries sizes, never content")
        self.assertEqual(traj["agent"]["name"], "mine")
        unknown = to_trajectory(p.text())
        self.assertIn("not reported", unknown["outcome"]["note"])

    def test_any_trace_compacts_to_a_vector_and_reads_back(self):
        src = ROOT / "demo" / "vendors" / "live" / "traces"
        traj = json.loads(next(src.glob("*.json")).read_text(encoding="utf-8"))
        v = from_trajectory(traj)
        back = to_trajectory(v)
        steps = [s for s in traj["steps"] if s["type"] != "answer"]
        self.assertEqual(len(back["steps"]) - 1, len(steps))
        self.assertEqual([s["name"] for s in back["steps"][:-1]], [s["name"] for s in steps])
        self.assertEqual(sum(s["tokens"] for s in back["steps"]), sum(int(s.get("tokens") or 0) for s in steps))
        self.assertLess(len(to_text(encode(v))), len(json.dumps(traj)) / 5, "much smaller than the trace")


class CommandTest(unittest.TestCase):
    def run_cli(self, *args, **kw):
        return subprocess.run([sys.executable, "-m", "agentdiff", "telemetry", *args], capture_output=True,
                              text=True, env=_env(), **kw)

    def test_decode_trace_fields_and_encode(self):
        p = Probe(agent="a", task="t")
        with p.hop("x"):
            pass
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "v.int"
            f.write_text(p.text())
            table = self.run_cli("decode", str(f))
            self.assertEqual(table.returncode, 0, table.stderr)
            self.assertIn("1 hop(s) across 1 process(es)", table.stdout)
            js = json.loads(self.run_cli("decode", str(f), "--json").stdout)
            self.assertEqual(js["summary"]["hops"], 1)
            out = Path(tmp) / "t.json"
            self.assertEqual(self.run_cli("trace", str(f), "-o", str(out), "--success", "true").returncode, 0)
            Trajectory.from_dict(json.loads(out.read_text()))
            enc = self.run_cli("encode", str(out))
            self.assertTrue(enc.stdout.startswith("adi1."))
        # a real run's vector, inline: far longer than a file name may be
        long = Probe()
        for i in range(60):
            with long.hop(f"tool-{i}"):
                pass
        self.assertGreater(len(long.text()), 4096)
        inline = self.run_cli("decode", long.text())
        self.assertEqual(inline.returncode, 0, inline.stderr)
        self.assertIn("60 hop(s)", inline.stdout)
        fields = self.run_cli("fields").stdout
        self.assertIn("latency", fields)
        bad = self.run_cli("decode", "adi1.!!!")
        self.assertEqual(bad.returncode, 2)


if __name__ == "__main__":
    unittest.main()
