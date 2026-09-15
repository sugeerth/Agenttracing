"""``agentdiff chat``: a grounded conversation about one output directory
under the narration covenant.

What is pinned is not prose — the engine writes none — but the contract:
the brief is built from the engine's own fields and every number in it
is allowed; without a provider the command prints the brief and the
prompt; with a scripted provider every answer is printed with its check
attached — an answer that invents a number or cites a fact that does not
exist is flagged beside the sentence, never dropped and never trusted; a
faithful answer is said to be one; the exit code is the provider's, not
the model's; the harness is imported only inside the command's ``run``;
and no credential ever reaches stdout or stderr.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from deepcompare.cli import main  # noqa: E402
from deepcompare.harness.chat import ask, converse, format_turn  # noqa: E402
from deepcompare.harness.providers import ProviderError, ScriptedProvider  # noqa: E402
from deepcompare.narrate import chat_brief, chat_prompt, check_narration, coevolution_brief  # noqa: E402
from test_evolvecompare import SAMPLES, _lineage_json, alpha_gens, beta_gens, write_lineage  # noqa: E402


def run_cli(*argv, stdin: str = ""):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch("sys.stdin", io.StringIO(stdin)):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class _Fixture(unittest.TestCase):
    """One ``evolve A --against B`` output over the hand-built lineages of
    ``test_evolvecompare``: pair reports, an aggregate with ``evolution``,
    ``coevolution`` and ``evolution_compare.evals``."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="chat-cli-"))
        ra = write_lineage(cls.tmp / "alpha", alpha_gens(), _lineage_json("alpha"))
        rb = write_lineage(cls.tmp / "beta", beta_gens(), _lineage_json("beta"))
        cls.out_dir = cls.tmp / "out"
        code, _, err = run_cli("evolve", str(ra), "--against", str(rb), "-o", str(cls.out_dir),
                               "--samples", str(SAMPLES))
        assert code == 0, err
        cls.aggregate = json.loads((cls.out_dir / "aggregate.json").read_text(encoding="utf-8"))
        cls.reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(cls.out_dir.glob("report_*.json"))]
        cls.brief = chat_brief(cls.aggregate, cls.reports)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def script(self, *texts) -> Path:
        path = self.tmp / f"script_{abs(hash(texts))}.json"
        path.write_text(json.dumps([{"text": t} for t in texts]), encoding="utf-8")
        return path

    def fact(self, source_prefix: str) -> dict:
        return next(f for f in self.brief["facts"] if f["source"].startswith(source_prefix))


# ---------------------------------------------------------------- the brief

