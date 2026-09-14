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
import socket
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
from deepcompare.harness.serve import _family, make_server  # noqa: E402
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

    def test_the_data_side_and_one_step_in_full(self):
        status, d = self.json(f"/api/v1/runs/{KEY}/data")
        self.assertEqual(status, 200)
        self.assertTrue(d["measurable"])
        self.assertEqual((d["task"]["prompt_chars"], d["corpus"]["distinct"], d["provenance"]["supported"]), (110, 3, 3))
        self.assertEqual(d, self.bundle.data(KEY))
        status, st = self.json(f"/api/v1/runs/{KEY}/steps/3")
        self.assertEqual(status, 200)
        self.assertEqual((st["index"], st["name"], st["output_chars"]), (3, "open_page", 301))
        self.assertEqual(st, self.bundle.step(KEY, 3))
        status, payload = self.json(f"/api/v1/runs/{KEY}/steps/99")
        self.assertEqual(status, 404)
        self.assertIn("no step 99", payload["reason"])
        status, payload = self.json(f"/api/v1/runs/{KEY}/steps/three")
        self.assertEqual(status, 400)
        self.assertIn("integer", payload["reason"])
        status, payload = self.json("/api/v1/runs/no/such/run/r1/data")
        self.assertEqual(status, 404)
        status, payload = self.json("/api/v1/runs/no/such/run/r1/steps/0")
        self.assertEqual(status, 404)

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

    def test_a_record_the_index_names_but_the_disk_lacks_is_a_500_with_a_reason(self):
        # finding 6: the handler raised inside route() and the client saw a dropped connection, not an answer
        rel = self.bundle.run_index[KEY]
        path = self.bundle.path / rel
        moved = path.with_suffix(".json.bak")
        path.rename(moved)
        try:
            status, payload = self.json("/api/v1/runs/" + KEY)
        finally:
            moved.rename(path)
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"], "server error")
        self.assertIn("the bundle cannot be read", payload["reason"])
        self.assertNotIn(str(self.bundle.path), payload["reason"], "no absolute path in the answer")
        status, payload = self.json("/api/v1/runs/" + KEY)
        self.assertEqual(status, 200, "restored, it answers again")

    def test_an_ipv6_loopback_binds_an_ipv6_socket(self):
        # finding 6: ::1 is in the command's LOCAL_HOSTS but the server was IPv4-only and raised gaierror
        self.assertEqual((_family("::1"), _family("127.0.0.1"), _family("localhost")), (socket.AF_INET6, socket.AF_INET, socket.AF_INET))
        try:
            probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            probe.bind(("::1", 0))
            probe.close()
        except OSError as exc:
            self.skipTest(f"no IPv6 loopback here: {exc}")
        server = make_server(self.bundle, "::1", 0)
        try:
            self.assertEqual(server.address_family, socket.AF_INET6)
            self.assertEqual(server.server_address[0], "::1")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            with urllib.request.urlopen(f"http://[::1]:{server.server_address[1]}/api/v1/overview", timeout=10) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn("totals", json.loads(resp.read().decode("utf-8")))
        finally:
            server.shutdown()
            server.server_close()


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

    def test_binding_elsewhere_is_warned_about_and_an_address_that_cannot_be_bound_is_exit_2(self):
        # the warning is printed before the server is made; an address that cannot be bound is an error with the
        # reason and exit 2 (finding 6: it used to be an uncaught OSError)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = serve_cmd.run(self.parse(["serve", "--bundle", str(demo_bundle()), "--host", "203.0.113.1", "--port", "0"]))
        self.assertEqual(code, 2)
        self.assertIn("warning: binding to 203.0.113.1", err.getvalue())
        self.assertIn("error: cannot bind 203.0.113.1:0", err.getvalue())
        busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = serve_cmd.run(self.parse(["serve", "--bundle", str(demo_bundle()), "--port", str(busy.getsockname()[1])]))
        finally:
            busy.close()
        self.assertEqual(code, 2, "a busy port is exit 2 with the reason, not a traceback")
        self.assertIn("error: cannot bind 127.0.0.1:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
