"""Builds the report page into the package whenever a wheel is built.

The page every command writes is assembled from ``web/blocks/`` by
``web/build_blocks.py``, and its copy inside the package
(``agentdiff/page/``) is a build output, not a tracked file. Without this
hook, ``pip install git+https://github.com/sugeerth/Agenttracing`` built
from a fresh clone installed a package with no page to write. Everything
else about the build is in ``pyproject.toml``.
"""

import subprocess
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithPage(build_py):
    def run(self):
        script = Path(__file__).resolve().parent / "web" / "build_blocks.py"
        if script.is_file():
            subprocess.run([sys.executable, str(script)], check=True, stdout=subprocess.DEVNULL)
        super().run()


setup(cmdclass={"build_py": BuildWithPage})
