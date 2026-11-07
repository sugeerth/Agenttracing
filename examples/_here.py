"""Puts the repository on the import path, so each example runs from a plain checkout: python3 examples/<name>.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
