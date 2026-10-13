"""Use an evolved harness in your own Claude Code, and take it out again.

A self-evolving harness (``self-evolve``) ends with instructions it was
told, tools it was denied and maybe a turn cap, each kept only because a
paired test said it helped. ``adopt`` writes them where Claude Code reads
them every session in a project:

- the instructions, as a marked block in the project's ``CLAUDE.md``;
- the denied tools, in ``permissions.deny`` of ``.claude/settings.local.json``
  (which git ignores, as the guard's hooks do).

A turn cap has no setting, so it is reported, with the flag. What was
written is recorded in ``.claude/agentdiff-harness.json``, so ``unadopt``
takes out exactly that and nothing a person wrote.

The paired test gave the instructions to Claude Code with
``--append-system-prompt``. ``CLAUDE.md`` gives the same words through
another channel, the project's instructions, which is why the result says
so instead of claiming the tested effect carries over unchanged.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

__all__ = ["adopt", "unadopt", "BEGIN", "END", "RECORD"]

BEGIN = "<!-- agentdiff harness"
END = "<!-- /agentdiff harness -->"
RECORD = Path(".claude") / "agentdiff-harness.json"
SETTINGS = Path(".claude") / "settings.local.json"
_BLOCK = re.compile(r"\n*<!-- agentdiff harness.*?<!-- /agentdiff harness -->\n?", re.S)


def _block(harness: dict, source: str) -> str:
    lines = [f"{BEGIN} v{harness.get('version', 0)}, from {source}: kept by a paired test. "
             "`agentdiff self-evolve --unadopt` removes this block. -->",
             "## From a self-evolving harness", ""]
    lines += [f"- {i}" for i in harness.get("instructions") or []]
    lines += ["", END]
    return "\n".join(lines) + "\n"


def adopt(harness: dict, project: Path, *, source: str = "self-evolve") -> dict:
    """Write ``harness`` into ``project``'s Claude Code. Returns what was written, and what could not be."""
    project = Path(project)
    if not project.is_dir():
        raise OSError(f"{project} is not a folder")
    instructions = list(harness.get("instructions") or [])
    deny = list(harness.get("deny_tools") or [])
    if not instructions and not deny and not harness.get("max_turns"):
        return {"wrote": [], "nothing": True, "why": "the harness ends as the agent ships: nothing to adopt"}
    unadopt(project)  # one adopted harness at a time: the newest replaces the last
    wrote, added = [], []
    md = project / "CLAUDE.md"
    created = instructions and not md.is_file()
    if instructions:
        text = md.read_text(encoding="utf-8") if md.is_file() else ""
        text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + _block(harness, source)
        md.write_text(text, encoding="utf-8")
        wrote.append(str(md))
    if deny:
        path = project / SETTINGS
        data = json.loads(path.read_text(encoding="utf-8") or "{}") if path.is_file() else {}
        have = data.setdefault("permissions", {}).setdefault("deny", [])
        added = [t for t in deny if t not in have]
        have.extend(added)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        wrote.append(str(path))
    record = {"source": source, "version": harness.get("version", 0), "instructions": instructions,
              "deny_added": added, "max_turns": harness.get("max_turns"), "created_claude_md": bool(created)}
    (project / RECORD).parent.mkdir(parents=True, exist_ok=True)
    (project / RECORD).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    out = {"wrote": wrote, "instructions": len(instructions), "deny": deny, "deny_added": added,
           "record": str(project / RECORD), "nothing": False}
    if harness.get("max_turns"):
        out["max_turns"] = harness["max_turns"]
    return out


def unadopt(project: Path) -> Optional[dict]:
    """Take out what ``adopt`` wrote, and only that. None when nothing was adopted here."""
    project = Path(project)
    rec_path = project / RECORD
    md = project / "CLAUDE.md"
    removed = []
    record: dict = {}
    if rec_path.is_file():
        try:
            record = json.loads(rec_path.read_text(encoding="utf-8"))
        except ValueError:
            record = {}
    if md.is_file():
        text = md.read_text(encoding="utf-8")
        cut = _BLOCK.sub("\n", text)
        if cut != text:
            cut = cut.strip("\n")
            if cut or not record.get("created_claude_md"):
                md.write_text(cut + "\n" if cut else "", encoding="utf-8")
            else:
                md.unlink()  # adopt made the file, and nothing else is in it
            removed.append(str(md))
    if not rec_path.is_file():
        return {"removed": removed} if removed else None
    added = record.get("deny_added") or []
    path = project / SETTINGS
    if added and path.is_file():
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        perms = data.get("permissions") or {}
        perms["deny"] = [t for t in perms.get("deny") or [] if t not in added]
        if not perms["deny"]:
            perms.pop("deny")
        if not perms:
            data.pop("permissions", None)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        removed.append(str(path))
    rec_path.unlink()
    return {"removed": removed, "version": record.get("version")}
