"""The entry point a frozen build runs: the agentdiff command line (a double-click opens the hub)."""

import sys

from agentdiff.cli import download_main

if __name__ == "__main__":
    sys.exit(download_main())
