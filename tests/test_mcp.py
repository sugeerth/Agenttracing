"""The MCP server: the three levels over JSON-RPC on stdio, stdlib only.

What this pins: initialize echoes a known protocol revision and answers
an unknown one with the newest; notifications get silence; ping, the
tool list with a schema per tool, each tool's result, the resources and
their contents; the error codes (−32601 unknown method, −32602 bad
arguments, −32000 a key that names no run, −32700 not JSON); the serve
loop reads lines until EOF; and the module imports no network module.
"""

from __future__ import annotations

import ast
import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deepcompare import mcpserver  # noqa: E402
from deepcompare.bundle import Bundle  # noqa: E402
from deepcompare.commands import mcp as mcp_cmd  # noqa: E402
from tests.helpers_bundle import demo_bundle  # noqa: E402

KEY = "batch/t01_acme_revenue/atlas-v2/r1"


def _req(rid, method, **params):
    msg = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        msg["id"] = rid
    if params:
        msg["params"] = params
    return msg


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = mcpserver.Server(Bundle(demo_bundle()))

    def call(self, name, **arguments):
        response = self.server.handle(_req(9, "tools/call", name=name, arguments=arguments))
        return response

    def test_initialize_echoes_a_known_revision_and_answers_an_unknown_one_with_the_newest(self):
        r = self.server.handle(_req(1, "initialize", protocolVersion="2024-11-05", capabilities={}, clientInfo={"name": "t", "version": "0"}))
        self.assertEqual(r["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(r["result"]["capabilities"], {"tools": {}, "resources": {}})
        self.assertEqual(r["result"]["serverInfo"]["name"], "agentdiff")
        r = self.server.handle(_req(2, "initialize", protocolVersion="1999-01-01"))
        self.assertEqual(r["result"]["protocolVersion"], mcpserver.PROTOCOL_VERSIONS[0])

    def test_notifications_are_silent_and_ping_answers(self):
        self.assertIsNone(self.server.handle(_req(None, "notifications/initialized")))
        self.assertIsNone(self.server.handle(_req(None, "nope")))
        self.assertEqual(self.server.handle(_req(3, "ping"))["result"], {})

    def test_the_tool_list_has_a_schema_and_a_description_per_tool(self):
        tools = self.server.handle(_req(4, "tools/list"))["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["overview", "runs", "run", "fetches", "budget", "step", "data", "lineage", "key", "verify"])
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertGreater(len(t["description"]), 80)
        self.assertEqual(tools[2]["inputSchema"]["required"], ["key"])
        self.assertEqual(tools[5]["inputSchema"]["required"], ["key", "index"])
        self.assertEqual(tools[6]["inputSchema"]["required"], ["key"])

    def test_step_returns_the_whole_text_of_one_step_and_data_the_run_s_data_side(self):
        record = self.call("run", key=KEY)["result"]["structuredContent"]
        st = self.call("step", key=KEY, index=3)["result"]["structuredContent"]
        self.assertEqual((st["key"], st["index"], st["type"], st["name"]), (KEY, 3, "read", "open_page"))
        self.assertEqual(st["input"], record["steps"][3]["input_text"])
        self.assertEqual(st["output"], record["steps"][3]["output_text"])
        self.assertEqual((st["input_chars"], st["output_chars"]), (43, 301))
        self.assertEqual(st["source"], "members/0/report_t01_acme_revenue.json#a.steps[3]")
        self.assertIn("$4.82 billion", st["output"])
        d = self.call("data", key=KEY)["result"]["structuredContent"]
        self.assertTrue(d["measurable"])
        self.assertEqual(d["task"]["prompt_chars"], 110)
        self.assertEqual(d["models"][0]["source"], "steps[].model")
        self.assertEqual(d["corpus"]["distinct"], 3)
        self.assertEqual(d["provenance"]["supported"], 3)
        self.assertEqual(d, record["data"])
        # the errors: a bad index, a missing index, a key that names no run
        bad = self.call("step", key=KEY, index=99)["error"]
        self.assertEqual(bad["code"], -32000)
        self.assertIn("no step 99", bad["message"])
        self.assertEqual(self.call("step", key=KEY)["error"]["code"], -32602)
        self.assertEqual(self.call("step", key=KEY, index=-1)["error"]["code"], -32602)
        self.assertEqual(self.call("step", key="no/such/run/r1", index=0)["error"]["code"], -32000)
        self.assertEqual(self.call("data", key="no/such/run/r1")["error"]["code"], -32000)

    def test_overview_runs_and_run(self):
        r = self.call("overview")["result"]
        self.assertFalse(r["isError"])
        self.assertEqual([a["name"] for a in r["structuredContent"]["agents"]], ["atlas-v2", "bolt-v3"])
        self.assertEqual(json.loads(r["content"][0]["text"]), r["structuredContent"])
        rows = self.call("runs", agent="atlas-v2", sort="tokens", limit=3)["result"]["structuredContent"]
        self.assertEqual(rows["n"], 3)
        self.assertEqual(rows["of"], 18)
        self.assertTrue(all(r["agent"] == "atlas-v2" for r in rows["runs"]))
        self.assertGreaterEqual(rows["runs"][0]["tokens"], rows["runs"][1]["tokens"])
        failed = self.call("runs", success=False)["result"]["structuredContent"]
        self.assertEqual(failed["n"], 4)
        record = self.call("run", key=KEY)["result"]["structuredContent"]
        self.assertEqual(record["key"], KEY)
        self.assertEqual(len(record["steps"]), 5)
        self.assertEqual(record["budget"]["tokens"]["total"], 840)

    def test_fetches_budget_lineage_key_and_verify(self):
        f = self.call("fetches", key=KEY)["result"]["structuredContent"]
        self.assertEqual(f["counts"]["total"], 3)
        summary = self.call("fetches", agent="bolt-v3")["result"]["structuredContent"]
        self.assertEqual(list(summary["batch"]["agents"]), ["bolt-v3"])
        self.assertEqual(summary["batch"]["source"], "derived from the pair reports' sides")
        b = self.call("budget", agent="atlas-v2", task="t01_acme_revenue")["result"]["structuredContent"]
        self.assertEqual(list(b["batch"]["agents"]), ["atlas-v2"])
        self.assertEqual(list(b["batch"]["tasks"]), ["t01_acme_revenue"])
        burn = self.call("budget", key=KEY)["result"]["structuredContent"]
        self.assertEqual(burn["burn"][-1][4], 840)
        ln = self.call("lineage")["result"]["structuredContent"]
        self.assertEqual(ln["lineages"], [])
        self.assertEqual(ln["reason"], "no lineage in the bundle")
        k = self.call("key")["result"]["structuredContent"]
        self.assertEqual(k["key"], self.server.bundle.key)
        self.assertEqual(k["overview"]["id"], self.server.bundle.id)
        self.assertTrue(self.call("verify")["result"]["structuredContent"]["match"])

    def test_the_error_codes(self):
        self.assertEqual(self.server.handle(_req(5, "nope"))["error"]["code"], -32601)
        self.assertEqual(self.call("nope")["error"]["code"], -32602)
        self.assertEqual(self.call("runs", bogus=1)["error"]["code"], -32602)
        self.assertEqual(self.call("runs", sort="bogus")["error"]["code"], -32602)
        self.assertEqual(self.call("runs", limit="3")["error"]["code"], -32602)
        self.assertEqual(self.call("run")["error"]["code"], -32602)
        missing = self.call("run", key="no/such/run/r1")["error"]
        self.assertEqual(missing["code"], -32000)
        self.assertIn("no run", missing["message"])
        self.assertEqual(self.server.handle(_req(6, "resources/read", uri="agentdiff://bundle/nope"))["error"]["code"], -32000)
        parse = self.server.handle_line("{not json")
        self.assertEqual(json.loads(parse[0])["error"]["code"], -32700)
        self.assertEqual(self.server.handle([1, 2])["error"]["code"], -32600)

    def test_an_empty_batch_is_an_invalid_request_and_the_jsonrpc_field_is_checked(self):
        # finding 14: "[]" was answered with silence, and a request without "jsonrpc": "2.0" was served
        empty = self.server.handle_line("[]")
        self.assertEqual(len(empty), 1)
        self.assertEqual(json.loads(empty[0]), {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "an empty batch"}})
        self.assertEqual(self.server.handle_line("[ ]"), empty)
        missing = self.server.handle({"id": 1, "method": "ping"})
        self.assertEqual((missing["id"], missing["error"]["code"]), (1, -32600))
        self.assertIn('"jsonrpc": "2.0"', missing["error"]["message"])
        wrong = self.server.handle({"jsonrpc": "1.0", "id": 2, "method": "ping"})
        self.assertEqual(wrong["error"]["code"], -32600)
        self.assertEqual(self.server.handle(_req(3, "ping"))["result"], {}, "a well-formed request still answers")
        self.assertEqual(self.server.handle_line(json.dumps([_req(4, "ping")])), [json.dumps({"jsonrpc": "2.0", "id": 4, "result": {}})])

    def test_resources_list_and_read(self):
        resources = self.server.handle(_req(7, "resources/list"))["result"]["resources"]
        uris = [r["uri"] for r in resources]
        self.assertIn("agentdiff://bundle/bundle.json", uris)
        self.assertIn("agentdiff://bundle/members/0/aggregate.json", uris)
        self.assertIn(f"agentdiff://bundle/runs/{KEY}.json", uris)
        self.assertTrue(all(r["mimeType"] == "application/json" for r in resources))
        read = self.server.handle(_req(8, "resources/read", uri=f"agentdiff://bundle/runs/{KEY}.json"))["result"]["contents"][0]
        self.assertEqual(json.loads(read["text"])["key"], KEY)
        manifest = self.server.handle(_req(8, "resources/read", uri="agentdiff://bundle/bundle.json"))["result"]["contents"][0]
        self.assertEqual(json.loads(manifest["text"])["id"], self.server.bundle.id)

    def test_the_serve_loop_reads_until_eof_one_response_per_request(self):
        lines = [json.dumps(_req(1, "initialize", protocolVersion="2025-06-18")), json.dumps(_req(None, "notifications/initialized")),
                 "", json.dumps(_req(2, "tools/call", name="overview", arguments={})),
                 json.dumps([_req(3, "ping"), _req(4, "ping")])]
        out = io.StringIO()
        code = mcpserver.Server(Bundle(demo_bundle())).serve(io.StringIO("\n".join(lines) + "\n"), out)
        self.assertEqual(code, 0)
        responses = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in responses], [1, 2, 3, 4])

    def test_the_command_runs_the_loop_on_stdio(self):
        import argparse
        p = argparse.ArgumentParser()
        sub = p.add_subparsers(dest="command", required=True)
        mcp_cmd.register(sub)
        args = p.parse_args(["mcp", "--bundle", str(demo_bundle())])
        stdin, stdout = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = io.StringIO(json.dumps(_req(1, "ping")) + "\n"), io.StringIO()
        try:
            code = mcp_cmd.run(args)
            text = sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = stdin, stdout
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["result"], {})
        self.assertEqual(mcp_cmd.run(p.parse_args(["mcp", "--bundle", "/nonexistent"])), 2)

    def test_the_server_imports_no_network_module(self):
        tree = ast.parse((ROOT / "deepcompare" / "mcpserver.py").read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertFalse(names & {"socket", "http", "urllib", "ssl", "threading", "asyncio"})


if __name__ == "__main__":
    unittest.main()
