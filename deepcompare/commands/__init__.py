"""Command implementations behind the CLI, one module per concern.

``deepcompare.cli`` owns the parser and ``main``; the modules here own
the command bodies.  ``live`` holds every command that may talk to a
network (run, replay, why) — each imports the harness lazily inside its
function, so importing this package loads no network code.
"""
