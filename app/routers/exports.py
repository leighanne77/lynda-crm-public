"""Export endpoints — Sheets (today) and CSV fallback (Slice 3).

The endpoint reuses dispatch_tool_call("search_contacts", ...) so the
privacy filter that gates Lynda also gates the export. There is no
parallel "raw" path; every export goes through the same gatekeeper as
the chat does.

Auto-naming uses a deterministic format today (Slice 4 will replace it
with a Claude-generated name).
"""

from __future__ import annotations

import csv
import re
from datetime import date
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.services import sheet_naming
from app.services import sheets as sheets_service
from app.services.audit import audit_log
from app.services.tool_dispatch import dispatch_tool_call
from app.services.tools import ContactType, PrimaryFund

router = APIRouter(
    prefix="/export",
    tags=["export"],
    dependencies=[Depends(get_current_user)],
)


class ExportRequest(BaseModel):
    """Filter criteria for an export. Mirrors a subset of search_contacts."""

    query: str | None = Field(None, max_length=200)
    primary_fund: PrimaryFund | None = None
    contact_type: ContactType | None = None


class ExportResponse(BaseModel):
    sheet_url: str
    sheet_id: str
    contact_count: int


# Sheet column layout. Order matters — these are the headers and the
# field names we pull from each contact result. Notes truncated to 200
# chars to keep the sheet readable.
_SHEET_COLUMNS: list[tuple[str, str]] = [
    ("Name", "name"),
    ("Title", "title"),
    ("Company", "company_name"),
    ("Cell Phone", "cell_phone"),
    ("Office Phone", "office_phone"),
    ("Email", "email"),
    ("Fund", "primary_fund"),
    ("Type", "contact_type"),
    ("Country", "country"),
    ("Sectors", "sectors"),
    ("Fly Status", "fly_status"),
]


def _format_value(field: str, value: object) -> str:
    """Coerce a contact field to a string for the sheet cell."""
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    s = str(value)
    return s[:200] if field == "notes" else s


def _build_rows(contacts: list[dict]) -> list[list[str]]:
    return [
        [_format_value(field, c.get(field)) for _, field in _SHEET_COLUMNS]
        for c in contacts
    ]


def _build_csv(contacts: list[dict]) -> str:
    """Render headers + rows as a single CSV string (RFC 4180 quoting)."""
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow([label for label, _ in _SHEET_COLUMNS])
    writer.writerows(_build_rows(contacts))
    return out.getvalue()


def _safe_filename(title: str) -> str:
    """Strip anything outside [A-Za-z0-9-_] from a title for use as a
    Content-Disposition filename. Non-ASCII chars get dropped entirely
    so we don't need RFC-5987 encoding."""
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", title).strip("_")
    return safe or "din_contacts"


def _csv_response(contacts: list[dict], title: str) -> Response:
    """Build a CSV download response with the right headers."""
    body = _build_csv(contacts)
    filename = f"{_safe_filename(title)}.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _filtered_contacts(
    body: ExportRequest, current_user: User, db: Session
) -> list[dict]:
    """Run the same privacy-filtered search the chat uses. 404 if empty."""
    search_params: dict = {}
    if body.query:
        search_params["query"] = body.query
    if body.primary_fund:
        search_params["primary_fund"] = body.primary_fund
    if body.contact_type:
        search_params["contact_type"] = body.contact_type
    result = dispatch_tool_call("search_contacts", search_params, current_user, db)
    contacts = result.get("results", [])
    if not contacts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No contacts matched the export filter.",
        )
    return contacts


def _filter_summary(req: ExportRequest) -> str:
    """Human-readable filter description used as the seed for Claude."""
    parts: list[str] = []
    if req.primary_fund:
        parts.append(req.primary_fund)
    if req.contact_type:
        parts.append(req.contact_type)
    if req.query:
        parts.append(f'"{req.query}"')
    return " ".join(parts) if parts else "All"


def _fallback_name(req: ExportRequest, count: int) -> str:
    """Deterministic name. Used when Claude is disabled or unreachable."""
    return (
        f"DIN Contacts — {_filter_summary(req)} — "
        f"{date.today().isoformat()} ({count})"
    )


async def _auto_name(req: ExportRequest, count: int) -> str:
    """Ask Claude for a descriptive sheet name; falls back to the
    deterministic format if Claude is slow / errors / returns garbage."""
    return await sheet_naming.suggest_name(
        filter_summary=_filter_summary(req),
        count=count,
    )


@router.post("/sheets")
@audit_log(action="export_sheet", target_type="export", payload_kwarg="body")
async def export_sheets(
    body: ExportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Export the filtered contact list to a new Google Sheet.

    Returns JSON `{sheet_url, sheet_id, contact_count}` on success.
    On a Sheets scope error (user hasn't granted drive.file or admin
    hasn't approved), AUTOMATICALLY falls back to a CSV download so
    the user gets something usable instead of a hard failure. Frontend
    distinguishes by Content-Type header:
      - application/json  -> parse, redirect to sheet_url
      - text/csv          -> save the body as a file
    """
    settings = get_settings()

    if not current_user.google_access_token:
        # Older sessions (pre-Day-5) don't have the Google access token
        # captured. They need to re-login to grant the drive.file scope.
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=(
                "Google access token missing — please log out and log back "
                "in to grant Drive access."
            ),
        )

    contacts = _filtered_contacts(body, current_user, db)
    title = await _auto_name(body, len(contacts))
    headers = [label for label, _ in _SHEET_COLUMNS]
    rows = _build_rows(contacts)

    try:
        created = sheets_service.create_sheet(
            title=title,
            headers=headers,
            rows=rows,
            access_token=current_user.google_access_token,
            refresh_token=current_user.google_refresh_token,
            drive_folder_id=settings.team_drive_folder_id or None,
            share_domain=settings.team_drive_share_domain or None,
        )
    except sheets_service.SheetsScopeError:
        # Auto-fallback to CSV — user gets a usable file even when the
        # scope/admin-approval path is blocked.
        return _csv_response(contacts, title)
    except sheets_service.SheetsQuotaError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Google Sheets quota exceeded. Try again in a minute.",
        ) from e
    except sheets_service.SheetsExportError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Sheets export failed: {e}",
        ) from e

    payload = ExportResponse(
        sheet_url=created.url,
        sheet_id=created.id,
        contact_count=len(contacts),
    )
    return Response(
        content=payload.model_dump_json(),
        media_type="application/json",
    )


@router.post("/csv")
@audit_log(action="export_csv", target_type="export", payload_kwarg="body")
async def export_csv(
    body: ExportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Direct CSV export — same input shape as /export/sheets. Always
    returns a CSV download; never touches Google. Useful when the user
    explicitly wants a file rather than a sheet, or when they've
    revoked the drive.file scope deliberately."""
    contacts = _filtered_contacts(body, current_user, db)
    title = await _auto_name(body, len(contacts))
    return _csv_response(contacts, title)