class BriefTest(_Fixture):
    def test_the_brief_numbers_every_fact_once_and_allows_every_number_in_it(self):
        ids = [f["id"] for f in self.brief["facts"]]
        self.assertEqual(ids, [f"F{i + 1}" for i in range(len(ids))])
        self.assertEqual(self.brief["shape"], "chat")
        for fact in self.brief["facts"]:
            self.assertEqual(check_narration(self.brief, fact["text"])["unsupported_numbers"], [],
                             f"{fact['id']} carries a number the brief does not allow")

    def test_the_brief_reuses_the_aggregate_and_the_pair_briefs(self):
        sources = {f["source"] for f in self.brief["facts"]}
        self.assertIn("aggregate", sources, "the batch's own facts")
        self.assertTrue(any(s.startswith("task ta: ") for s in sources), "each pair's facts, prefixed by its task")
        self.assertTrue(any(s.startswith("task ta: reading.") for s in sources))

    def test_the_coevolution_brief_carries_the_ledger_the_hindsight_the_integrity_and_the_recommendation(self):
        co = self.aggregate["coevolution"]
        sources = [f["source"] for f in self.brief["facts"]]
        for needed in ("coevolution.eval_generation", "coevolution.metric", "coevolution.ledger", "coevolution.matrix",
                       "coevolution.hindsight", "coevolution.caught_at", "coevolution.probe", "coevolution.recommended",
                       "coevolution.integrity", "coevolution.external", "coevolution.gap", "coevolution.flow",
                       "evolution", "evolution.step", "evolution.recommended"):
            self.assertIn(needed, sources, needed)
        ledger = [f for f in self.brief["facts"] if f["source"] == "coevolution.ledger"]
        self.assertEqual(len(ledger), len(co["ledger"]), "one fact per ledger row")
        for fact, row in zip(ledger, co["ledger"]):
            self.assertIn(row["decision"], fact["text"])
            self.assertIn(row["reason"], fact["text"])
            self.assertIn(row["spec_id"], fact["text"])
        for mid, c in co["hindsight"]["caught_at"].items():
            self.assertTrue(any(f["source"] == "coevolution.caught_at" and f["text"].startswith(mid + ":")
                                and c["note"] in f["text"] for f in self.brief["facts"]), mid)
        rec = self.fact("coevolution.recommended")
        self.assertIn(co["recommended"]["why"], rec["text"])
        integ = self.fact("coevolution.integrity")
        self.assertIn(str(co["integrity"]["multiplicity"]["tested"]), integ["text"])
        self.assertIn(co["integrity"]["gap"], self.fact("coevolution.gap")["text"])

    def test_the_evals_comparison_is_in_the_brief_when_the_directory_is_a_comparison(self):
        evals = self.aggregate["evolution_compare"]["evals"]
        rows = [f for f in self.brief["facts"] if f["source"] == "evolution_compare.evals.lineage"]
        self.assertEqual([r["value"]["label"] for r in rows], ["alpha", "beta"])
        transfers = [f for f in self.brief["facts"] if f["source"] == "evolution_compare.evals.transfer"]
        self.assertEqual([t["text"] for t in transfers], [t["reading"] for t in evals["transfer"]])
        self.assertEqual(self.fact("evolution_compare.evals.rule")["text"], evals["transfer_rule"])
        reading = next(f for f in self.brief["facts"] if f["source"] == "evolution_compare.evals")
        self.assertEqual(reading["text"], evals["reading"])
        self.assertIn("no eval is declared the better one", reading["text"])
        # a plain lineage has no comparison and no evals facts
        plain = chat_brief({k: v for k, v in self.aggregate.items() if k != "evolution_compare"}, [])
        self.assertFalse(any(f["source"].startswith("evolution_compare") for f in plain["facts"]))
        self.assertTrue(any(f["source"] == "coevolution.ledger" for f in plain["facts"]))

    def test_an_unreadable_eval_is_one_fact_saying_so(self):
        brief = coevolution_brief({"measurable": False, "reason": "no step to walk"})
        self.assertEqual([f["text"] for f in brief["facts"]], ["the eval cannot be read: no step to walk"])

    def test_the_prompt_asks_for_citations_and_for_not_in_the_report(self):
        prompt = chat_prompt(self.brief)
        self.assertIn("[F7]", prompt)
        self.assertIn('say "not in the report"', prompt)
        self.assertIn("machine-checked", prompt)
        self.assertIn("declare no winner", prompt)
        self.assertIn(f"[{self.brief['facts'][-1]['id']}]", prompt)
        self.assertEqual(prompt, chat_prompt(chat_brief(self.aggregate, self.reports)), "deterministic")


# ---------------------------------------------------------------- the turn and the check

