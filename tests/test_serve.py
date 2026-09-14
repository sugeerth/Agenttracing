"""The HTTP API: the three levels behind a read-only local server.

The server is started on port 0 in a thread and driven with ``urllib``
from the test — the test may reach a socket, the engine may not. What
this pins: every route's status and shape, JSON with no-store on every
answer, 404 with a reason for anything else, no directory listing, a
bad filter is 400, the page and the manifest are served, and the
command warns when told to bind beyond localhost.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare.bundle import Bundle  # noqa: E402
from deepcompare.commands import serve as serve_cmd  # noqa: E402
from deepcompare.harness.serve import make_server  # noqa: E402
from tests.helpers_bundle import demo_bundle  # noqa: E402

KEY = "batch/t01_acme_revenue/atlas-v2/r1"


class ServeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = Bundle(demo_bundle())
        cls.server = make_server(cls.bundle, "127.0.0.1", 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.base = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as err:
            return err.code, dict(err.headers), err.read()

    def json(self, path):
        status, headers, body = self.get(path)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertTrue(headers.get("Content-Type", "").startswith("application/json"), path)
        return status, json.loads(body.decode("utf-8"))

    def test_overview_runs_and_run(self):
        status, o = self.json("/api/v1/overview")
        self.assertEqual(status, 200)
        self.assertEqual([a["name"] for a in o["agents"]], ["atlas-v2", "bolt-v3"])
        status, rows = self.json("/api/v1/runs?agent=bolt-v3&sort=tokens&limit=2")
        self.assertEqual(status, 200)
        self.assertEqual(rows["n"], 2)
        self.assertEqual(rows["of"], 16)
        self.assertTrue(all(r["agent"] == "bolt-v3" for r in rows["runs"]))
        status, rows = self.json("/api/v1/runs?success=false")
        self.assertEqual(rows["n"], 4)
        status, record = self.json(f"/api/v1/runs/{KEY}")
        self.assertEqual(status, 200)
        self.assertEqual(record["key"], KEY)
        self.assertEqual(len(record["steps"]), 5)
        status, fetches = self.json(f"/api/v1/runs/{KEY}/fetches")
        self.assertEqual(status, 200)
        self.assertEqual(fetches["counts"]["total"], 3)

    def test_budget_lineage_key_and_verify(self):
        status, b = self.json("/api/v1/budget?agent=atlas-v2")
        self.assertEqual(status, 200)
        self.assertEqual(list(b["batch"]["agents"]), ["atlas-v2"])
        status, ln = self.json("/api/v1/lineage")
        self.assertEqual((status, ln["lineages"]), (200, []))
        status, k = self.json("/api/v1/key")
        self.assertEqual((status, k["key"]), (200, self.bundle.key))
        self.assertEqual(k["overview"]["id"], self.bundle.id)
        status, v = self.json("/api/v1/verify")
        self.assertEqual((status, v["match"]), (200, True))

    def test_the_page_and_the_manifest(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn(b"DEEPCOMPARE_DATA", body)
        self.assertEqual(self.get("/report.html")[0], 200)
        status, manifest = self.json("/bundle.json")
        self.assertEqual((status, manifest["id"]), (200, self.bundle.id))

    def test_404_with_a_reason_no_listing_and_400_for_a_bad_filter(self):
        for path in ("/nope", "/api/v1/nope", "/api/v1/runs/no/such/run/r1", "/runs/", "/members/0/aggregate.json", "/api/v2/overview"):
            status, payload = self.json(path)
            self.assertEqual(status, 404, path)
            self.assertIn("reason", payload)
        status, payload = self.json("/api/v1/runs?sort=bogus")
        self.assertEqual(status, 400)
        self.assertIn("sort must be one of", payload["reason"])
        status, payload = self.json("/api/v1/runs?success=maybe")
        self.assertEqual(status, 400)
        req = urllib.request.Request(self.base + "/api/v1/overview", data=b"{}", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 501)


class CommandTest(unittest.TestCase):
    def parse(self, argv):
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="command", required=True)
        serve_cmd.register(sub)
        return p.parse_args(argv)

    def test_defaults_bind_localhost(self):
        args = self.parse(["serve", "--bundle", "x"])
        self.assertEqual((args.host, args.port), ("127.0.0.1", 8787))

    def test_a_missing_bundle_is_exit_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(serve_cmd.run(self.parse(["serve", "--bundle", "/nonexistent"])), 2)
        self.assertIn("error:", err.getvalue())

    def test_binding_elsewhere_is_warned_about(self):
        # the warning is printed before the server is made, so a port that cannot be bound stops it right after
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(OSError):
            serve_cmd.run(self.parse(["serve", "--bundle", str(demo_bundle()), "--host", "203.0.113.1", "--port", "0"]))
        self.assertIn("warning: binding to 203.0.113.1", err.getvalue())


if __name__ == "__main__":
    unittest.main()
