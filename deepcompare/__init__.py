"""DeepCompare AI — git diff for AI agents.

Pure-stdlib engine that aligns two agent trajectories on the same task,
detects divergences, attributes failures, and computes metric deltas per
SCHEMA.md.
"""

from .reasoning import read_trace
from .report import compare
from .trace import Trajectory

__all__ = ["compare", "read_trace", "Trajectory"]
__version__ = "0.9.0"
