"""Module entry point: ``python -m deepcompare``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
