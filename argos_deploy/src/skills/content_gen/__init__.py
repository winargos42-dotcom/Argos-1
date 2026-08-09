"""Compatibility package for the restored content generator skill.

The preserved runtime implementation lives in the legacy flat module
``src/skills/content_gen.py``. This package has the same import name, so
Python resolves the package first. Load the preserved flat module explicitly
and export its ContentGen class instead of the unrelated stale skill.py copy.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

__version__ = "1.3.0"

_IMPL_NAME = "src.skills._content_gen_flat_impl"
_IMPL_PATH = Path(__file__).resolve().parent.parent / "content_gen.py"


def _load_preserved_module():
    module = sys.modules.get(_IMPL_NAME)
    if module is not None:
        return module

    spec = importlib.util.spec_from_file_location(_IMPL_NAME, _IMPL_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load preserved content generator from {_IMPL_PATH}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[_IMPL_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_IMPL_NAME, None)
        raise
    return module


_impl = _load_preserved_module()
ContentGen = _impl.ContentGen


def register(core=None):
    """Return the preserved runtime for SkillLoader lifecycle resolution."""
    return ContentGen()


__all__ = ["ContentGen", "register"]
