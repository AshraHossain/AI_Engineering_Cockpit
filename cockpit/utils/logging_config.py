"""Centralized logging configuration for the cockpit platform.

Provides a single :func:`get_logger` entry point that configures a
non-default handler/formatter exactly once per logger name, honoring the
``LOG_LEVEL`` environment variable. Modules across the platform should use
this instead of ``print()`` or the root ``logging.basicConfig``.
"""

from __future__ import annotations

import logging
import os
import sys

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Marker attribute set on handlers we install, so repeated calls to
# get_logger() can detect our handler is already present and skip adding
# a duplicate (which would otherwise double- or triple-log messages).
_HANDLER_MARKER = "_cockpit_handler"


def _resolve_log_level() -> int:
    """Resolve the effective log level from the ``LOG_LEVEL`` env var.

    Returns:
        A ``logging`` module level constant (e.g. ``logging.INFO``). Falls
        back to ``logging.INFO`` if the env var is unset or unrecognized.
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, level_name, logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """Get (or lazily configure) a logger for the given module name.

    Safe to call repeatedly for the same ``name``: the handler is only
    attached once, so no duplicate log lines are produced across repeated
    imports or calls.

    Args:
        name: Logger name, typically ``__name__`` of the calling module.

    Returns:
        A configured ``logging.Logger`` instance.

    Raises:
        ValueError: If ``name`` is empty.
    """
    if not name:
        raise ValueError("Logger name must be a non-empty string.")

    logger = logging.getLogger(name)
    logger.setLevel(_resolve_log_level())

    already_configured = any(getattr(h, _HANDLER_MARKER, False) for h in logger.handlers)
    if not already_configured:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))
        setattr(handler, _HANDLER_MARKER, True)
        logger.addHandler(handler)
        logger.propagate = False

    return logger
