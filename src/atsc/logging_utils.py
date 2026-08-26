"""Logging helpers and friendly error reporting.

Goals:
  * one consistent, readable log format across CLI, training and the dashboard;
  * *actionable* error messages (the operator will not debug) via :func:`friendly_error`.
"""
from __future__ import annotations

import logging
import sys
from typing import Optional

_CONFIGURED = False

# ANSI colours (auto-disabled when not a TTY, e.g. piped logs)
_COLORS = {
    "DEBUG": "\033[37m",
    "INFO": "\033[36m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[41m",
}
_RESET = "\033[0m"


class _Formatter(logging.Formatter):
    def __init__(self, use_color: bool) -> None:
        super().__init__("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                         datefmt="%H:%M:%S")
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if self.use_color:
            color = _COLORS.get(record.levelname, "")
            return f"{color}{msg}{_RESET}"
        return msg


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once, with colour when attached to a terminal."""
    global _CONFIGURED
    if _CONFIGURED:
        logging.getLogger().setLevel(level.upper())
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Formatter(use_color=sys.stdout.isatty()))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger, ensuring logging is configured."""
    if not _CONFIGURED:
        setup_logging()
    return logging.getLogger(name)


def friendly_error(title: str, detail: str, fix: Optional[str] = None) -> str:
    """Format a boxed, actionable error message for the operator."""
    lines = [
        "",
        "=" * 72,
        f"  {title}",
        "-" * 72,
    ]
    for para in detail.strip().splitlines():
        lines.append(f"  {para}")
    if fix:
        lines.append("-" * 72)
        lines.append("  HOW TO FIX:")
        for para in fix.strip().splitlines():
            lines.append(f"    {para}")
    lines.append("=" * 72)
    lines.append("")
    return "\n".join(lines)


class BannerError(RuntimeError):
    """An error whose message is already a nicely formatted banner."""
