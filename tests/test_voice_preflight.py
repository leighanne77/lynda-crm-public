"""Tests for the TTS voice startup preflight check.

Proves a stale/removed voice or bad key produces a clear warning string,
and that a healthy or disabled config stays quiet — so the silent-502
failure mode (Goddess goes mute) becomes a loud log line instead.
"""

from app.config import get_settings
from app.services.voice.preflight import voice_config_warning


class _FakeResp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeClient:
    """Minimal stand-in for httpx.Client.get used by the check."""

    def __init__(self, status_code: int) -> None:
        self._status_code = status_code

    def get(self, url: str, headers: dict | None = None) -> _FakeResp:
        return _FakeResp(self._status_code)


def _voice_settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "voice_enabled", True)
    monkeypatch.setattr(s, "tts_provider", "elevenlabs")
    monkeypatch.setattr(s, "elevenlabs_api_key", "test-key")
    monkeypatch.setattr(s, "elevenlabs_voice_id", "voice-123")
    return s


def test_no_warning_when_voice_disabled(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "voice_enabled", False)
    # Should not even attempt a lookup — pass a client that would 404.
    result = voice_config_warning(s, client=_FakeClient(404))  # type: ignore[arg-type]
    assert result is None


def test_warns_when_voice_not_found(monkeypatch) -> None:
    s = _voice_settings(monkeypatch)
    msg = voice_config_warning(s, client=_FakeClient(404))  # type: ignore[arg-type]
    assert msg is not None
    assert "NOT FOUND" in msg
    assert "voice-123" in msg


def test_warns_when_key_rejected(monkeypatch) -> None:
    s = _voice_settings(monkeypatch)
    msg = voice_config_warning(s, client=_FakeClient(401))  # type: ignore[arg-type]
    assert msg is not None
    assert "rejected the API key" in msg


def test_quiet_when_voice_resolves(monkeypatch) -> None:
    s = _voice_settings(monkeypatch)
    result = voice_config_warning(s, client=_FakeClient(200))  # type: ignore[arg-type]
    assert result is None


def test_warns_when_voice_id_unset(monkeypatch) -> None:
    s = _voice_settings(monkeypatch)
    monkeypatch.setattr(s, "elevenlabs_voice_id", "")
    msg = voice_config_warning(s, client=_FakeClient(200))  # type: ignore[arg-type]
    assert msg is not None
    assert "ELEVENLABS_VOICE_ID is unset" in msg


def test_warns_when_key_unset(monkeypatch) -> None:
    s = _voice_settings(monkeypatch)
    monkeypatch.setattr(s, "elevenlabs_api_key", "")
    msg = voice_config_warning(s, client=_FakeClient(200))  # type: ignore[arg-type]
    assert msg is not None
    assert "ELEVENLABS_API_KEY is unset" in msg
