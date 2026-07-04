"""Slice 7 — lock in the ENTERPRISE_MODE=false invariant.

When enterprise_mode is off, no real-looking email may appear in any
prompt sent to Claude. This is the "even if everything else is wrong"
last-ditch defense against accidental data leak during Phase 1.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import get_settings
from app.routers.chat import _system_prompt
from app.services import llm


def _fake_response() -> Any:
    return SimpleNamespace(
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        content=[SimpleNamespace(type="text", text="ok")],
        stop_reason="end_turn",
    )


@pytest.fixture
def mock_create(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    create = AsyncMock(return_value=_fake_response())
    fake_client = MagicMock()
    fake_client.messages.create = create
    monkeypatch.setattr(llm, "_client", lambda: fake_client)
    return create


def test_system_prompt_contains_no_email_addresses() -> None:
    """The static system prompt template must never carry contact data."""
    text = _system_prompt("text")
    voice = _system_prompt("voice")
    for prompt in (text, voice):
        assert "@" not in prompt or "<USER_DATA>" in prompt
        assert not any(
            tld in prompt.lower()
            for tld in (".com>", ".org>", ".net>", "@gmail", "@yahoo")
        )


async def test_guard_allows_dummy_email_in_dummy_mode(
    mock_create: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "enterprise_mode", False)
    await llm.call_claude(
        messages=[{"role": "user", "content": "ping wbarrett@mareislandnaval.fake"}],
        system="sys",
    )
    assert mock_create.called


async def test_guard_allows_din_team_email_in_dummy_mode(
    mock_create: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "enterprise_mode", False)
    await llm.call_claude(
        messages=[{"role": "user", "content": "forward to pat@example.com"}],
        system="sys",
    )
    assert mock_create.called


async def test_guard_blocks_real_email_in_dummy_mode(
    mock_create: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "enterprise_mode", False)
    with pytest.raises(llm.EnterpriseModeViolation, match="apparently-real"):
        await llm.call_claude(
            messages=[{"role": "user", "content": "email john@gmail.com about it"}],
            system="sys",
        )
    assert not mock_create.called


async def test_guard_blocks_real_email_in_tool_result(
    mock_create: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Most likely leak path: a contact note containing a real email."""
    monkeypatch.setattr(get_settings(), "enterprise_mode", False)
    with pytest.raises(llm.EnterpriseModeViolation):
        await llm.call_claude(
            messages=[
                {"role": "user", "content": "hi"},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_1",
                            "content": '{"email":"victim@megacorp.com"}',
                        }
                    ],
                },
            ],
            system="sys",
        )
    assert not mock_create.called


async def test_guard_off_in_enterprise_mode(
    mock_create: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When enterprise_mode=True, real emails are allowed (production path)."""
    monkeypatch.setattr(get_settings(), "enterprise_mode", True)
    await llm.call_claude(
        messages=[{"role": "user", "content": "email john@gmail.com about it"}],
        system="sys",
    )
    assert mock_create.called
