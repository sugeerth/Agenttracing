"""Live: the recorder streams, the watcher rebuilds, the server pushes.

A running agent's trace is drawn while it runs and never analysed until it
is whole; a finished pair becomes the ordinary story. The server binds to
localhost, serves this directory and nothing else, and every payload says
what is running and what is compared.
"""

from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepcompare.harness.watch import LIVE_SUFFIX, Watcher, serve, simulate
from deepcompare.record import Recorder

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo" / "traces"
TEMPLATE = ROOT / "web" / "blocks.html"


class RecorderStreamingTest(unittest.TestCase):
    def test_stream_writes_the_run_so_far_and_removes_it_on_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = Recorder(task="t1", prompt="what is 2+2", agent="bot", expected="4",
                           out_dir=tmp, stream=True)
            live = rec.live_path
            self.assertTrue(str(live).endswith(LIVE_SUFFIX))
            with rec:
                rec.plan("add the numbers")
                self.assertTrue(live.is_file())
                partial = json.loads(live.read_text(encoding="utf-8"))
                self.assertTrue(partial["in_progress"])
                self.assertEqual(len(partial["steps"]), 1)
                self.assertFalse(partial["outcome"]["success"])
                call = rec.tool("calc", {"expr": "2+2"})
                call.observe("4")
                partial = json.loads(live.read_text(encoding="utf-8"))
                self.assertEqual(len(partial["steps"]), 2)
                self.assertEqual(partial["steps"][1]["output"], "4", "an observation re-streams the step")
                rec.answer("4", success=True)
            self.assertFalse(live.exists(), "the live file goes when the final lands")
            final = json.loads(rec.path.read_text(encoding="utf-8"))
            self.assertTrue(final["outcome"]["success"])
            self.assertNotIn("in_progress", final)

    def test_without_stream_nothing_partial_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            with Recorder(task="t1", prompt="p", agent="bot", expected="4", out_dir=tmp) as rec:
                rec.plan("x")
                self.assertIsNone(rec.live_path)
                self.assertEqual(list(Path(tmp).glob("*" + LIVE_SUFFIX)), [])
                rec.answer("4", success=True)


class WatcherTest(unittest.TestCase):
    def test_a_running_trace_is_shown_not_analysed_and_a_finished_pair_is_compared(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            a = json.loads((DEMO / "t05_flight_duration__atlas-v2.json").read_text(encoding="utf-8"))
            b = json.loads((DEMO / "t05_flight_duration__bolt-v3.json").read_text(encoding="utf-8"))
            (out / "t05_flight_duration__atlas-v2.json").write_text(json.dumps(a), encoding="utf-8")
            partial = dict(b)
            partial["steps"] = b["steps"][:2]
            partial["in_progress"] = True
            (out / ("t05_flight_duration__bolt-v3" + LIVE_SUFFIX)).write_text(json.dumps(partial), encoding="utf-8")
            w = Watcher(out, poll=0.05)
            payload = w.payload()
            self.assertEqual(payload["reports"], [], "no report while one side is still running")
            self.assertEqual(len(payload["live"]["runs"]), 1)
            run = payload["live"]["runs"][0]
            self.assertEqual((run["task"], run["agent"], len(run["steps"])), ("t05_flight_duration", "bolt-v3", 2))
            self.assertEqual([f["agent"] for f in payload["live"]["finished"]], ["atlas-v2"])
            v1 = payload["live"]["version"]
            # the run finishes
            (out / "t05_flight_duration__bolt-v3.json").write_text(json.dumps(b), encoding="utf-8")
            (out / ("t05_flight_duration__bolt-v3" + LIVE_SUFFIX)).unlink()
            self.assertTrue(w.refresh())
            payload = w.payload()
            self.assertGreater(payload["live"]["version"], v1)
            self.assertEqual(len(payload["reports"]), 1)
            self.assertEqual(payload["reports"][0]["diagnosis"]["decisive_step"]["step"], 1)
            self.assertEqual(payload["live"]["runs"], [])
            self.assertFalse(w.refresh(), "nothing changed: nothing rebuilt")

    def test_a_half_written_file_is_skipped_until_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / ("t1__bot" + LIVE_SUFFIX)).write_text('{"task": {"id": "t1"}, "steps": [', encoding="utf-8")
            w = Watcher(out)
            self.assertEqual(w.payload()["live"]["runs"], [])


