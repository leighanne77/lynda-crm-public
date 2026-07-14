"""FastAPI application entry point."""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.logging_config import configure_logging
from app.middleware import RequestIDMiddleware
from app.routers import (
    admin,
    admin_cost,
    auth,
    chat,
    contacts,
    exports,
    health,
    next_steps,
    reviews,
    users,
    voice,
)


class SPAStaticFiles(StaticFiles):
    """StaticFiles that falls back to index.html on 404 so React Router
    owns client-side routes like /login and /auth/success. Real files
    (assets, favicon) still serve directly. Unknown /api/* paths are
    left to 404 as JSON — they represent a backend bug, not a frontend
    route."""

    async def get_response(self, path, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not path.startswith("api/"):
                return await super().get_response("index.html", scope)
            raise


def create_app() -> FastAPI:
    """Build and configure the FastAPI app."""
    settings = get_settings()

    app = FastAPI(
        title="DESS CRM",
        description="Voice-enabled team CRM for DIN",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIDMiddleware)

    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(contacts.router, prefix="/api")
    app.include_router(users.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(exports.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")
    app.include_router(admin_cost.router, prefix="/api")
    app.include_router(reviews.router, prefix="/api")
    app.include_router(next_steps.router, prefix="/api")
    app.include_router(voice.router, prefix="/api")

    static_dir = Path("/app/frontend-dist")
    if static_dir.is_dir():
        app.mount("/", SPAStaticFiles(directory=static_dir, html=True), name="static")

    return app


app = create_app()

if os.environ.get("LOG_JSON", "").lower() in ("1", "true", "yes"):
    from app.config import log_config_source_audit

    configure_logging()
    log_config_source_audit()

# Loud-warn at boot if the TTS voice is misconfigured (deleted voice /
# lapsed subscription / bad key) so it shows in logs instead of as a
# silent 502 at speak time. Runs on a daemon thread — never blocks boot.
from app.services.voice.preflight import (  # noqa: E402
    run_voice_config_check_in_background,
)

run_voice_config_check_in_background()
