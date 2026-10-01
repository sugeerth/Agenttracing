# The hub

`agentdiff hub` is AgentDiff as a small platform. It is one process with
no database, standard library only, behind a sign-in. It shows every run
under a directory:
- duels, with their scoreboards
- reports
- in-band telemetry agents post to it (`docs/TELEMETRY.md`)

```bash
agentdiff hub                       # this directory, on http://127.0.0.1:8790/
agentdiff hub ~/work --port 8800    # another root
```

It prints the address, the sign-in and the lines an agent needs to post
telemetry:

```
AgentDiff hub: http://127.0.0.1:8790/   (root /home/me/work)
  sign in: demo / demo   (demo account; --no-demo turns it off)
  agents post telemetry: AGENTDIFF_HUB=http://127.0.0.1:8790 AGENTDIFF_HUB_TOKEN=…
```

## Signing in

- **The demo account** (`demo` / `demo` by default) exists when the hub
  serves this machine only. A known password on a network is no login,
  so beyond this machine it is off unless `--demo` turns it on.
  `--no-demo` turns it off anywhere. It is an ordinary user marked
  `demo`; a real user who takes the name is never touched.
- **Real users:** `agentdiff hub --add-user alice` asks for a password
  (or reads `AGENTDIFF_HUB_NEW_PASSWORD`), of at least 8 characters. It
  is stored as a salted PBKDF2-SHA256 hash in
  `.agentdiff-hub/users.json`, readable by its owner only.
  `--remove-user` removes one.
- **Sessions** are a random token in an `HttpOnly; SameSite=Strict`
  cookie. Every form that changes state carries its session's token,
  the login form a single-use one. After 5 failed sign-ins a name waits
  60 seconds. Sessions live in memory: restarting the hub signs everyone
  out.

## Settings

Every setting has one default, in `agentdiff/hub/config.py`. A file
(`.agentdiff-hub/hub.json`, or `--config`) overrides the defaults.
`AGENTDIFF_HUB_<NAME>` environment variables override the file, and
flags override everything. An unknown key in the file is refused, not
ignored.

```json
{"port": 8800, "title": "Team runs", "demo_password": "something-better", "session_hours": 8,
 "login_attempts": 5, "login_lockout_s": 60, "scan_depth": 3}
```

## Pages and the API

| route | what |
|---|---|
| `/` | every run under the root, newest first: kind, title, pass counts or hops |
| `/runs/<id>` | a duel's scoreboard, notes and the commands to keep a change; a telemetry run's timeline (one lane per process) and its hops |
| `/runs/<id>/page` | the report the run wrote |
| `/api/v1/runs` | the catalog as JSON |
| `POST /api/v1/telemetry` | an agent posts `{vector, prompt?, success?, answer?, model?}` with `Authorization: Bearer <ingest token>` |
| `/healthz` | liveness |

The ingest token is made once and kept in `.agentdiff-hub/ingest.token`
(owner only), so agent configurations survive a restart. A posted run is
kept in three forms:
- its vector
- its trajectory, under `.agentdiff-hub/telemetry/traces/`, which
  `agentdiff batch` reads like any trace directory
- what the poster said beside it

A run posted again as it grows replaces the stored copy only when it has
at least as many hops.

## Security

- **Network:** the hub binds to `127.0.0.1`. Anything else needs
  `--allow-remote`, because it shows every run under its root, including
  what the agents read.
- **The hub's own pages** run no script at all. Their policy is
  `default-src 'none'; style-src 'unsafe-inline'; form-action 'self'`.
- **A run's report** is a page that run wrote, with its own scripts and
  data from agents. It is served under
  `Content-Security-Policy: sandbox allow-scripts`, which gives it an
  origin of its own, so nothing in a trace can act as the signed-in user.
- **Paths:** nothing outside the root is ever served. Ids are hashes the
  catalog made, never paths.
- **Errors and redirects:** a sign-in returns only to a path on the hub,
  never to another site. One failing request returns a 500 without
  taking the hub down.

## Layout

| module | one job |
|---|---|
| `hub/config.py` | the settings, in layers |
| `hub/auth.py` | users, hashes, the demo account (`UserStore`: JSON file or memory) |
| `hub/sessions.py` | sessions, form tokens, the login throttle |
| `hub/catalog.py` | runs on disk, found by detectors (a new kind of run is one more detector) |
| `hub/ingest.py` | posted telemetry, kept as runs |
| `hub/views.py` | the pages |
| `hub/urls.py` | paths, queries and forms, without `urllib` (the engine's rule) |
| `hub/app.py` | request in, response out: every route, no sockets |
| `harness/hub_server.py` | the only part that listens: the HTTP adapter, the wiring, the client |

The app takes its collaborators as arguments, so a test, or an embedding,
swaps any of them. Nearly every test runs the app with no socket.
