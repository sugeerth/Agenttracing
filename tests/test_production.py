"""What running AgentDiff for real needs, beyond being correct.

A tool that starts other agents with API keys in their environment has
obligations a library does not: it must stop them when it is stopped, it
must not leave their workspaces behind, it must not let one run's output
fill the disk, and a page carrying every trace it read must not be served
to the network by accident. And what `pip install` puts on a machine has
to work from anywhere, not only from the checkout it was built in.
"""

import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKES = ROOT / "tests" / "fixtures" / "vendors"
TASK = ROOT / "demo" / "vendors" / "task.json"


def _env(**extra):
    e = dict(os.environ, OPENAI_API_KEY="sk-test-0000000000", ANTHROPIC_API_KEY="sk-ant-test-0000000000")
    e.pop("FAKE_VENDOR_MODE", None)
    e.update(extra)
    return e


class VersionTest(unittest.TestCase):
    def test_the_cli_reports_the_packaged_version(self):
        import re
        # tomllib is 3.11+, and this package supports 3.10: read the one field
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        want = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)
        done = subprocess.run([sys.executable, "-m", "deepcompare", "--version"], cwd=str(ROOT),
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0)
        self.assertEqual(done.stdout.strip(), f"agentdiff {want}")
        from deepcompare import __version__
        self.assertEqual(__version__, want)


