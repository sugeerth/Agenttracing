"""The entry point a frozen build runs: the agentdiff command line."""

import sys

from agentdiff.cli import main

if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # the reader went away (| head): nothing more to say
        sys.exit(0)
