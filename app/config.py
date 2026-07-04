"""Application settings loaded from environment variables.

All configuration lives here. Nothing else in the app should read os.environ.
"""

import logging
import os
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Immutable settings instance for the app."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Hard gate: false = dummy data only, true = real data allowed
    enterprise_mode: bool = False

    # Database
    database_url: str = "postgresql+psycopg://lynda:lynda_dev_only@localhost:5432/lynda"
    test_database_url: str = (
        "postgresql+psycopg://lynda:lynda_dev_only@localhost:5432/lynda_test"
    )

    # Anthropic
    anthropic_api_key: str = ""

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/auth/callback"

    # JWT
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expiration_days: int = 7

    # Access control
    allowed_emails: str = ""
    allowed_origins: str = "http://localhost:5173"

    # Chat safety rails
    chat_input_max_chars: int = 4000
    chat_rate_limit_per_hour: int = 60
    # Day 6 bump: 100k/25k was too conservative — Day 5 smoke showed
    # heavy real-world build use (100+ chat calls with growing history
    # per day) exhausted the 100k input cap before all planned work
    # landed. 500k/125k keeps the same 4:1 ratio and gives ~5x
    # headroom. Per-user override column remains for truly heavy
    # users (e.g. Alex Rivera's current override is 2M).
    chat_input_token_budget_per_day: int = 500_000
    chat_output_token_budget_per_day: int = 125_000
    chat_tool_iteration_cap: int = 5
    chat_history_max_turns: int = 20
    chat_model: str = "claude-sonnet-4-6"

    # Browser session cookie (Day 4 cookie-based auth flow)
    session_cookie_name: str = "lynda_session"
    frontend_url: str = "http://localhost:5173"
    frontend_auth_success_path: str = "/auth/success"

    # Google Sheets export (Day 5)
    # Drive folder ID where exported sheets land. Empty = sheets are
    # created in the user's root Drive (Phase 1 dev default).
    team_drive_folder_id: str = ""
    # Domain that gets read access to every exported sheet.
    team_drive_share_domain: str = "example.com"

    # Daily cost alert (Day 6 Slice 7). Threshold at which the
    # cost-summary endpoint flips over_threshold=true and the daily
    # job dispatches an alert email to every admin user.
    daily_cost_alert_threshold_usd: float = 10.0

    # SMTP for daily cost-alert emails (Slice 7.1). Defaults target
    # Gmail SMTP with an app password — quota is 500/day per account,
    # vastly more than once-daily alert traffic. The from-name is
    # what shows in the inbox; the from-address typically matches the
    # SMTP username unless an alias is used. Leave smtp_password
    # empty in dev — the daily job no-ops without throwing.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_address: str = ""
    smtp_from_name: str = "DIN Alerts"
    # Optional comma-separated override. Empty = resolve recipients
    # from User.role='admin' at job run time. Override exists for
    # smoke-testing into a single inbox without granting admin role.
    cost_alert_recipients_override: str = ""

    # Fernet key for encrypting Google OAuth tokens at rest. Generate
    # with `python -c "from cryptography.fernet import Fernet;
    # print(Fernet.generate_key().decode())"`. In enterprise_mode the
    # app refuses to start without this set — see validator below.
    token_encryption_key: str = ""

    # Phase 3 Slice 1 — voice / speech-to-text. Default provider is
    # google_chirp (Chirp 2 via Cloud Speech-to-Text v2). The
    # provider-agnostic interface allows future swaps (Whisper, local)
    # by changing stt_provider + adding the implementation module.
    voice_enabled: bool = False
    stt_provider: str = "google_chirp"
    stt_model: str = "chirp_2"
    # Chirp 2 list rate (2026-05). Per-provider so a Whisper swap
    # updates one constant. Cost per call = duration_sec / 60 * rate.
    stt_cost_per_minute_usd: float = 0.024
    # Sync recognition hard cap on Cloud Speech-to-Text v2 is 60 seconds.
    # Audio over this is rejected with a 400; batch path is a Phase 3.5
    # follow-up.
    stt_max_duration_sec: int = 60
    # Global default for per-user voice budget (minutes/day). Per-user
    # override lives on User.daily_voice_minutes_budget_override.
    default_daily_voice_minutes_budget: int = 60
    # GCP region for the v2 Speech endpoint. Chirp 2 is supported in
    # us-central1, eu, asia-southeast1, and a few others; us-central1
    # is the safest default. The Cloud Run service is in us-west1 but
    # Speech-to-Text is a regional endpoint with its own residency.
    stt_region: str = "us-central1"

    # Phase 3 Slice 4 — text-to-speech via ElevenLabs (cloned voice).
    # Provider-agnostic so a future swap (Google TTS, OpenAI, Resemble)
    # is a one-module change. Empty api key disables the endpoint at
    # request time — the same way smtp_password gates the cost-alert
    # job. voice_id is the ElevenLabs custom-voice identifier; it must
    # be set in prod before /api/voice/speak returns usable audio.
    tts_provider: str = "elevenlabs"
    tts_model: str = "eleven_turbo_v2_5"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    # Playback speed sent in voice_settings. 1.0 is the voice's dashboard
    # default; we clamp to 0.7–1.0 because anything faster than 1.0
    # tends to push the cloned voice into a chipmunk register, and
    # below 0.7 ElevenLabs starts dropping prosody.
    elevenlabs_speed: float = Field(default=0.8, ge=0.7, le=1.0)
    # Voice timbre knobs. Pitch isn't a parameter — these adjust how
    # the cloned voice is rendered:
    #   stability        — lower = more emotional/varied delivery
    #   similarity_boost — higher = closer to the source recording
    #   style            — exaggerates the speaker's stylistic delivery
    # Defaults aim for richer, less-thin output on Goddess's voice clone
    # (suspected contralto target). All three are 0.0–1.0.
    elevenlabs_stability: float = Field(default=0.35, ge=0.0, le=1.0)
    elevenlabs_similarity_boost: float = Field(default=0.85, ge=0.0, le=1.0)
    elevenlabs_style: float = Field(default=0.35, ge=0.0, le=1.0)
    # ElevenLabs list rate (2026-05) — $0.18/1k chars over plan quota.
    # Per-provider constant so a swap updates one number.
    tts_cost_per_1k_chars_usd: float = 0.18
    # Global default for per-user daily TTS character budget. Per-user
    # override candidate column not yet on User — add in a future slice
    # if anyone needs higher caps.
    default_daily_tts_chars_budget: int = 50_000
    # Hard ceiling on a single TTS call. ElevenLabs accepts up to 5k
    # chars per request; we cap shorter to keep audio responses snappy
    # and to avoid runaway costs from a long Claude reply.
    tts_max_chars_per_call: int = 2_500

    @property
    def allowed_email_set(self) -> frozenset[str]:
        """Parse comma-separated ALLOWED_EMAILS into a lowercase frozenset."""
        return frozenset(
            email.strip().lower()
            for email in self.allowed_emails.split(",")
            if email.strip()
        )

    @property
    def allowed_origin_list(self) -> list[str]:
        """Parse comma-separated ALLOWED_ORIGINS into a list."""
        return [
            origin.strip()
            for origin in self.allowed_origins.split(",")
            if origin.strip()
        ]

    @model_validator(mode="after")
    def _reject_default_jwt_secret_in_enterprise_mode(self) -> "Settings":
        if self.enterprise_mode and self.jwt_secret == "change-me":
            raise ValueError(
                "jwt_secret must be changed from the default 'change-me' "
                "when enterprise_mode is enabled."
            )
        return self

    @model_validator(mode="after")
    def _require_token_encryption_key_in_enterprise_mode(self) -> "Settings":
        if self.enterprise_mode and not self.token_encryption_key:
            raise ValueError(
                "token_encryption_key must be set when enterprise_mode is "
                "enabled. Generate with Fernet.generate_key()."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()


CRITICAL_SETTINGS: tuple[str, ...] = (
    "DATABASE_URL",
    "ANTHROPIC_API_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REDIRECT_URI",
    "JWT_SECRET",
    "ALLOWED_EMAILS",
    "TOKEN_ENCRYPTION_KEY",
)


def audit_config_sources(
    names: Sequence[str] = CRITICAL_SETTINGS,
    *,
    env: Mapping[str, str] | None = None,
    dotenv_path: Path | None = None,
) -> list[dict[str, str]]:
    """Return, for each named setting, where the value actually came from.

    Sources:
      - ``secret_manager`` — env var set AND running on Cloud Run (``K_SERVICE``)
      - ``env_var``        — env var set AND not on Cloud Run (shell export)
      - ``dotenv``         — not in env, but present in the dotenv file
      - ``default``        — neither (hardcoded default wins)

    The Day 3 shell-env-var-shadows-dotenv bug is catchable because env_var
    and dotenv are reported distinctly. Prefix/suffix are 4 chars each, never
    the full secret.
    """
    env_map = dict(env) if env is not None else dict(os.environ)
    dotenv_file = dotenv_path if dotenv_path is not None else Path(".env")
    on_cloud_run = "K_SERVICE" in env_map

    dotenv_values: dict[str, str] = {}
    if dotenv_file.is_file():
        for raw in dotenv_file.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            dotenv_values[key.strip().upper()] = value.strip().strip('"').strip("'")

    records: list[dict[str, str]] = []
    for name in names:
        if name in env_map:
            value = env_map[name]
            source = "secret_manager" if on_cloud_run else "env_var"
        elif name in dotenv_values:
            value = dotenv_values[name]
            source = "dotenv"
        else:
            value = ""
            source = "default"
        prefix = value[:4] if len(value) >= 4 else value
        suffix = value[-4:] if len(value) >= 4 else ""
        records.append(
            {
                "setting": name,
                "source": source,
                "prefix": prefix,
                "suffix": suffix,
            }
        )
    return records


def log_config_source_audit() -> None:
    """Emit one INFO log per critical setting with its resolved source."""
    logger = logging.getLogger("app.config")
    for record in audit_config_sources():
        logger.info("config_source_audit", extra=record)