class CheckpointingWatcherTest(unittest.TestCase):
    def test_every_live_update_becomes_a_checkpoint_and_the_final_a_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            db = out / "t.sqlite"
            b = json.loads((DEMO / "t05_flight_duration__bolt-v3.json").read_text(encoding="utf-8"))
            w = Watcher(out, poll=0.05, db=db)
            for k in (1, 2):
                partial = dict(b); partial["steps"] = b["steps"][:k]; partial["in_progress"] = True
                (out / ("t05_flight_duration__bolt-v3" + LIVE_SUFFIX)).write_text(json.dumps(partial), encoding="utf-8")
                w.refresh(force=True)
            (out / "t05_flight_duration__bolt-v3.json").write_text(json.dumps(b), encoding="utf-8")
            (out / ("t05_flight_duration__bolt-v3" + LIVE_SUFFIX)).unlink()
            w.refresh(force=True)
            from deepcompare.tracedb import TraceDB
            with TraceDB(db) as store:
                self.assertEqual([c["step"] for c in store.checkpoints("t05_flight_duration__bolt-v3")], [1, 2])
                self.assertEqual(store.count(source="watch"), 1)


class SimulatorTest(unittest.TestCase):
    def test_the_demo_streams_steps_then_lands_the_finals(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as out:
            for name in ("t05_flight_duration__atlas-v2.json", "t05_flight_duration__bolt-v3.json"):
                (Path(src) / name).write_text((DEMO / name).read_text(encoding="utf-8"), encoding="utf-8")
            stop = threading.Event()
            seen_live = False
            t = threading.Thread(target=simulate, args=(src, out, 0.02, False, stop), daemon=True)
            t.start()
            deadline = time.time() + 10
            while t.is_alive() and time.time() < deadline:
                if list(Path(out).glob("*" + LIVE_SUFFIX)):
                    seen_live = True
                time.sleep(0.01)
            t.join(timeout=5)
            self.assertTrue(seen_live, "partial traces were streamed")
            finals = sorted(p.name for p in Path(out).glob("*.json"))
            self.assertEqual(finals, ["t05_flight_duration__atlas-v2.json", "t05_flight_duration__bolt-v3.json"])


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.stop = threading.Event()
        cls.server = serve(cls.out, TEMPLATE, port=0, poll=0.05, stop=cls.stop)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown_all()
        cls.tmp.cleanup()

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, resp.getheader("Content-Type"), body

    def test_the_page_is_served_with_the_live_flag_and_data_json_matches(self):
        status, ctype, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn('"live": {"enabled": true', body.decode("utf-8").replace("\\/", "/"))
        status, ctype, body = self.get("/data.json")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["live"]["enabled"])
        self.assertEqual(payload["live"]["events"], "/events")
        self.assertEqual(self.get("/nope")[0], 404)

    def test_events_push_a_payload_when_a_trace_lands(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/events")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertIn("text/event-stream", resp.getheader("Content-Type"))
        first = self._read_event(resp)
        self.assertEqual(first["live"]["runs"], [])
        a = (DEMO / "t05_flight_duration__atlas-v2.json").read_text(encoding="utf-8")
        (self.out / ("t05_flight_duration__atlas-v2" + LIVE_SUFFIX)).write_text(
            json.dumps({"task": {"id": "t05_flight_duration"}, "agent": {"name": "atlas-v2"},
                        "steps": json.loads(a)["steps"][:3], "in_progress": True}), encoding="utf-8")
        second = self._read_event(resp)
        self.assertEqual(len(second["live"]["runs"]), 1)
        self.assertEqual(len(second["live"]["runs"][0]["steps"]), 3)
        self.assertGreater(second["live"]["version"], first["live"]["version"])
        conn.close()

    @staticmethod
    def _read_event(resp):
        data_lines = []
        deadline = time.time() + 10
        while time.time() < deadline:
            line = resp.readline().decode("utf-8")
            if not line:
                break
            line = line.rstrip("\n")
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif line == "" and data_lines:
                return json.loads("".join(data_lines))
        raise AssertionError("no event arrived")


if __name__ == "__main__":
    unittest.main()
