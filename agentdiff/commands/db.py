"""The ``db`` command: the trace database (SQLite) — import directories
of traces, summarise, query, full-text search, list checkpoints, export
matching traces back out as JSON files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..tracedb import TraceDB

__all__ = ["register", "run"]


def register(subparsers) -> None:
    parser = subparsers.add_parser("db", help="the trace database (SQLite): import directories of traces, "
                                             "summarise, query, full-text search, export")
    parser.add_argument("--db", default="traces.sqlite", help="database file (default traces.sqlite)")
    db_sub = parser.add_subparsers(dest="db_cmd", required=True)
    p_db_import = db_sub.add_parser("import", help="ingest every trace JSON in the directories (idempotent by trace id)")
    p_db_import.add_argument("dirs", nargs="+")
    p_db_import.add_argument("--source", default="import", help="provenance label (default import)")
    p_db_summary = db_sub.add_parser("summary", help="what the store holds")
    p_db_summary.add_argument("--json", action="store_true")
    p_db_query = db_sub.add_parser("query", help="list traces by task, family, agent, outcome, source")
    p_db_query.add_argument("--task"); p_db_query.add_argument("--family"); p_db_query.add_argument("--agent")
    p_db_query.add_argument("--outcome", choices=["any", "solved", "failed"], default="any")
    p_db_query.add_argument("--source", dest="source_filter", default=None)
    p_db_query.add_argument("--limit", type=int, default=50)
    p_db_query.add_argument("--json", action="store_true")
    p_db_search = db_sub.add_parser("search", help="full-text search over step names, inputs and outputs")
    p_db_search.add_argument("text"); p_db_search.add_argument("--limit", type=int, default=50)
    p_db_ckpt = db_sub.add_parser("checkpoints", help="runs with checkpoints (the run-so-far at each step a watcher or recorder kept)")
    p_db_ckpt.add_argument("trace_id", nargs="?", default=None)
    p_db_export = db_sub.add_parser("export", help="write matching traces back out as JSON files")
    p_db_export.add_argument("-o", "--output", required=True)
    p_db_export.add_argument("--task"); p_db_export.add_argument("--family"); p_db_export.add_argument("--agent")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """The trace database: import, summary, query, search, export."""
    with TraceDB(args.db) as db:
        if args.db_cmd == "import":
            total = {"added": 0, "skipped": []}
            for directory in args.dirs:
                result = db.add_directory(directory, source=args.source)
                total["added"] += result["added"]
                total["skipped"] += result["skipped"]
            print(f"imported {total['added']} trace(s) into {args.db}" +
                  (f"; skipped {len(total['skipped'])}" if total["skipped"] else ""))
            for line in total["skipped"][:10]:
                print("  skipped " + line, file=sys.stderr)
            return 0
        if args.db_cmd == "summary":
            summary = db.summary()
            print(json.dumps(summary, indent=1, ensure_ascii=False) if args.json else
                  f"{summary['path']}: {summary['traces']} trace(s), {summary['steps']} step(s)"
                  + (", full-text search on" if summary["fts"] else "")
                  + "\n  agents: " + ", ".join(f"{a} {v['successes']}/{v['n']}" for a, v in summary["success_by_agent"].items())
                  + "\n  families: " + ", ".join(f"{k} ×{v}" for k, v in list(summary["by"]["family"].items())[:12])
                  + "\n  sources: " + ", ".join(f"{k or '?'} ×{v}" for k, v in summary["by"]["source"].items()))
            return 0
        if args.db_cmd == "query":
            success = None if args.outcome == "any" else (args.outcome == "solved")
            rows = db.query(limit=args.limit, task=args.task, family=args.family, agent=args.agent,
                            success=success, source=args.source_filter)
            if args.json:
                print(json.dumps(rows, indent=1, ensure_ascii=False))
            else:
                for r in rows:
                    print(f"{r['trace_id']:<44} {'✓' if r['success'] else '✗'} {r['steps']:>3} steps "
                          f"{(r['cost_usd'] or 0):>8.4f}$ {(r['latency_s'] or 0):>7.2f}s  {r['source'] or ''}")
                print(f"{len(rows)} of {db.count()} trace(s)")
            return 0
        if args.db_cmd == "search":
            hits = db.search(args.text, limit=args.limit)
            for h in hits:
                print(f"{h['trace_id']} step {h['idx']} {h['type']} {h['name'] or ''}: {(h['output'] or '')[:90]!r}")
            print(f"{len(hits)} hit(s)")
            return 0
        if args.db_cmd == "checkpoints":
            if args.trace_id:
                for c in db.checkpoints(args.trace_id):
                    print(f"{c['trace_id']} step {c['step']:>4} {c['label'] or ''} {c['source'] or ''} "
                          f"{len(c['trace'].get('steps') or [])} step(s) recorded")
            else:
                for c in db.checkpoint_ids():
                    print(f"{c['trace_id']:<44} {c['n']:>3} checkpoint(s), latest at step {c['latest']}")
            return 0
        if args.db_cmd == "export":
            out = Path(args.output)
            out.mkdir(parents=True, exist_ok=True)
            n = 0
            for row in db.query(task=args.task, family=args.family, agent=args.agent):
                data = db.get(row["trace_id"])
                if data is None:
                    continue
                (out / (row["trace_id"] + ".json")).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                n += 1
            print(f"exported {n} trace(s) to {out}")
            return 0
    print("error: unknown db command", file=sys.stderr)
    return 2
