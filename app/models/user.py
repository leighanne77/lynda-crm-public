"""User model."""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.types import EncryptedString


class User(Base):
    """DIN team member with access to DESS."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    google_user_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    role: Mapped[str] = mapped_column(String(50), default="member")
    intro_seen: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_existence_hints: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Per-user daily token budget (Day 3 chat safety rails)
    daily_input_tokens_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    daily_output_tokens_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    token_budget_reset_at: Mapped[date | None] = mapped_column(Date)

    # Per-user overrides (null = use global setting). Lets heavy users like
    # Alex Rivera have higher caps without raising the global default for everyone.
    daily_input_token_budget_override: Mapped[int | None] = mapped_column(Integer)
    rate_limit_per_hour_override: Mapped[int | None] = mapped_column(Integer)
    daily_voice_minutes_budget_override: Mapped[int | None] = mapped_column(Integer)

    # Google access token — captured from OAuth callback so the backend
    # can call Sheets/Drive on the user's behalf. Encrypted at rest via
    # EncryptedString (Fernet). Column widened to 1024 to hold the
    # ciphertext overhead.
    google_access_token: Mapped[str | None] = mapped_column(EncryptedString(1024))
    # Google refresh token — captured at first consent (Day 6). Lets
    # google-auth swap an expired or revoked access_token for a fresh
    # one without bouncing the user back through OAuth. Encrypted at
    # rest via EncryptedString.
    google_refresh_token: Mapped[str | None] = mapped_column(EncryptedString(1024))
