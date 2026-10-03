# agentdiff to download and run

One file, nothing to install. Start it, or double-click it, and it opens
the hub in your browser on **your own Claude Code sessions**, read from
this machine and followed live as you work, beside a few example runs.

## Get it

| You have | Download | Start it |
|---|---|---|
| a Mac with Apple silicon | `agentdiff-darwin-arm64.tar.gz` | `./agentdiff` |
| an Intel Mac | `agentdiff-darwin-x86_64.tar.gz` | `./agentdiff` |
| Windows | `agentdiff-windows-amd64.zip` | `agentdiff.exe` |
| Linux | `agentdiff-linux-x86_64.tar.gz` | `./agentdiff` |
| any OS with Python 3.10+ | `agentdiff.pyz` | `python3 agentdiff.pyz` |

A tag `v*` publishes every file on the repository's release page, with a
`SHA256SUMS`. Every change to `packaging/` also builds them as the
artifacts of the **binaries** workflow run. Each archive holds the binary,
named `agentdiff`, and a `README.txt` with its own checksum. The archive
keeps the binary runnable, so there is no `chmod` to do.

**The first time.** The binaries are not notarised or signed.
- **macOS** asks before it runs one: right-click it, then Open, then Open.
  Or run `xattr -d com.apple.quarantine agentdiff`.
- **Windows SmartScreen** may warn: More info, then Run anyway.

## What starting it does

With no arguments (a double-click), it runs
`agentdiff hub --claude-code --examples --open`:

- **Your sessions.** It reads every session Claude Code wrote under
  `~/.claude/projects` (or `$CLAUDE_CONFIG_DIR/projects`) into
  `~/.agentdiff/claude-code` (or `$AGENTDIFF_HOME`). The newest three are
  read before the first page, the rest in the background. From then on it
  follows them: a session written to in the last five minutes is live, so
  its page and the Live page grow as you work.
- **The examples.** The examples are copied once to
  `~/.agentdiff/examples`. Each is marked *synthetic* or *recorded* wherever
  it is listed, and the Overview's numbers leave them out once you have runs
  of your own.
- **The browser.** It opens `http://127.0.0.1:8790/`; sign in with
  demo / demo. A second start finds the hub already running and opens it.
  If something else holds the port, it takes a free one and says which.
  Ctrl-C stops it.

Nothing leaves the machine. The hub listens on 127.0.0.1 only, the traces
are files in your home folder (the folder is readable by you alone), and no
part of reading a session talks to a network.

## A session in the hub

The importer is `agentdiff/claude_sessions.py`; the steps come from the
transcript reader, `claude_code.transcript_to_trajectory`.
- **Named by what you asked.** The task is the project and your first
  prompt. A context summary, a slash command or a system notice is not a
  prompt.
- **What you asked.** A tab lists each prompt you typed. For each it gives
  what the agent did until your next one: the time, the steps, the edits,
  the checks and how many failed, the errors, the tools it used most, and a
  link into the steps at that point.
- **Sub-agents as lanes.** Each sub-agent Claude Code started joins the
  session at the times it ran, on a lane named by what it was asked.
- **Never a failure nobody measured.** Nothing graded a session, so it is
  *not graded*: never counted as failed, never said to have got anything
  wrong. Its first step to change is *Give it a check*:
  `agentdiff guard --install --check '…'`, with the check command read from
  the session when it ran one. Every session after that is graded by it, and
  the guard keeps the agent from stopping before the check passes.
- **Long sessions.** A session of days opens on Phases, where loops of hours
  show as loops (`docs/LONGRUN.md`).

## Building it yourself

```bash
python packaging/build.py              # dist/agentdiff.pyz
python packaging/build.py --binary     # also the binary for this OS and its download archive (needs pyinstaller)
```

`tests/test_download.py` builds the archive and runs it from another
folder with an empty cache, and also runs the command line as a Windows
console would (cp1252). The binaries workflow unpacks each download on its
own OS and checks that it runs: it prints its version, writes a timeline,
and serves the hub with its script.
