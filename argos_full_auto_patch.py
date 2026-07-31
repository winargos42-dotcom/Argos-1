
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ARGOS full auto patcher

What it does:
1) Recursively scans Python files in the current project.
2) Creates backups before any change.
3) Fixes common Agent-generated code corruption patterns:
   - def init(...) -> def __init__(...)
   - loadjson -> load_json
   - savejson -> save_json
   - readtext -> read_text
   - writetext -> write_text
   - shellsplit -> shell_split
   - nowts -> now_ts
   - Optionalsubprocess.Popen -> Optional[subprocess.Popen]
   - restoreifpossible -> _restore_if_possible
   - pidexists -> _pid_exists
   - missingok -> missing_ok
   - existok -> exist_ok
   - preexecfn -> preexec_fn
   - startedat -> started_at
   - lasterror -> last_error
   - statestore -> state_store
   - logfile -> log_file
   - CRETATENEWPROCESSGROUP/CREATENEWPROCESSGROUP -> CREATE_NEW_PROCESS_GROUP
4) Restores common dataclass init bug:
   - RuntimeState(data) -> RuntimeState(**data)
   - RuntimeState(asdict(self.state)) -> RuntimeState(**asdict(self.state))
5) Adds a safe Agent fallback helper when files contain the text
   "Ollama недоступен в текущем режиме" or "Ollama недоступен"
   and injects ALLOW_AGENT_NO_OLLAMA env flag support.
6) Validates Python syntax after every file patch. If broken, auto-rolls back.
7) Writes a patch report.

Usage:
    python argos_full_auto_patch.py
    python argos_full_auto_patch.py --root /path/to/project
    python argos_full_auto_patch.py --dry-run
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import difflib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple

SKIP_DIRS = {
    ".git", ".idea", ".vscode", "__pycache__", ".mypy_cache", ".pytest_cache",
    "node_modules", "dist", "build", "site-packages", ".venv", "venv", "env",
}
BACKUP_DIRNAME = ".argos_patch_backups"

TOKEN_REPLACEMENTS = [
    (r'\bloadjson\b', 'load_json'),
    (r'\bsavejson\b', 'save_json'),
    (r'\breadtext\b', 'read_text'),
    (r'\bwritetext\b', 'write_text'),
    (r'\bshellsplit\b', 'shell_split'),
    (r'\bnowts\b', 'now_ts'),
    (r'\bstatestore\b', 'state_store'),
    (r'\blogfile\b', 'log_file'),
    (r'\bstartedat\b', 'started_at'),
    (r'\blasterror\b', 'last_error'),
    (r'\bmissingok\b', 'missing_ok'),
    (r'\bexistok\b', 'exist_ok'),
    (r'\bpreexecfn\b', 'preexec_fn'),
    (r'\brestoreifpossible\b', '_restore_if_possible'),
    (r'\bpidexists\b', '_pid_exists'),
    (r'\bOptionalsubprocess\.Popen\b', 'Optional[subprocess.Popen]'),
    (r'\bsubprocess\.CREATENEWPROCESSGROUP\b', 'subprocess.CREATE_NEW_PROCESS_GROUP'),
    (r'\bsubprocess\.CREATENEWPROCESSGROUP\b', 'subprocess.CREATE_NEW_PROCESS_GROUP'),
    (r'\bdef\s+init\s*\(', 'def __init__('),
]

EXACT_REPLACEMENTS = [
    ('RuntimeState(data)', 'RuntimeState(**data)'),
    ('RuntimeState(asdict(self.state))', 'RuntimeState(**asdict(self.state))'),
]
