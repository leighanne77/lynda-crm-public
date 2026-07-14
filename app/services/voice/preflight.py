"""Startup preflight check for the text-to-speech voice.

A misconfigured TTS voice — a deleted ElevenLabs voice, a lapsed
subscription that locked a custom voice, or a wrong/expired key —
otherwise surfaces only as a silent HTTP 502 at `/api/voice/speak` time,
which looks to the user like "DESS just stopped talking." This logs a
loud WARNING at boot so the cause is obvious in Cloud Logging instead.

Best-effort and non-fatal: a network/transport failure logs nothing
(we don't cry wolf when ElevenLabs itself is briefly unreachable), and
the check runs on a daemon thread so the startup network call never
delays a Cloud Run cold start.
"""

from __future__ import annotations

import logging
import threading

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_VOICE_URL = "https://api.elevenlabs.io/v1/voices/{voice_id}"
_CHECK_TIMEOUT_SEC = 5.0


def voice_config_warning(
    settings: Settings, *, client: httpx.Client | None = None
) -> str | None:
    """Return a human-readable warning if TTS is enabled but the configured
    ElevenLabs voice can't be used; return None if it's fine or TTS is off.

    A transport error returns None — we can't conclude the voice is bad
    just because the network hiccuped.
    """
    if not settings.voice_enabled or settings.tts_provider != "elevenlabs":
        return None
    if not settings.elevenlabs_api_key:
        return (
            "tts_provider=elevenlabs but ELEVENLABS_API_KEY is unset "
            "— /speak will fail."
        )
    if not settings.elevenlabs_voice_id:
        return "ELEVENLABS_VOICE_ID is unset — /speak will fail."

    owns_client = client is None
    c = client or httpx.Client(timeout=_CHECK_TIMEOUT_SEC)
    try:
        resp = c.get(
            _VOICE_URL.format(voice_id=settings.elevenlabs_voice_id),
            headers={"xi-api-key": settings.elevenlabs_api_key},
        )
    except httpx.HTTPError:
        return None
    finally:
        if owns_client:
            c.close()

    if resp.status_code == 200:
        return None
    if resp.status_code == 404:
        return (
            f"ELEVENLABS_VOICE_ID '{settings.elevenlabs_voice_id}' was NOT "
            "FOUND on the ElevenLabs account (HTTP 404). TTS /speak will "
            "return 502 and DESS will be silent. The voice was likely "
            "deleted or the subscription lapsed (custom voices are locked "
            "below a paid tier). Fix ELEVENLABS_VOICE_ID or restore it."
        )
    if resp.status_code in (401, 403):
        return (
            f"ElevenLabs rejected the API key (HTTP {resp.status_code}) "
            f"while verifying voice '{settings.elevenlabs_voice_id}'. TTS "
            "/speak will fail — check the elevenlabs-api-key secret / "
            "the ElevenLabs account."
        )
    return (
        f"Unexpected HTTP {resp.status_code} verifying ELEVENLABS_VOICE_ID "
        f"'{settings.elevenlabs_voice_id}'. TTS /speak may be failing."
    )


def log_voice_config_check(settings: Settings | None = None) -> None:
    """Run the check and log the outcome — WARNING if misconfigured, INFO
    if verified. Safe to call at startup."""
    settings = settings or get_settings()
    msg = voice_config_warning(settings)
    if msg:
        logger.warning("VOICE MISCONFIG: %s", msg)
    elif settings.voice_enabled and settings.tts_provider == "elevenlabs":
        logger.info(
            "VOICE: ElevenLabs voice '%s' verified available at startup.",
            settings.elevenlabs_voice_id,
        )


def run_voice_config_check_in_background(settings: Settings | None = None) -> None:
    """Fire log_voice_config_check on a daemon thread so the startup network
    call never blocks a Cloud Run cold start."""
    threading.Thread(
        target=log_voice_config_check, args=(settings,), daemon=True
    ).start()
