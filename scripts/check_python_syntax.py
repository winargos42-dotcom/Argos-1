#!/usr/bin/env python3
"""Validate tracked ARGOS Python sources without importing or executing them."""

from __future__ import annotations

import argparse
from collections import Counter
import os
from pathlib import Path
import subprocess


EXCLUDED_PARTS = {
    ".git", ".npm-cache", ".venv", ".venv_arc", "venv", "__pycache__",
    "node_modules", ".pio", ".buildozer",
}
EXCLUDED_PREFIXES = (
    "argos_deploy/backups/",
    "argos_deploy/.argos_patch_backups/",
    "argos_deploy/tmp/kolibrios/",
    "argos_deploy/claude-code-templates/",
    "argos_deploy/claude-code-config-main/",
    "argos_deploy/mempalace-develop/",
    "argos_deploy/openai-chatkit-advanced-samples/",
    "argos_deploy/piper1-gpl-main/",
)


def tracked_python_sources(root: Path) -> tuple[list[Path], Counter]:
    """Return first-party tracked paths and an auditable exclusion count."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", "*.py"], cwd=root,
            capture_output=True, check=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Could not list tracked Python files: {exc}") from exc
    selected = []
    excluded = Counter()
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        relative = os.fsdecode(raw)
        path = Path(relative)
        reason = next((p.rstrip("/") for p in EXCLUDED_PREFIXES if relative.startswith(p)), None)
        if reason is None:
            reason = next((part for part in path.parts if part in EXCLUDED_PARTS), None)
        if reason is not None:
            excluded[reason] += 1
        else:
            selected.append(root / path)
    return sorted(selected), excluded


def print_selection(files: list[Path], excluded: Counter) -> None:
    """Print exactly which source categories participate in validation."""
    print(f"Tracked Python files: {len(files) + sum(excluded.values())}")
    print(f"First-party Python files: {len(files)}")
    print(f"Excluded Python files: {sum(excluded.values())}")
    for reason, count in sorted(excluded.items()):
        print(f"  {reason}: {count}")


def check_source(path: Path, root: Path) -> None:
    """Compile UTF-8 source in memory, without executing it or creating pyc files."""
    source = path.read_text(encoding="utf-8-sig")
    compile(source, str(path.relative_to(root)), "exec", dont_inherit=True)


def main() -> int:
    """Run the tracked-source check and fail on missing files or invalid Python."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    root = parser.parse_args().root.resolve()
    try:
        files, excluded = tracked_python_sources(root)
    except RuntimeError as exc:
        print(exc)
        return 1
    print_selection(files, excluded)
    if not files:
        print("No tracked first-party Python files")
        return 1
    failures = 0
    for path in files:
        try:
            check_source(path, root)
        except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
            failures += 1
            print(f"Syntax check failed: {path.relative_to(root)}: {exc}")
    print(f"Python syntax: {len(files) - failures} passed, {failures} failed")
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