class TurnTest(_Fixture):
    def test_an_answer_that_cites_a_fact_and_its_number_is_faithful(self):
        fact = self.fact("coevolution.integrity")
        tested = self.aggregate["coevolution"]["integrity"]["multiplicity"]["tested"]
        provider = ScriptedProvider([{"text": f"The eval tested {tested} candidates [{fact['id']}]."}])
        turn = ask(provider, self.brief, "how many candidates did the eval test?")
        self.assertTrue(turn["check"]["faithful"], turn["check"])
        self.assertEqual(turn["check"]["citations"], 1)
        self.assertIn("faithful:", format_turn(turn))
        self.assertIn(f"[{fact['id']}]", format_turn(turn))

    def test_an_answer_that_invents_a_number_is_flagged_beside_the_sentence(self):
        provider = ScriptedProvider([{"text": "The eval adopted 4242 metrics at g1→g2 [F2]."}])
        turn = ask(provider, self.brief, "what did it learn?")
        self.assertFalse(turn["check"]["faithful"])
        self.assertEqual(turn["check"]["unsupported_numbers"], ["4242"])
        text = format_turn(turn)
        self.assertIn("The eval adopted 4242 metrics", text, "the sentence is printed, never dropped")
        self.assertIn("1 number not in the report: 4242", text)
        self.assertIn("flagged", text)

    def test_a_citation_naming_no_fact_is_flagged(self):
        provider = ScriptedProvider([{"text": "It is so [F99999]."}])
        turn = ask(provider, self.brief, "is it?")
        self.assertEqual(turn["check"]["invalid_citations"], ["F99999"])
        self.assertIn("1 citation naming no fact: F99999", format_turn(turn))

    def test_the_turn_carries_the_prompt_the_history_and_the_question_in_order(self):
        seen = []
        provider = ScriptedProvider(lambda messages, tools: seen.append((messages, tools)) or {"text": "ok"})
        history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "answered"}]
        ask(provider, self.brief, "now?", history)
        messages, tools = seen[0]
        self.assertIsNone(tools, "a chat declares no tools")
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
        self.assertEqual(messages[0]["content"], chat_prompt(self.brief))
        self.assertEqual(messages[-1]["content"], "now?")

    def test_converse_keeps_history_skips_blanks_and_stops_at_exit(self):
        lengths = []
        provider = ScriptedProvider(lambda messages, tools: lengths.append(len(messages)) or {"text": "a reply [F1]"})
        printed = []
        code = converse(provider, self.brief, ["first?", "", "  ", "second?", "exit", "never asked?"], out=printed.append)
        self.assertEqual(code, 0)
        self.assertEqual(lengths, [2, 4], "the second question carries the first turn as history")
        self.assertEqual([p for p in printed if p.startswith("> ")], ["> first?", "> second?"])

    def test_a_provider_failure_ends_the_conversation_with_its_own_exit_code_and_no_key(self):
        provider = ScriptedProvider([{"text": "one [F1]"}])
        printed, errors = [], []
        code = converse(provider, self.brief, ["a?", "b?"], out=printed.append, err=errors.append)
        self.assertEqual(code, 3)
        self.assertEqual([p for p in printed if p.startswith("> ")], ["> a?", "> b?"],
                         "the turn before the failure stands and the failed question is shown")
        self.assertEqual(sum(1 for p in printed if "faithful" in p or "flagged" in p), 1, "only one answer was printed")
        self.assertEqual(errors, ["error: provider failed: scripted provider has no more turns"])
        with self.assertRaises(ProviderError):
            ask(provider, self.brief, "c?")


# ---------------------------------------------------------------- the command

