"""Tool dispatcher — security-critical path.

`dispatch_tool_call` validates Claude's tool call against the registry
schemas from app/services/tools, then runs the matching handler. Every
read goes through `visible_contacts_query`, so the Day 2 privacy filter
gates the result regardless of what Claude asked for.

Two error patterns:
  - `ToolDispatchError` for misuse (unknown tool, schema validation
    fails). The chat endpoint converts these into tool_result with
    is_error=true so Claude can adjust.
  - `{"error": "...", "message": "..."}` dict for legitimate operation
    failures (contact not found, not owned). Claude sees these as
    normal results and can explain to the user.
"""

from collections.abc import Callable
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models import Contact, NextStep, Relationship, User
from app.services.audit import hash_payload, write_audit_row
from app.services.fly_status import fly_status_search_priority, unknown_search_rank
from app.services.google_tasks import (
    GoogleTasksError,
    GoogleTasksQuotaError,
    GoogleTasksScopeError,
)
from app.services.google_tasks import complete_task as gt_complete_task
from app.services.google_tasks import create_next_step_task, create_talk_to_task
from app.services.gov_detect import looks_like_gov_email
from app.services.intro_pathfinder import find_intro_paths as find_intro_paths_service
from app.services.privacy import redactable_contacts_query, visible_contacts_query
from app.services.tools import (
    TOOL_REGISTRY,
    CompleteNextStepInput,
    CreateContactInput,
    CreateGoogleTaskInput,
    CreateNextStepInput,
    DeleteContactInput,
    FindIntroPathsInput,
    LinkContactsInput,
    PipelineSummaryInput,
    SearchContactsInput,
    TransferContactInput,
    UpdateContactInput,
)
from app.services.user_link import user_id_for_email

SEARCH_RESULT_LIMIT = 25


def _jsonable(value: Any) -> Any:
    """JSON-safe coercion for audit metadata. Most contact fields are
    already friendly (str/int/bool/None/list[str]); datetimes get
    isoformatted; anything else falls back to str()."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):  # datetime / date
        return value.isoformat()
    return str(value)


# Subset of create_contact input that's worth recording for the
# changelog. Notes/email/phones intentionally excluded — the audit
# trail shouldn't be a backdoor to PII even for the owner's history.
_CREATE_METADATA_FIELDS: tuple[str, ...] = (
    "name",
    "company_name",
    "title",
    "primary_fund",
    "contact_type",
    "lp_subtype",
    "fly_status",
    "opt_in_status",
    "country",
    "metro",
    "sectors",
    "is_private",
)

# Phase 2 Slice 6.5 — partial-reveal privacy.
# The whitelist of columns that can legally appear in reveal_fields.
# PII columns (name, email, cell_phone, office_phone, title, notes,
# image_url) are NEVER in this set — those are non-negotiably private
# on a redacted row. Validator rejects any list containing a value
# outside this set.
ALLOWED_REVEAL_FIELDS: frozenset[str] = frozenset(
    {
        "primary_fund",
        "company_name",
        "sectors",
        "contact_type",
        "country",
        "lp_subtype",
        "fly_status",
        "ex_government",
        "gender",
    }
)
DEFAULT_REVEAL_FIELDS: tuple[str, ...] = ("primary_fund", "company_name", "sectors")


class ToolDispatchError(Exception):
    """Raised when a tool call's name or params are invalid."""


