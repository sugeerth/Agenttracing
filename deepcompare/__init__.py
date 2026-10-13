"""The name this package had before it was AgentDiff: ``deepcompare``.

Kept so code, hooks and notebooks written against the old name still run.
``import deepcompare.report`` returns the very module ``agentdiff.report``
(one module object, so classes and state are shared, not copied), and
``python -m deepcompare`` is ``python -m agentdiff``. New code imports
``agentdiff``.
"""

import importlib
import importlib.abc
import importlib.util
import sys

import agentdiff as _agentdiff

_OLD, _NEW = "deepcompare", "agentdiff"


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        # this package and its own __main__ are real files; the rest alias
        if name in (_OLD, _OLD + ".__main__") or not name.startswith(_OLD + "."):
            return None
        return importlib.util.spec_from_loader(name, self)

    def create_module(self, spec):
        return importlib.import_module(_NEW + spec.name[len(_OLD):])

    def exec_module(self, module):
        # already executed under its own name; alias it, do not run it twice
        sys.modules[module.__spec__.name] = module


if not any(isinstance(f, _Alias) for f in sys.meta_path):
    sys.meta_path.insert(0, _Alias())

# the package itself: the same attributes as agentdiff
globals().update({k: v for k, v in vars(_agentdiff).items() if not k.startswith("__")})
__all__ = getattr(_agentdiff, "__all__", [])
__version__ = getattr(_agentdiff, "__version__", None)