class ServingTest(unittest.TestCase):
    def test_the_bind_policy(self):
        from deepcompare.harness.watch import bind_policy
        for host in ("127.0.0.1", "::1", "localhost"):
            self.assertIsNone(bind_policy(host))
        for host in ("0.0.0.0", "192.168.1.10", ""):
            with self.assertRaises(ValueError) as caught:
                bind_policy(host)
            self.assertIn("--allow-remote", str(caught.exception))
        token = bind_policy("0.0.0.0", allow_remote=True)
        self.assertGreaterEqual(len(token), 24)
        self.assertNotEqual(token, bind_policy("0.0.0.0", allow_remote=True))

    def test_a_tokened_server_refuses_without_it_and_remembers_it_with_a_cookie(self):
        from deepcompare.commands.paths import DEFAULT_TEMPLATE
        from deepcompare.harness.watch import serve
        with tempfile.TemporaryDirectory() as tmp:
            server = serve(tmp, DEFAULT_TEMPLATE, host="127.0.0.1", port=0, token="t0ken-abcdefghijklmnop")
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                for path in ("/", "/data.json", "/events"):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(base + path, timeout=5)
                    self.assertEqual(caught.exception.code, 403, path)
                    caught.exception.close()
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(base + "/data.json?token=wrong", timeout=5)
                caught.exception.close()
                with urllib.request.urlopen(base + "/data.json?token=t0ken-abcdefghijklmnop", timeout=5) as ok:
                    self.assertEqual(ok.status, 200)
                    cookie = ok.headers.get("Set-Cookie")
                    frame, referrer = ok.headers.get("X-Frame-Options"), ok.headers.get("Referrer-Policy")
                    ok.read()
                self.assertIn("HttpOnly", cookie)
                self.assertIn("SameSite=Strict", cookie)
                self.assertEqual(frame, "DENY")
                self.assertEqual(referrer, "no-referrer")
                with urllib.request.urlopen(urllib.request.Request(
                        base + "/data.json", headers={"Cookie": cookie.split(";")[0]}), timeout=5) as again:
                    self.assertEqual(again.status, 200)
                    again.read()
            finally:
                server.shutdown_all()

    def test_watch_will_not_serve_beyond_this_machine_by_accident(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run([sys.executable, "-m", "deepcompare", "watch", tmp, "--host", "0.0.0.0"],
                                  cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 2)
        self.assertIn("--allow-remote", done.stderr)


class DuelLifecycleTest(unittest.TestCase):
    def test_sigterm_stops_the_agents_removes_their_workspaces_and_exits_130(self):
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp) / "tmp"
            scratch.mkdir()
            proc = subprocess.Popen([sys.executable, "-m", "deepcompare", "duel", "--task", str(TASK), "--quiet",
                                     "--codex-bin", str(FAKES / "fake_codex.py"),
                                     "--claude-bin", str(FAKES / "fake_claude.py"),
                                     "-o", str(Path(tmp) / "d")],
                                    cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    env=_env(FAKE_VENDOR_SLOW="2", TMPDIR=str(scratch)))
            # wait until both agents are running in their workspace copies
            deadline = time.time() + 30
            while time.time() < deadline and len(list(scratch.glob("agentdiff-*"))) < 2:
                time.sleep(0.2)
            self.assertEqual(len(list(scratch.glob("agentdiff-*"))), 2, "both agents started")
            time.sleep(1.0)
            proc.send_signal(signal.SIGTERM)
            out, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 130, err[-2000:])
            self.assertIn("interrupted: stopped", err)
            self.assertEqual(list(scratch.glob("agentdiff-*")), [], "no workspace copy left behind")
            stray = [p for p in subprocess.run(["pgrep", "-f", "fake_claude.py|fake_codex.py"],
                                               capture_output=True, text=True).stdout.split() if p]
            for pid in stray:
                try:
                    environ = Path(f"/proc/{pid}/environ").read_bytes()
                except OSError:
                    continue
                self.assertNotIn(str(scratch).encode(), environ, "no agent process left running")

    def test_a_run_that_floods_its_output_is_stopped_at_the_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "d"
            done = subprocess.run([sys.executable, "-m", "deepcompare", "duel", "--task", str(TASK), "--quiet",
                                   "--codex-bin", str(FAKES / "fake_codex.py"),
                                   "--claude-bin", str(FAKES / "fake_claude.py"),
                                   "--max-stream-mb", "0.002", "-o", str(out)],
                                  cwd=str(ROOT), capture_output=True, text=True, env=_env(), timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            recs = [json.loads(p.read_text()) for p in (out / "records").glob("*.json")]
            capped = [r for r in recs if r["stopped_by"] == "stream_cap"]
            self.assertTrue(capped)
            for r in capped:
                self.assertLessEqual(r["stream"]["bytes"], r["stream"]["cap"] + 64 * 1024)
                trace = json.loads((out / r["trace"]).read_text())
                self.assertIn("stream cap", trace["outcome"]["note"])
                self.assertEqual(trace["outcome"]["termination"], "user_stop")


class InstalledTest(unittest.TestCase):
    """The wheel, installed into a clean environment, run from outside the checkout.

    Built from the files git sees, as `pip install git+https://...` does: the
    page inside the package is a build output, absent from a fresh clone,
    and the build writes it. Built from this checkout, where the page was
    already there, the test once passed while a fresh clone installed a
    package with no page."""

    @unittest.skipUnless(os.environ.get("AGENTDIFF_WHEEL_TEST", "1") == "1", "wheel install test disabled")
    def test_the_installed_console_script_works_from_anywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                                    cwd=str(ROOT), capture_output=True, text=True)
            if listed.returncode != 0:
                self.skipTest("not a git checkout")
            clean = tmp / "clean"
            for rel in filter(None, listed.stdout.split("\0")):
                src = ROOT / rel
                if src.is_file():
                    (clean / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, clean / rel)
            self.assertFalse((clean / "deepcompare" / "page").exists(), "a fresh clone has no built page")
            built = subprocess.run([sys.executable, "-m", "pip", "wheel", str(clean), "--no-deps", "-q",
                                    "-w", str(tmp / "wheel")], capture_output=True, text=True, timeout=600)
            if built.returncode != 0:
                self.skipTest(f"could not build a wheel here: {built.stderr[-300:]}")
            subprocess.run([sys.executable, "-m", "venv", str(tmp / "venv")], check=True, timeout=300)
            pip = tmp / "venv" / "bin" / "pip"
            wheel = next((tmp / "wheel").glob("agentdiff-*.whl"))
            subprocess.run([str(pip), "install", "-q", str(wheel)], check=True, timeout=600,
                           capture_output=True)
            exe = tmp / "venv" / "bin" / "agentdiff"
            elsewhere = tmp / "elsewhere"
            elsewhere.mkdir()
            ver = subprocess.run([str(exe), "--version"], cwd=str(elsewhere), capture_output=True, text=True)
            self.assertEqual(ver.returncode, 0, ver.stderr)
            self.assertTrue(ver.stdout.startswith("agentdiff "))
            out = elsewhere / "out"
            done = subprocess.run([str(exe), "batch", str(ROOT / "demo" / "traces"), "-o", str(out)],
                                  cwd=str(elsewhere), capture_output=True, text=True, timeout=600)
            self.assertEqual(done.returncode, 0, done.stderr[-1500:])
            page = (out / "report.html").read_text(encoding="utf-8")
            self.assertIn("AgentDiff", page)
            self.assertIn("focus-frame", page, "the installed page is the current blocks page")
            dry = subprocess.run([str(exe), "duel", "--dry-run", "--prompt", "x", "--workspace", str(elsewhere),
                                  "--codex-bin", str(FAKES / "fake_codex.py"),
                                  "--claude-bin", str(FAKES / "fake_claude.py")],
                                 cwd=str(elsewhere), capture_output=True, text=True, env=_env(), timeout=120)
            self.assertEqual(dry.returncode, 0, dry.stderr[-1500:])


if __name__ == "__main__":
    unittest.main()