def _format_contact(
    c: Contact,
    *,
    caller: User,
    owner_name: str | None,
) -> dict[str, Any]:
    """Contact view for tool results — no notes.

    Includes email + phones because the frontend offers a "make this a
    list" export with phone/email modes. The privacy filter already
    restricts which contacts the user sees, so including their PII here
    only exposes what the user already has access to.

    owner_name + is_self_owned were added in Phase 2 Slice 5 follow-up:
    Goddess needs to know the owner BEFORE offering a "Talk to <Owner>"
    reminder, and needs to detect self-owned contacts so she doesn't
    offer to remind the user to talk to themselves. The user.id itself
    is deliberately NOT exposed — owner_name is what Goddess speaks; the
    boolean is what gates the offer.
    """
    return {
        "id": c.id,
        "name": c.name,
        "company_name": c.company_name,
        "title": c.title,
        "email": c.email,
        "cell_phone": c.cell_phone,
        "office_phone": c.office_phone,
        # Slice 6.8 — surface notes on fully-visible rows so the
        # expanded card view + Goddess's chat readback can use them.
        # Redacted rows never get notes (see _redacted_view) — notes
        # is on the never-reveal PII list.
        "notes": c.notes,
        "primary_fund": c.primary_fund,
        "contact_type": c.contact_type,
        "sectors": list(c.sectors or []),
        "is_private": c.is_private,
        "gender": c.gender,
        "country": c.country,
        "metro": c.metro,
        "lp_subtype": c.lp_subtype,
        "fly_status": c.fly_status,
        "opt_in_status": c.opt_in_status,
        "image_url": c.image_url,
        "ex_government": c.ex_government,
        "is_gov_employee": c.is_gov_employee,
        "patina_overrides": c.patina_overrides,
        "owner_name": owner_name,
        "owner_initials": _initials_for(owner_name),
        "is_self_owned": c.owner_id == caller.id,
        "is_redacted": False,
        # The owner sees their own reveal_fields config so they can
        # adjust it. Non-owners viewing the full row (public contact or
        # explicitly shared) don't need it.
        "reveal_fields": (
            list(c.reveal_fields or DEFAULT_REVEAL_FIELDS)
            if c.owner_id == caller.id
            else None
        ),
        # ISO timestamps so the frontend can show the auto-UPDATED stamp
        # (typewritten note that appears on the card for 14 days after a
        # contact is modified).
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _owner_name_lookup(rows: list[Contact], db: Session) -> dict[int, str | None]:
    """Single-query lookup of `{owner_id: owner.name}` for the given
    rows. Avoids the N+1 that would come from per-row db.get(User, ...).
    Returns None for any owner whose name isn't set."""
    owner_ids = {c.owner_id for c in rows}
    if not owner_ids:
        return {}
    users = list(db.scalars(select(User).where(User.id.in_(owner_ids))))
    return {u.id: u.name for u in users}


# Owner-name → corner-badge initials. Explicit overrides cover names
# where the "drop the last word as surname" heuristic gets the wrong
# answer — the heuristic drops the last word as a surname, so a plain
# First-Last name yields a single initial; the overrides give the fuller
# two-letter badge. Keyed by full owner name as stored on User.name.
_OWNER_INITIALS_OVERRIDE: dict[str, str] = {
    "Alex Rivera": "AR",
    "Sam Chen": "SC",
    "Jordan Blake": "JB",
}


def _effective_reveal_set(c: Contact) -> set[str]:
    """Defensive: intersect the row's reveal_fields with the allowed
    whitelist. A bad value sneaking into the DB (e.g. someone manually
    setting reveal_fields=['notes']) is filtered out here so it can
    never leak via a tool result."""
    declared = set(c.reveal_fields or DEFAULT_REVEAL_FIELDS)
    return declared & ALLOWED_REVEAL_FIELDS


def _redacted_view(c: Contact, *, owner_name: str | None) -> dict[str, Any]:
    """Build the partial-reveal shape for a private contact owned by a
    teammate. PII columns are forced to null; only fields in the
    intersection of the row's reveal_fields and ALLOWED_REVEAL_FIELDS
    are populated. The owner's name + initials are included so Goddess
    can say 'ask <Owner>'.
    """
    reveal = _effective_reveal_set(c)
    return {
        "id": c.id,
        "name": "Private contact",
        "company_name": c.company_name if "company_name" in reveal else None,
        "title": None,
        "email": None,
        "cell_phone": None,
        "office_phone": None,
        "primary_fund": c.primary_fund if "primary_fund" in reveal else None,
        "contact_type": c.contact_type if "contact_type" in reveal else None,
        "sectors": (list(c.sectors or []) if "sectors" in reveal else []),
        "is_private": True,
        "is_redacted": True,
        "gender": c.gender if "gender" in reveal else None,
        "country": c.country if "country" in reveal else None,
        "lp_subtype": c.lp_subtype if "lp_subtype" in reveal else None,
        "fly_status": c.fly_status if "fly_status" in reveal else None,
        "image_url": None,
        "ex_government": c.ex_government if "ex_government" in reveal else None,
        # is_gov_employee never revealed on a redacted row. It's a
        # status flag derived from PII (email domain), so leaking it
        # would partially leak the email-domain identity of a private
        # contact. Hidden uniformly until the contact is fully visible.
        "is_gov_employee": False,
        "patina_overrides": None,
        "owner_name": owner_name,
        "owner_initials": _initials_for(owner_name),
        # By construction this row is owned by a teammate.
        "is_self_owned": False,
        "reveal_fields": sorted(reveal),
        "created_at": None,
        "updated_at": None,
    }


def _redacted_row_matches(c: Contact, params: SearchContactsInput) -> bool:
    """Decide whether a redacted candidate row satisfies the search
    filters using ONLY its revealed columns. Any filter that targets a
    hidden field disqualifies the row — that's how we keep the
    free-text query from leaking name/title/notes matches.

    A row with no filters at all always matches (existence hint).
    """
    reveal = _effective_reveal_set(c)

    if params.query:
        # For redacted rows the query can only match company_name, and
        # only if the owner revealed it. name/title/notes are hidden.
        if "company_name" not in reveal:
            return False
        haystack = (c.company_name or "").lower()
        if params.query.lower() not in haystack:
            return False

    # Each structured filter must (a) be revealed AND (b) match.
    def _filter_ok(field: str, expected: Any, actual: Any) -> bool:
        if expected is None:
            return True
        if field not in reveal:
            return False
        return expected == actual

    if not _filter_ok("primary_fund", params.primary_fund, c.primary_fund):
        return False
    if not _filter_ok("contact_type", params.contact_type, c.contact_type):
        return False
    if not _filter_ok("gender", params.gender, c.gender):
        return False
    if not _filter_ok("lp_subtype", params.lp_subtype, c.lp_subtype):
        return False
    if not _filter_ok("fly_status", params.fly_status, c.fly_status):
        return False
    if not _filter_ok("ex_government", params.ex_government, c.ex_government):
        return False
    if params.country is not None:
        if "country" not in reveal:
            return False
        if (c.country or "").lower() != params.country.lower():
            return False

    return True


def _initials_for(name: str | None) -> str | None:
    """Compute a short, all-caps badge label (1–3 chars) for the corner
    ownership pill. Logic:
      - empty / None -> None
      - explicit override wins (see _OWNER_INITIALS_OVERRIDE)
      - 1 word -> first letter
      - 2+ words -> initial of every word except the last (treated as
        surname). 'Ada B. Lovelace' -> 'AB'.
    """
    if not name:
        return None
    name = name.strip()
    if not name:
        return None
    if name in _OWNER_INITIALS_OVERRIDE:
        return _OWNER_INITIALS_OVERRIDE[name]
    parts = name.split()
    if len(parts) == 1:
        return parts[0][0].upper()
    return "".join(p[0].upper() for p in parts[:-1])


def _handle_search(
    params: SearchContactsInput, user: User, db: Session
) -> dict[str, Any]:
    stmt = visible_contacts_query(user)
    if params.query:
        like = f"%{params.query}%"
        stmt = stmt.where(
            or_(
                Contact.name.ilike(like),
                Contact.company_name.ilike(like),
                Contact.title.ilike(like),
                Contact.notes.ilike(like),
            )
        )
    if params.primary_fund:
        stmt = stmt.where(Contact.primary_fund == params.primary_fund)
    if params.contact_type:
        stmt = stmt.where(Contact.contact_type == params.contact_type)
    if params.gender:
        stmt = stmt.where(Contact.gender == params.gender)
    if params.country:
        # Case-insensitive exact match — Claude is responsible for
        # canonicalizing voice forms ("Saudi" → "Saudi Arabia").
        stmt = stmt.where(func.lower(Contact.country) == params.country.lower())
    if params.metro:
        stmt = stmt.where(func.lower(Contact.metro) == params.metro.lower())
    if params.lp_subtype:
        stmt = stmt.where(Contact.lp_subtype == params.lp_subtype)
    if params.fly_status:
        stmt = stmt.where(Contact.fly_status == params.fly_status)
    if params.ex_government:
        stmt = stmt.where(Contact.ex_government == params.ex_government)
    if params.opt_in_status:
        stmt = stmt.where(Contact.opt_in_status == params.opt_in_status)

    # Order: Must Fly first ... Off Fly List last (still visible — they
    # can come up if asked for explicitly). Within each tier, fall back to
    # name for stability. The ranking (incl. the "Not Sure Yet" legacy
    # alias) comes from the shared fly_status source of truth so it can't
    # drift from the engine's affinity weights.
    fly_priority = case(
        fly_status_search_priority(),
        value=Contact.fly_status,
        else_=unknown_search_rank(),
    )
    stmt = stmt.order_by(fly_priority, Contact.name)

    visible_rows = list(db.scalars(stmt.limit(SEARCH_RESULT_LIMIT + 1)))
    visible_overflow = len(visible_rows) > SEARCH_RESULT_LIMIT
    visible_rows = visible_rows[:SEARCH_RESULT_LIMIT]

    # Phase 2 Slice 6.5 — also pull redactable candidates (private
    # contacts owned by teammates who allow existence hints). Apply the
    # search filters against each candidate's revealed fields only —
    # `_redacted_row_matches` is the security gate that prevents a
    # free-text query from leaking name/title/notes matches.
    #
    # Visible rows take precedence; redacted rows fill any remaining
    # slots up to SEARCH_RESULT_LIMIT. truncated=True when EITHER pool
    # was capped.
    remaining = SEARCH_RESULT_LIMIT - len(visible_rows)
    redacted_rows: list[Contact] = []
    redacted_overflow = False
    if remaining > 0:
        redacted_candidates = list(
            db.scalars(redactable_contacts_query(user).limit(SEARCH_RESULT_LIMIT + 1))
        )
        matched = [c for c in redacted_candidates if _redacted_row_matches(c, params)]
        redacted_rows = matched[:remaining]
        redacted_overflow = len(matched) > remaining

    owner_names = _owner_name_lookup(visible_rows + redacted_rows, db)

    formatted: list[dict[str, Any]] = [
        _format_contact(c, caller=user, owner_name=owner_names.get(c.owner_id))
        for c in visible_rows
    ]
    formatted.extend(
        _redacted_view(c, owner_name=owner_names.get(c.owner_id)) for c in redacted_rows
    )

    # Audit each redacted exposure so the owner can later see who
    # learned of the contact's existence. One row per redacted contact
    # surfaced — bounded by the result limit so worst case is small.
    for c in redacted_rows:
        write_audit_row(
            db,
            user,
            action="redacted_reveal",
            target_type="contact",
            target_id=c.id,
            payload_hash=hash_payload(params),
        )

    return {
        "count": len(formatted),
        "truncated": visible_overflow or redacted_overflow,
        "limit": SEARCH_RESULT_LIMIT,
        "results": formatted,
    }


def _handle_create(
    params: CreateContactInput, user: User, db: Session
) -> dict[str, Any]:
    create_kwargs = params.model_dump()
    # Slice 6.11 — auto-detect current-government-employee status from
    # the email domain when the caller didn't pass an explicit value.
    # Explicit False overrides the auto-detect (e.g. a contractor with
    # a .gov inbox who isn't actually a gov employee).
    if create_kwargs.get("is_gov_employee") is None:
        create_kwargs["is_gov_employee"] = looks_like_gov_email(
            create_kwargs.get("email")
        )
    contact = Contact(**create_kwargs, owner_id=user.id)
    # Auto-link to the team user this contact *is*, by email match — so the
    # warm-intro engine can scope paths to that teammate's own relationships.
    contact.user_id = user_id_for_email(db, contact.email)
    db.add(contact)
    db.commit()
    db.refresh(contact)
    initial_fields = {
        field: _jsonable(getattr(contact, field)) for field in _CREATE_METADATA_FIELDS
    }
    write_audit_row(
        db,
        user,
        action="create_contact",
        target_type="contact",
        target_id=contact.id,
        payload_hash=hash_payload(params),
        payload_metadata={"initial_fields": initial_fields},
    )
    return {"created": _format_contact(contact, caller=user, owner_name=user.name)}


def _handle_update(
    params: UpdateContactInput, user: User, db: Session
) -> dict[str, Any]:
    stmt = visible_contacts_query(user).where(Contact.id == params.contact_id)
    contact = db.scalars(stmt).first()
    if contact is None:
        return {
            "error": "not_found",
            "message": f"Contact {params.contact_id} is not visible to you.",
        }
    if contact.owner_id != user.id:
        # Specific guidance for owner-only paths: fly list removal and
        # patina edits. Non-owners should retry through request_change —
        # the system prompt teaches Claude to do that automatically.
        update_dict_check = params.model_dump(
            exclude={"contact_id"}, exclude_unset=True
        )
        if update_dict_check.get("fly_status") == "Off Fly List":
            return {
                "error": "forbidden_owner_only",
                "message": (
                    f"Only the owner of contact {params.contact_id} can take "
                    "them off the fly list. File this via request_change "
                    'with payload {"kind": "off_fly_list"} so the owner can '
                    "review."
                ),
            }
        if "patina_overrides" in update_dict_check:
            return {
                "error": "forbidden_owner_only",
                "message": (
                    f"Only the owner of contact {params.contact_id} can "
                    "customize their patina marks. File this via "
                    'request_change with payload {"kind": "patina_override", '
                    '"items": [...]} so the owner can review.'
                ),
            }
        return {
            "error": "forbidden",
            "message": (
                f"Contact {params.contact_id} is visible but not owned by you."
            ),
        }
    update_dict = params.model_dump(exclude={"contact_id"}, exclude_unset=True)
    # Snapshot the BEFORE values so we can record a per-field diff for
    # the change log. Read these BEFORE setattr clobbers them.
    before = {field: _jsonable(getattr(contact, field)) for field in update_dict}
    for field, value in update_dict.items():
        setattr(contact, field, value)
    db.commit()
    db.refresh(contact)
    after = {field: _jsonable(getattr(contact, field)) for field in update_dict}
    changes = [
        {"field": field, "old": before[field], "new": after[field]}
        for field in update_dict
        if before[field] != after[field]
    ]
    write_audit_row(
        db,
        user,
        action="update_contact",
        target_type="contact",
        target_id=contact.id,
        payload_hash=hash_payload(params),
        payload_metadata={"changes": changes} if changes else None,
    )
    return {"updated": _format_contact(contact, caller=user, owner_name=user.name)}


def _handle_link_contacts(
    params: LinkContactsInput, user: User, db: Session
) -> dict[str, Any]:
    """Create (or re-assert) a who-knows-whom edge for the intro engine.

    Privacy stacks on the graph: both endpoints must be visible to the
    caller — you can't link through a contact you can't see. The edge is
    an idempotent upsert on (from, to, relationship_type): re-recording
    the same pair updates it in place (and revives a soft-deleted edge)
    rather than violating the unique constraint or stacking duplicates.
    """
    src = db.scalars(
        visible_contacts_query(user).where(Contact.id == params.from_contact_id)
    ).first()
    dst = db.scalars(
        visible_contacts_query(user).where(Contact.id == params.to_contact_id)
    ).first()
    missing = [
        cid
        for cid, row in (
            (params.from_contact_id, src),
            (params.to_contact_id, dst),
        )
        if row is None
    ]
    if missing:
        return {
            "error": "not_found",
            "message": f"Contact(s) {missing} not visible to you — can't link them.",
        }
    assert src is not None and dst is not None  # narrowing for type-checker

    existing = db.scalars(
        select(Relationship).where(
            Relationship.from_contact_id == params.from_contact_id,
            Relationship.to_contact_id == params.to_contact_id,
            Relationship.relationship_type == params.relationship_type,
        )
    ).first()
    if existing is not None:
        existing.shared_history = params.shared_history
        existing.notes = params.notes
        existing.source = "manual"
        existing.deleted_at = None  # revive if it had been removed
        edge = existing
        created = False
    else:
        edge = Relationship(
            from_contact_id=params.from_contact_id,
            to_contact_id=params.to_contact_id,
            relationship_type=params.relationship_type,
            shared_history=params.shared_history,
            notes=params.notes,
            source="manual",
            confidence=1.0,
            created_by_user_id=user.id,
        )
        db.add(edge)
        created = True
    db.commit()
    db.refresh(edge)

    write_audit_row(
        db,
        user,
        action="link_contacts",
        target_type="relationship",
        target_id=edge.id,
        payload_hash=hash_payload(params),
        payload_metadata={
            "from_contact_id": params.from_contact_id,
            "to_contact_id": params.to_contact_id,
            "relationship_type": params.relationship_type,
            "shared_history": params.shared_history,
            "created": created,
        },
    )
    return {
        "linked": {
            "id": edge.id,
            "from": {"id": src.id, "name": src.name},
            "to": {"id": dst.id, "name": dst.name},
            "relationship_type": edge.relationship_type,
            "shared_history": edge.shared_history,
            "created": created,
        }
    }


def _handle_delete(
    params: DeleteContactInput, user: User, db: Session
) -> dict[str, Any]:
    """Soft-delete an owned contact. Sets deleted_at; visible_contacts_query
    filters it out of every subsequent read.

    Ownership semantics mirror _handle_update: 404 when not visible, 403
    when visible but not owned. Destructive — the system prompt tells
    Claude to confirm with the user first."""
    from datetime import datetime, timezone

    stmt = visible_contacts_query(user).where(Contact.id == params.contact_id)
    contact = db.scalars(stmt).first()
    if contact is None:
        return {
            "error": "not_found",
            "message": f"Contact {params.contact_id} is not visible to you.",
        }
    if contact.owner_id != user.id:
        return {
            "error": "forbidden",
            "message": (
                f"Contact {params.contact_id} is visible but not owned by you."
            ),
        }
    # Snapshot a small last-state summary BEFORE flipping deleted_at,
    # so the changelog can show "Deleted Marcus Sterling (Ironclad)".
    deletion_snapshot = {
        "name": contact.name,
        "company_name": contact.company_name,
        "primary_fund": contact.primary_fund,
        "contact_type": contact.contact_type,
    }
    contact.deleted_at = datetime.now(timezone.utc)
    db.commit()
    write_audit_row(
        db,
        user,
        action="delete_contact",
        target_type="contact",
        target_id=contact.id,
        payload_hash=hash_payload(params),
        payload_metadata={"last_state": deletion_snapshot},
    )
    return {"deleted": {"id": contact.id, "name": contact.name}}


def _handle_summary(
    params: PipelineSummaryInput, user: User, db: Session
) -> dict[str, Any]:
    stmt = visible_contacts_query(user)
    if params.primary_fund:
        stmt = stmt.where(Contact.primary_fund == params.primary_fund)
    contacts = list(db.scalars(stmt))
    summary: dict[str, dict[str, int]] = {}
    for c in contacts:
        summary.setdefault(c.primary_fund, {})
        summary[c.primary_fund][c.contact_type] = (
            summary[c.primary_fund].get(c.contact_type, 0) + 1
        )
    return {
        "total": len(contacts),
        "by_fund_and_type": summary,
        "fund_filter": params.primary_fund,
    }


def _handle_find_intro_paths(
    params: FindIntroPathsInput, user: User, db: Session
) -> dict[str, Any]:
    """Find warm-introduction paths to a target contact.

    Thin wrapper over the pathfinder service: it already enforces the
    three-tier privacy filter (only contacts the caller can see are used
    or named), the blocklist, and the opt-in gate. Here we just shape the
    ScoredPath value objects into the JSON Claude reads back to the user.

    `not_found` when the target isn't visible to the caller. A visible
    target with no usable route returns count=0 / paths=[] (not an error)
    so Claude can say 'no warm path' rather than 'no such person'.
    """
    result = find_intro_paths_service(
        db, user, params.target_contact_id, max_results=params.max_results
    )
    if result.target is None:
        return {
            "error": "not_found",
            "message": (
                f"Contact {params.target_contact_id} is not visible to you, "
                "so I can't look for intro paths to them."
            ),
        }

    paths = [
        {
            # The first intermediary is whom the user would actually
            # reach out to; surface their warmth so Claude can explain why.
            "reach_out_to": {
                "id": sp.path.intermediaries[0].contact_id,
                "name": sp.path.intermediaries[0].name,
                "fly_status": sp.path.intermediaries[0].fly_status,
            },
            # Full route, names in order, ending at the target.
            "chain": [n.name for n in sp.path.intermediaries] + [sp.path.target.name],
            "hops": sp.degrees,
            "score": round(sp.score, 4),
        }
        for sp in result.paths
    ]
    return {
        "target": {"id": result.target.contact_id, "name": result.target.name},
        "count": len(paths),
        "paths": paths,
    }


def _handle_request_change(
    params: Any, user: User, db: Session  # RequestChangeInput
) -> dict[str, Any]:
    """File a change request. Refuses if requester is the contact's
    owner — owners should edit directly via update_contact."""
    from app.models import ChangeRequest

    contact = db.get(Contact, params.contact_id)
    if contact is None:
        return {
            "error": "not_found",
            "message": f"Contact {params.contact_id} does not exist.",
        }
    # Even non-owner can request, but they must AT LEAST be able to see
    # the contact (privacy filter applies).
    visible_ids = {c.id for c in db.scalars(visible_contacts_query(user))}
    if contact.id not in visible_ids:
        return {
            "error": "not_found",
            "message": f"Contact {params.contact_id} is not visible to you.",
        }
    if contact.owner_id == user.id:
        return {
            "error": "owner_should_edit_directly",
            "message": (
                f"You own contact {params.contact_id} — edit it via "
                "update_contact instead of filing a request."
            ),
        }

    # Pydantic dumped the discriminated union into a dict already; store
    # the inner items only for patina_override (off_fly_list has no body).
    payload_dict = params.payload.model_dump()
    if payload_dict.get("kind") == "off_fly_list":
        stored_payload = None
    else:
        stored_payload = payload_dict  # includes "items"

    cr = ChangeRequest(
        requester_id=user.id,
        contact_id=contact.id,
        kind=params.payload.kind,
        payload=stored_payload,
        reason=params.reason,
    )
    db.add(cr)
    db.commit()
    db.refresh(cr)
    write_audit_row(
        db,
        user,
        action="request_change",
        target_type="change_request",
        target_id=cr.id,
        payload_hash=hash_payload(params),
        payload_metadata={
            "kind": cr.kind,
            "request_id": cr.id,
            "contact_id": contact.id,
            "reason": params.reason,
        },
    )
    return {
        "request_id": cr.id,
        "contact_id": cr.contact_id,
        "kind": cr.kind,
        "status": cr.status,
        "owner_id": contact.owner_id,
    }


def _handle_resolve_change_request(
    params: Any, user: User, db: Session  # ResolveChangeRequestInput
) -> dict[str, Any]:
    """Owner only: approve (apply change) or disapprove (close)."""
    from datetime import datetime, timezone

    from app.models import ChangeRequest

    cr = db.get(ChangeRequest, params.request_id)
    if cr is None:
        return {
            "error": "not_found",
            "message": f"Change request {params.request_id} does not exist.",
        }
    if cr.status != "pending":
        return {
            "error": "already_resolved",
            "message": (
                f"Request {cr.id} was already {cr.status}; cannot resolve again."
            ),
        }
    contact = db.get(Contact, cr.contact_id)
    if contact is None or contact.owner_id != user.id:
        return {
            "error": "forbidden_owner_only",
            "message": (
                "Only the contact's owner can resolve a change request " "against it."
            ),
        }

    if params.decision == "approve":
        if cr.kind == "off_fly_list":
            contact.fly_status = "Off Fly List"
        elif cr.kind == "patina_override":
            # Pull the items list out of the stored payload dict.
            items = (cr.payload or {}).get("items", []) if cr.payload else []
            contact.patina_overrides = items

    cr.status = "approved" if params.decision == "approve" else "disapproved"
    cr.resolution_note = params.note
    cr.resolved_at = datetime.now(timezone.utc)
    cr.resolved_by_id = user.id
    db.commit()
    db.refresh(cr)
    write_audit_row(
        db,
        user,
        action="resolve_change_request",
        target_type="change_request",
        target_id=cr.id,
        payload_hash=hash_payload(params),
        payload_metadata={
            "kind": cr.kind,
            "decision": params.decision,
            "applied": params.decision == "approve",
            "note": params.note,
            "request_id": cr.id,
            "contact_id": cr.contact_id,
        },
    )
    return {
        "request_id": cr.id,
        "status": cr.status,
        "applied": params.decision == "approve",
    }


def _handle_create_google_task(
    params: CreateGoogleTaskInput, user: User, db: Session
) -> dict[str, Any]:
    """Drop a 'Talk to <Owner>' task in the calling user's Google Tasks.

    Ownership rules:
      - contact must be visible to the caller (privacy filter applies);
      - caller must NOT own the contact (no point reminding yourself
        to talk to your own contact);
      - owner name is read from the owner User row — Goddess's prompt
        never gets to pick it.
    """
    stmt = visible_contacts_query(user).where(Contact.id == params.contact_id)
    contact = db.scalars(stmt).first()
    if contact is None:
        return {
            "error": "not_found",
            "message": f"Contact {params.contact_id} is not visible to you.",
        }
    if contact.owner_id == user.id:
        return {
            "error": "self_owned",
            "message": (
                f"You own contact {params.contact_id} — no need to file a "
                "reminder to talk to yourself."
            ),
        }
    owner = db.get(User, contact.owner_id)
    if owner is None or not owner.name:
        # Defensive: every contact has a real owner with a name, but if
        # the seed/migration ever drifted we'd rather surface this than
        # crash inside the Google call.
        return {
            "error": "owner_unknown",
            "message": (f"Contact {params.contact_id} has no resolvable owner name."),
        }
    if not user.google_access_token:
        return {
            "error": "no_google_token",
            "message": (
                "You need to sign in with Google before I can add this to "
                "your Tasks. Sign out and back in to grant access."
            ),
        }

    task_title = f"Talk to {owner.name} about {contact.name}"

    try:
        created = create_talk_to_task(
            owner_name=owner.name,
            task_title=task_title,
            notes=params.note,
            access_token=user.google_access_token,
            refresh_token=user.google_refresh_token,
        )
    except GoogleTasksScopeError as e:
        return {
            "error": "google_tasks_scope",
            "message": (
                "Your Google account hasn't granted the Tasks scope (or "
                "revoked it). Sign out and back in to re-grant. "
                f"Details: {e}"
            ),
        }
    except GoogleTasksQuotaError as e:
        return {
            "error": "google_tasks_quota",
            "message": f"Google Tasks is rate-limited; try again shortly. Details: {e}",
        }
    except GoogleTasksError as e:
        return {
            "error": "google_tasks_error",
            "message": f"Google Tasks call failed: {e}",
        }

    write_audit_row(
        db,
        user,
        action="create_google_task",
        target_type="contact",
        target_id=contact.id,
        payload_hash=hash_payload(params),
    )
    return {
        "task_id": created.task_id,
        "task_list_id": created.task_list_id,
        "task_list_title": created.task_list_title,
        "owner_name": owner.name,
        "contact_id": contact.id,
        "contact_name": contact.name,
    }


def _handle_transfer_contact(
    params: TransferContactInput, user: User, db: Session
) -> dict[str, Any]:
    """Move ownership of a contact to a teammate.

    Authorization: caller is the current owner OR caller.role=='admin'.
    Admin bypass uses `db.get(Contact, id)` directly (not the privacy
    filter) so an admin can reassign even a private contact they
    couldn't otherwise see — the use case is 'teammate leaves the team,
    redistribute their contacts.'
    """
    is_admin = (user.role or "").lower() == "admin"
    if is_admin:
        contact = db.get(Contact, params.contact_id)
    else:
        stmt = visible_contacts_query(user).where(Contact.id == params.contact_id)
        contact = db.scalars(stmt).first()

    if contact is None or contact.deleted_at is not None:
        return {
            "error": "not_found",
            "message": f"Contact {params.contact_id} does not exist or is deleted.",
        }
    if not is_admin and contact.owner_id != user.id:
        return {
            "error": "forbidden",
            "message": (
                f"Only the contact's owner or an admin can transfer it. "
                f"Contact {params.contact_id} is not yours."
            ),
        }

    new_owner = db.scalars(
        select(User).where(func.lower(User.email) == params.new_owner_email.lower())
    ).first()
    if new_owner is None:
        return {
            "error": "unknown_teammate",
            "message": (
                f"{params.new_owner_email} is not an DIN teammate. "
                "Transfer only works between team members."
            ),
        }
    if new_owner.id == contact.owner_id:
        return {
            "error": "already_owner",
            "message": (
                f"Contact {params.contact_id} is already owned by "
                f"{new_owner.email} — no transfer needed."
            ),
        }

    old_owner_id = contact.owner_id
    old_owner = db.get(User, old_owner_id)
    old_owner_name = old_owner.name if old_owner else None
    contact.owner_id = new_owner.id
    db.commit()
    db.refresh(contact)
    write_audit_row(
        db,
        user,
        action="transfer_contact",
        target_type="contact",
        target_id=contact.id,
        payload_hash=hash_payload(params),
        payload_metadata={
            "old_owner_id": old_owner_id,
            "old_owner_name": old_owner_name,
            "new_owner_id": new_owner.id,
            "new_owner_name": new_owner.name,
            "by_admin": is_admin and old_owner_id != user.id,
        },
    )
    return {
        "transferred": {
            "contact_id": contact.id,
            "contact_name": contact.name,
            "old_owner_id": old_owner_id,
            "new_owner_id": new_owner.id,
            "new_owner_name": new_owner.name,
            "new_owner_email": new_owner.email,
            "by_admin": is_admin and old_owner_id != user.id,
        }
    }


def _handle_create_next_step(
    params: CreateNextStepInput, user: User, db: Session
) -> dict[str, Any]:
    """Slice 6.10 — add a forward-looking todo to the contact's Next
    Steps log. The OWNER is the teammate who owes the action, looked up
    by email; must be on the DIN team. Visibility: caller must be able
    to see the contact in full (private redacted view callers can't
    create steps — the contact isn't theirs to act on).

    Side effect: best-effort Google Tasks task on the OWNER's
    'DIN: Next Steps' list. The owner's google_access_token is used,
    NOT the caller's — the reminder appears on the OWNER's account.
    Tasks failure doesn't block the in-app row (returns a warning).
    """
    stmt = visible_contacts_query(user).where(Contact.id == params.contact_id)
    contact = db.scalars(stmt).first()
    if contact is None:
        return {
            "error": "not_found",
            "message": f"Contact {params.contact_id} is not visible to you.",
        }
    owner = db.scalars(
        select(User).where(func.lower(User.email) == params.owner_email.lower())
    ).first()
    if owner is None:
        return {
            "error": "unknown_teammate",
            "message": (
                f"{params.owner_email} is not an DIN teammate. Next-step "
                "owners must be team members."
            ),
        }

    step = NextStep(
        contact_id=contact.id,
        owner_id=owner.id,
        created_by_id=user.id,
        title=params.title,
    )
    db.add(step)
    db.commit()
    db.refresh(step)

    # Best-effort Google Tasks mirror on the OWNER's account.
    # Notes string makes the assignment explicit: the owner sees who's
    # responsible + who created it + which contact, useful when scanning
    # the list outside the app context.
    assignee_label = owner.name or owner.email
    creator_label = user.name or user.email
    notes_body = (
        f"Assigned to: {assignee_label}\n"
        f"For contact: {contact.name}\n"
        f"Created by: {creator_label}"
    )
    tasks_warning: str | None = None
    if owner.google_access_token:
        try:
            created = create_next_step_task(
                contact_name=contact.name,
                title=params.title,
                notes=notes_body,
                access_token=owner.google_access_token,
                refresh_token=owner.google_refresh_token,
            )
            step.google_task_id = created.task_id
            step.google_task_list_id = created.task_list_id
            db.commit()
        except GoogleTasksScopeError as e:
            tasks_warning = (
                f"Google Tasks scope error for {owner.name} — the in-app "
                f"next step was saved but no Tasks mirror was created. "
                f"({e})"
            )
        except (GoogleTasksQuotaError, GoogleTasksError) as e:
            tasks_warning = (
                f"Google Tasks call failed — in-app next step saved "
                f"without mirror. ({e})"
            )
    else:
        tasks_warning = (
            f"{owner.name} hasn't signed in with Google yet; in-app next "
            "step saved without a Tasks mirror."
        )

    write_audit_row(
        db,
        user,
        action="create_next_step",
        target_type="contact",
        target_id=contact.id,
        payload_hash=hash_payload(params),
        payload_metadata={
            "next_step_id": step.id,
            "title": params.title,
            "owner_id": owner.id,
            "owner_name": owner.name,
        },
    )

    return {
        "next_step": {
            "id": step.id,
            "contact_id": contact.id,
            "title": step.title,
            "owner_id": owner.id,
            "owner_name": owner.name,
            "google_task_list_id": step.google_task_list_id,
        },
        "warning": tasks_warning,
    }


def _handle_complete_next_step(
    params: CompleteNextStepInput, user: User, db: Session
) -> dict[str, Any]:
    """Mark a next-step done. Allowed callers: the step's owner OR the
    contact's owner. Also flips the linked Google Tasks task to
    completed on the owner's account, best-effort."""
    from datetime import datetime, timezone

    step = db.get(NextStep, params.next_step_id)
    if step is None or step.done:
        return {
            "error": "not_found_or_done",
            "message": (
                f"Next-step {params.next_step_id} doesn't exist or is " "already done."
            ),
        }
    contact = db.get(Contact, step.contact_id)
    if contact is None:
        return {
            "error": "not_found",
            "message": "Underlying contact is gone.",
        }
    if user.id not in (step.owner_id, contact.owner_id):
        return {
            "error": "forbidden",
            "message": (
                "Only the step's owner or the contact's owner can " "mark this done."
            ),
        }

    step.done = True
    step.done_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(step)

    # Best-effort mirror to Google Tasks on the OWNER's account.
    if step.google_task_id and step.google_task_list_id:
        owner = db.get(User, step.owner_id)
        if owner and owner.google_access_token:
            try:
                gt_complete_task(
                    task_list_id=step.google_task_list_id,
                    task_id=step.google_task_id,
                    access_token=owner.google_access_token,
                    refresh_token=owner.google_refresh_token,
                )
            except (
                GoogleTasksScopeError,
                GoogleTasksQuotaError,
                GoogleTasksError,
            ):
                # Best-effort — local row is the source of truth.
                pass

    write_audit_row(
        db,
        user,
        action="complete_next_step",
        target_type="contact",
        target_id=step.contact_id,
        payload_hash=hash_payload(params),
        payload_metadata={
            "next_step_id": step.id,
            "title": step.title,
        },
    )
    return {"completed": {"id": step.id, "done_at": step.done_at.isoformat()}}


HANDLERS: dict[str, Callable[[Any, User, Session], dict[str, Any]]] = {
    "search_contacts": _handle_search,
    "create_contact": _handle_create,
    "update_contact": _handle_update,
    "delete_contact": _handle_delete,
    "link_contacts": _handle_link_contacts,
    "find_intro_paths": _handle_find_intro_paths,
    "get_pipeline_summary": _handle_summary,
    "request_change": _handle_request_change,
    "resolve_change_request": _handle_resolve_change_request,
    "create_google_task": _handle_create_google_task,
    "transfer_contact": _handle_transfer_contact,
    "create_next_step": _handle_create_next_step,
    "complete_next_step": _handle_complete_next_step,
}


def dispatch_tool_call(
    name: str,
    params: dict[str, Any],
    current_user: User,
    db: Session,
) -> dict[str, Any]:
    """Validate name + params, then run the matching handler."""
    if name not in TOOL_REGISTRY:
        raise ToolDispatchError(f"Unknown tool: {name!r}")
    if name not in HANDLERS:
        raise ToolDispatchError(f"No handler registered for tool: {name!r}")

    spec = TOOL_REGISTRY[name]
    try:
        validated = spec.input_model(**params)
    except PydanticValidationError as e:
        raise ToolDispatchError(f"Invalid params for {name}: {e.errors()}") from e

    return HANDLERS[name](validated, current_user, db)
