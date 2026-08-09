"""Shared logging helpers for ARGOS.

This module is intentionally lightweight so the server runtime can initialize
before optional AI, vision, audio, and hardware dependencies are loaded.
"""
from __future__ import annotations

import logging
import os
import sys
import threading

_CONFIGURED = False
_LOCK = threading.Lock()


def _configured_level() -> int:
    value = os.getenv("ARGOS_LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, value, logging.INFO)


def _configure_argos_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    with _LOCK:
        if _CONFIGURED:
            return
        logger = logging.getLogger("argos")
        logger.setLevel(_configured_level())
        logger.propagate = False
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            logger.addHandler(handler)
        _CONFIGURED = True


def get_logger(name: str = "argos") -> logging.Logger:
    """Return an ARGOS logger configured for console/container output."""
    _configure_argos_logging()
    logger = logging.getLogger(name or "argos")
    logger.setLevel(_configured_level())
    if logger.name != "argos":
        logger.propagate = True
    return logger
