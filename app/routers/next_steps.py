"""Next-steps REST endpoints (Phase 2 Slice 6.10).

The chat tools (`create_next_step`, `complete_next_step`) are the
primary write surface — DESS uses those for voice-driven adds. These
REST endpoints exist so the expanded-card UI can:
  - list a contact's pending + recently-completed next-steps
  - mark one done with a single click without round-tripping through
    a chat turn
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Contact, NextStep, User
from app.services.privacy import visible_contacts_query
from app.services.tool_dispatch import CompleteNextStepInput, _handle_complete_next_step

router = APIRouter(tags=["next-steps"], dependencies=[Depends(get_current_user)])


class NextStepRead(BaseModel):
    id: int
    contact_id: int
    title: str
    owner_id: int
    owner_name: str | None
    owner_initials: str | None
    created_by_id: int
    google_task_list_id: str | None
    google_task_url: str | None
    done: bool
    done_at: datetime | None
    created_at: datetime


def _serialize(step: NextStep, owners_by_id: dict[int, User | None]) -> NextStepRead:
    from app.services.google_tasks import list_url
    from app.services.tool_dispatch import _initials_for

    owner = owners_by_id.get(step.owner_id)
    return NextStepRead(
        id=step.id,
        contact_id=step.contact_id,
        title=step.title,
        owner_id=step.owner_id,
        owner_name=owner.name if owner else None,
        owner_initials=_initials_for(owner.name if owner else None),
        created_by_id=step.created_by_id,
        google_task_list_id=step.google_task_list_id,
        google_task_url=(
            list_url(step.google_task_list_id) if step.google_task_list_id else None
        ),
        done=step.done,
        done_at=step.done_at,
        created_at=step.created_at,
    )


@router.get("/contacts/{contact_id}/next-steps", response_model=list[NextStepRead])
def list_next_steps(
    contact_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[NextStepRead]:
    """Return next-steps for the contact, open first then recently-done.

    Authorization: visible contacts only (full view). Redacted-view
    callers get 404 — next-steps could leak intent ("Jordan Blake is
    about to call Marcus") and there's nothing the redacted caller
    could usefully do with them anyway."""
    contact = db.scalars(
        visible_contacts_query(current_user).where(Contact.id == contact_id)
    ).first()
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    rows = list(
        db.scalars(
            select(NextStep)
            .where(NextStep.contact_id == contact_id)
            .order_by(NextStep.done.asc(), NextStep.created_at.desc())
            .limit(100)
        )
    )
    owner_ids = {r.owner_id for r in rows}
    owners_by_id: dict[int, User | None] = {}
    if owner_ids:
        users = list(db.scalars(select(User).where(User.id.in_(owner_ids))))
        owners_by_id = {u.id: u for u in users}
    return [_serialize(r, owners_by_id) for r in rows]


class CompleteNextStepRequest(BaseModel):
    done: bool


@router.patch("/next-steps/{next_step_id}", response_model=NextStepRead)
def update_next_step(
    next_step_id: int,
    payload: CompleteNextStepRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NextStepRead:
    """Toggle a next-step's done state. Currently only supports
    done=true (re-opening a completed step is out of scope; create a
    new step instead). Delegates the heavy lifting (auth, Google Tasks
    mirror, audit row) to the chat tool's handler so both surfaces
    behave identically."""
    if not payload.done:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Re-opening a completed next-step is not supported.",
        )
    result = _handle_complete_next_step(
        CompleteNextStepInput(next_step_id=next_step_id),
        current_user,
        db,
    )
    if "error" in result:
        if result["error"] in ("not_found_or_done", "not_found"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if result["error"] == "forbidden":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    step = db.get(NextStep, next_step_id)
    if step is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    owner = db.get(User, step.owner_id)
    return _serialize(step, {step.owner_id: owner})