class CommandTest(_Fixture):
    def test_without_a_provider_the_command_prints_the_prompt_and_the_question(self):
        code, out, err = run_cli("chat", str(self.out_dir), "--ask", "which generation should I keep?")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith(chat_prompt(self.brief)))
        self.assertIn("QUESTION: which generation should I keep?", out)
        self.assertIn(f"{len(self.brief['facts'])} fact(s) in the brief", err)
        code, out, _ = run_cli("chat", str(self.out_dir))
        self.assertEqual(code, 0)
        self.assertNotIn("QUESTION:", out)

    def test_ask_with_a_script_prints_the_answer_and_its_check(self):
        fact = self.fact("coevolution.recommended")
        rec = self.aggregate["coevolution"]["recommended"]
        path = self.script(f"Keep {rec['base']}: both rules pick it [{fact['id']}].")
        code, out, err = run_cli("chat", str(self.out_dir), "--script", str(path), "--ask", "which generation?")
        self.assertEqual(code, 0, err)
        self.assertIn("> which generation?", out)
        self.assertIn(f"Keep {rec['base']}: both rules pick it [{fact['id']}].", out)
        self.assertIn("faithful:", out)
        self.assertNotIn("not in the report", out)

    def test_an_invented_number_is_printed_flagged_and_the_exit_code_stays_zero(self):
        path = self.script("The lineage ran 4242 episodes and the eval adopted 98765.5 metrics [F1].")
        code, out, err = run_cli("chat", str(self.out_dir), "--script", str(path), "--ask", "how many?")
        self.assertEqual(code, 0, err)
        self.assertIn("The lineage ran 4242 episodes", out)
        self.assertIn("2 numbers not in the report: 4242, 98765.5", out)
        self.assertIn("flagged", out)

    def test_the_repl_reads_stdin_until_exit_and_keeps_the_conversation(self):
        fact = self.fact("coevolution.eval_generation")
        path = self.script(f"It grew [{fact['id']}].", "Not in the report.")
        code, out, err = run_cli("chat", str(self.out_dir), "--script", str(path),
                                 stdin="what did it learn?\n\nwhat is the weather?\nexit\nnever asked\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count("> "), 2)
        self.assertIn("> what did it learn?", out)
        self.assertIn("> what is the weather?", out)
        self.assertIn("Not in the report.", out)
        self.assertNotIn("never asked", out)

    def test_an_exhausted_script_is_a_provider_failure_exit_3(self):
        path = self.script("only one [F1]")
        code, out, err = run_cli("chat", str(self.out_dir), "--script", str(path), stdin="a?\nb?\n")
        self.assertEqual(code, 3)
        self.assertIn("> a?", out)
        self.assertIn("provider failed", err)

    def test_a_scripted_provider_spec_works_through_provider_too(self):
        path = self.script("spec route [F1]")
        code, out, err = run_cli("chat", str(self.out_dir), "--provider", f"scripted:{path}", "--ask", "q?")
        self.assertEqual(code, 0, err)
        self.assertIn("spec route [F1]", out)

    def test_usage_errors_exit_2(self):
        code, _, err = run_cli("chat", str(self.tmp / "missing"), "--ask", "q?")
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err)
        empty = self.tmp / "empty"
        empty.mkdir(exist_ok=True)
        code, _, err = run_cli("chat", str(empty))
        self.assertEqual(code, 2)
        self.assertIn("no aggregate.json", err)
        code, _, err = run_cli("chat", str(self.out_dir), "--provider", "nokind")
        self.assertEqual(code, 2)
        self.assertIn("kind:model", err)
        code, _, err = run_cli("chat", str(self.out_dir), "--script", str(self.tmp / "nofile.json"))
        self.assertEqual(code, 2)

    def test_no_credential_reaches_the_output(self):
        path = self.script("safe [F1]")
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-never-printed", "ANTHROPIC_API_KEY": "ak-never-printed"}):
            code, out, err = run_cli("chat", str(self.out_dir), "--script", str(path), "--ask", "q?",
                                     "--api-key-env", "OPENAI_API_KEY")
        self.assertEqual(code, 0, err)
        for secret in ("sk-never-printed", "ak-never-printed"):
            self.assertNotIn(secret, out)
            self.assertNotIn(secret, err)

    def test_the_command_imports_the_harness_only_inside_run(self):
        tree = ast.parse((ROOT / "deepcompare" / "commands" / "chat.py").read_text(encoding="utf-8"))
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertFalse(any("harness" in (getattr(n, "module", "") or "") for n in top))
        nested = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and "harness" in (n.module or "")]
        self.assertTrue(nested and all(n.col_offset > 0 for n in nested))
        # and the harness side imports the engine, never the other way round
        narrate = (ROOT / "deepcompare" / "narrate.py").read_text(encoding="utf-8")
        self.assertNotIn("harness", narrate)


if __name__ == "__main__":
    unittest.main()
