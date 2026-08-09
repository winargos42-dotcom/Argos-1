from __future__ import annotations

import os
import shutil
from pathlib import Path


class PersistentStateError(RuntimeError):
    """Raised when ARGOS persistent state cannot be prepared safely."""


def _is_empty(path: Path) -> bool:
    return not any(path.iterdir())


def _seed(source: Path, target: Path) -> None:
    if not source.exists() or not source.is_dir() or not _is_empty(target):
        return

    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)


def _replace_with_symlink(runtime_path: Path, persistent_path: Path) -> None:
    if runtime_path.is_symlink():
        if runtime_path.resolve() == persistent_path.resolve():
            return
        runtime_path.unlink()
    elif runtime_path.exists():
        if runtime_path.is_dir():
            shutil.rmtree(runtime_path)
        else:
            runtime_path.unlink()

    os.symlink(persistent_path, runtime_path, target_is_directory=True)


def prepare_persistent_state(app_root: Path, state_root: Path) -> dict[str, str]:
    """Seed and redirect mutable ARGOS state to a persistent root.

    Existing non-empty persistent directories always win over image defaults.
    The operation is idempotent and only redirects ``data`` and ``config``.
    """

    try:
        app_root = app_root.resolve()
        state_root.mkdir(parents=True, exist_ok=True)
        if not state_root.is_dir():
            raise PersistentStateError(f"state root is not a directory: {state_root}")

        mapping: dict[str, str] = {}
        for name in ("data", "config"):
            runtime_path = app_root / name
            persistent_path = state_root / name
            persistent_path.mkdir(parents=True, exist_ok=True)
            _seed(runtime_path, persistent_path)
            _replace_with_symlink(runtime_path, persistent_path)
            mapping[name] = str(persistent_path.resolve())

        return mapping
    except PersistentStateError:
        raise
    except Exception as exc:
        raise PersistentStateError(str(exc)) from exc
