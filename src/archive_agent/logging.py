"""Structured logging with automatic secret redaction.

Every module gets its logger via ``get_logger(__name__)``; no module
calls ``logging.getLogger`` directly and library code never prints to
stdout (see the operating rules in ``claude-code-pack/CLAUDE.md``).

The redaction processor walks each event dict (recursively through
nested dicts) and replaces values of keys in ``REDACT_FIELDS`` with
``"***"``. Matching is case-insensitive and aware of underscore
boundaries: ``api_key`` matches ``JELLYFIN_API_KEY`` and
``tmdb_api_key``, ``token`` matches ``auth_token`` but **not**
``input_tokens`` (plural — a counter, not a secret).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any, Literal, cast

import structlog

__all__ = [
    "REDACT_FIELDS",
    "configure_logging",
    "get_logger",
    "redact_processor",
]

REDACT_FIELDS: frozenset[str] = frozenset(
    {"api_key", "token", "password", "secret", "authorization"}
)
_REDACTED = "***"


def _is_sensitive(key: str) -> bool:
    """True if ``key`` equals a redact field exactly, or has one attached
    via an underscore boundary. Plural variants (``tokens``) don't match
    — counters stay visible."""
    k = key.lower()
    for field in REDACT_FIELDS:
        if k == field or k.endswith("_" + field) or k.startswith(field + "_"):
            return True
    return False


def redact_processor(
    logger: Any,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """structlog processor: replace values of sensitive keys with ``***``.

    Recurses into nested dicts. Lists of dicts are walked; scalar list
    members are left alone (they rarely carry named secrets).
    """

    def _walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: (_REDACTED if _is_sensitive(k) else _walk(v)) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk(v) for v in obj]
        return obj

    return cast(MutableMapping[str, Any], _walk(dict(event_dict)))


def configure_logging(
    level: str = "INFO",
    fmt: Literal["json", "console"] = "json",
) -> None:
    """Install the process-wide logging configuration.

    Called once at CLI entry (and in tests that need the real renderer).
    Subsequent calls are idempotent — structlog.configure resets
    processors cleanly.
    """
    stdlib_level = getattr(logging, level.upper(), logging.INFO)
    # ``force=True`` resets any handlers installed earlier in the process,
    # which matters both in tests (pytest installs its own capture handler)
    # and when the CLI callback reconfigures between subcommands.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=stdlib_level,
        force=True,
    )

    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_processor,
    ]
    if fmt == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=False))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(stdlib_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structured logger bound to ``name`` (typically
    ``__name__``)."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
