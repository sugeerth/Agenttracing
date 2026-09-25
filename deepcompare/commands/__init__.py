"""Command implementations behind the CLI, one module per command.

``deepcompare.cli`` owns the parser and ``main``; each module here owns
one subcommand and exposes ``register(subparsers)`` (its argparse
surface) and ``run(args) -> int`` (its body).  ``cli.COMMANDS`` lists
them in ``--help`` order; adding a command is adding one module and one
entry there.

Shared pieces, each kept once:

- ``_io`` — the load-run-write shape: ``load_traces`` (SCHEMA validation,
  the ``harness`` block kept beside the typed trajectory, run ids from
  the runs layout's filenames) and ``write_outputs`` (``report_<task>.json``,
  ``aggregate.json``, ``report.html``, or ``fleet.json`` for a fleet).
- ``_common`` — the argument groups two or more commands share and the
  helpers that read them: the CI artifacts and exit-code policy, the
  provider options, the trace-database source.
- ``paths`` — where the page templates live.

The commands that may talk to a network (run, loop, replay, judge, why,
watch) and the hermetic replay commands (rerun, context, checkpoint)
import the harness inside their ``run`` function, so importing this
package loads no network code (pinned by
``tests/test_harness.py::TestNetworkBoundary``).
"""
