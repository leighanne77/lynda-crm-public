"""Async wrapper around Anthropic's API for the chat endpoint.

Adds three things on top of the SDK:
- prompt caching on the stable prefix (system prompt + tool schemas)
- token usage callback so callers can debit per-user daily budgets
- ENTERPRISE_MODE guard: if false, refuse to send when the prompt
  contains any email outside the dummy domains (.fake or example.com)

Zero-retention is configured at the Anthropic ORG level (console.
anthropic.com → Privacy controls), not per-request, so there's no
beta header for it here.

The SDK already retries 429s and 5xx with exponential backoff
(default max_retries=2). We don't reimplement that.
"""

import json
import logging
import re
from collections.abc import Callable, Sequence
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import Message

from app.config import get_settings

logger = logging.getLogger(__name__)

_API_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_-]+")


class _ApiKeyScrubFilter(logging.Filter):
    """Replace any Anthropic API key in a log record with [REDACTED]."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _API_KEY_PATTERN.sub("[REDACTED]", record.msg)
        if record.args:
            record.args = tuple(
                _API_KEY_PATTERN.sub("[REDACTED]", str(a)) if isinstance(a, str) else a
                for a in record.args
            )
        return True


# Install the scrubber on every logger that the Anthropic SDK or its
# transport (httpx) might use. Defense in depth — a stack trace from
# inside the SDK can include the Authorization header.
for name in ("anthropic", "httpx", "httpcore"):
    logging.getLogger(name).addFilter(_ApiKeyScrubFilter())


def _client() -> AsyncAnthropic:
    """Return a fresh AsyncAnthropic client. Tests monkeypatch this."""
    return AsyncAnthropic(api_key=get_settings().anthropic_api_key)


# Allowed in dummy mode: any *.fake TLD plus the DIN team domain.
# Everything else is treated as "real" and blocked.
_DUMMY_EMAIL_RE = re.compile(r"@[\w.-]+\.fake\b|@example\.com\b", re.IGNORECASE)
_ANY_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")


class EnterpriseModeViolation(RuntimeError):
    """Raised when ENTERPRISE_MODE=false but a prompt contains real-looking data."""


def _assert_dummy_data_only(blob: str) -> None:
    """Refuse to send if any non-dummy email appears while enterprise_mode is off."""
    if get_settings().enterprise_mode:
        return
    for match in _ANY_EMAIL_RE.finditer(blob):
        email = match.group(0)
        if _DUMMY_EMAIL_RE.search(email):
            continue
        raise EnterpriseModeViolation(
            f"ENTERPRISE_MODE=false but prompt contains an apparently-real "
            f"email ({email}). Refusing to send to Claude."
        )


def _system_with_cache(system: str) -> list[dict[str, Any]]:
    """Wrap the system prompt as a single text block with cache_control."""
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _tools_with_cache(
    tools: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mark the last tool with cache_control so tools+system cache together.

    Render order is tools → system → messages. A breakpoint on the last
    tool block caches the entire tools section; the system block also
    carries cache_control so the next request reads through both.
    """
    if not tools:
        return []
    out = [dict(t) for t in tools]
    out[-1] = {**out[-1], "cache_control": {"type": "ephemeral"}}
    return out


async def call_claude(
    *,
    messages: Sequence[Any],
    system: str,
    tools: Sequence[dict[str, Any]] | None = None,
    on_tokens: Callable[[int, int], None] | None = None,
    max_tokens: int = 16000,
) -> Message:
    """Call Anthropic. Returns the raw Message; caller handles tool dispatch."""
    settings = get_settings()
    client = _client()

    request_kwargs: dict[str, Any] = {
        "model": settings.chat_model,
        "max_tokens": max_tokens,
        "system": _system_with_cache(system),
        "messages": list(messages),
        "thinking": {"type": "adaptive"},
    }
    if tools:
        request_kwargs["tools"] = _tools_with_cache(tools)

    _assert_dummy_data_only(
        json.dumps(
            {
                "system": system,
                "messages": request_kwargs["messages"],
                "tools": request_kwargs.get("tools", []),
            },
            default=str,
        )
    )

    response = await client.messages.create(**request_kwargs)

    if on_tokens is not None:
        usage = response.usage
        on_tokens(usage.input_tokens or 0, usage.output_tokens or 0)

    return response
