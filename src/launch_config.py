"""Launch argument compatibility helpers for ARGOS."""
from __future__ import annotations


def normalize_launch_args(args):
    if args is None:
        return []
    return [str(value).strip() for value in args if value is not None and str(value).strip()]
