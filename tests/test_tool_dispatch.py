"""Tests for the Claude tool dispatcher.

The privacy regression tests here are the "even if Claude is tricked"
defense — they prove that no matter what Claude calls, the privacy
filter still applies.
"""

from typing import Callable

import pytest
from sqlalchemy.orm import Session

from app.models import Contact, User
from app.services.tool_dispatch import ToolDispatchError, dispatch_tool_call


def _make_contact(
    db: Session,
    owner: User,
    *,
    name: str = "Person",
    company_name: str | None = None,
    primary_fund: str = "General",
    contact_type: str = "Other",
    is_private: bool = False,
    gender: str = "Unknown",
    country: str | None = None,
    lp_subtype: str | None = None,
) -> Contact:
    contact = Contact(
        name=name,
        company_name=company_name,
        primary_fund=primary_fund,
        contact_type=contact_type,
        is_private=is_private,
        gender=gender,
        country=country,
        lp_subtype=lp_subtype,
        owner_id=owner.id,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def test_unknown_tool_name_raises(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    with pytest.raises(ToolDispatchError, match="Unknown tool"):
        dispatch_tool_call("delete_database", {}, user, db)


def test_malformed_params_raise(db: Session, user_factory: Callable[..., User]) -> None:
    user = user_factory()
    # search accepts only the known enum for primary_fund
    with pytest.raises(ToolDispatchError, match="Invalid params"):
        dispatch_tool_call("search_contacts", {"primary_fund": "Aerospace"}, user, db)


def test_create_missing_required_raises(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    with pytest.raises(ToolDispatchError, match="Invalid params"):
        dispatch_tool_call("create_contact", {}, user, db)  # name required


def test_search_owner_sees_full_private(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """The owner sees their own private contact in full (name, etc.)."""
    user_a = user_factory(email="alice@test.fake")
    _make_contact(db, user_a, name="Public A", is_private=False)
    _make_contact(db, user_a, name="Private A", is_private=True)

    a_result = dispatch_tool_call("search_contacts", {}, user_a, db)
    assert a_result["count"] == 2
    names = {r["name"] for r in a_result["results"]}
    assert names == {"Public A", "Private A"}
    # Owner rows are not redacted.
    assert all(r["is_redacted"] is False for r in a_result["results"])


def test_search_non_owner_sees_redacted_private_not_full_name(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Phase 2 Slice 6.5 — non-owner sees a redacted preview of a private
    contact, never the real name/PII. Default reveal set covers
    primary_fund + company_name + sectors; everything else (name, email,
    phones, title) is forced null on the redacted view."""
    user_a = user_factory(email="alice@test.fake")
    user_b = user_factory(email="bob@test.fake")
    _make_contact(db, user_a, name="Public A", is_private=False)
    _make_contact(
        db,
        user_a,
        name="Alice's Secret LP",
        company_name="ADIA",
        primary_fund="Energy",
        is_private=True,
    )

    b_result = dispatch_tool_call("search_contacts", {}, user_b, db)
    assert b_result["count"] == 2  # 1 public + 1 redacted

    by_redacted = {r["is_redacted"]: r for r in b_result["results"]}
    full = by_redacted[False]
    redacted = by_redacted[True]

    assert full["name"] == "Public A"

    # Redacted row never leaks the real name or any PII column.
    assert redacted["name"] == "Private contact"
    assert redacted["email"] is None
    assert redacted["cell_phone"] is None
    assert redacted["office_phone"] is None
    assert redacted["title"] is None
    assert redacted["image_url"] is None

    # Revealed fields are populated.
    assert redacted["company_name"] == "ADIA"
    assert redacted["primary_fund"] == "Energy"
    assert redacted["is_private"] is True
    assert redacted["is_self_owned"] is False
    assert redacted["owner_name"] == user_a.name


def test_search_hides_private_when_owner_disabled_existence_hints(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """allow_existence_hints=False on the OWNER reverts to Phase 1
    full-hide: non-owner never learns the contact exists at all."""
    user_a = user_factory(email="alice@test.fake")
    user_a.allow_existence_hints = False
    db.commit()
    user_b = user_factory(email="bob@test.fake")
    _make_contact(db, user_a, name="Alice's Secret", is_private=True)

    b_result = dispatch_tool_call("search_contacts", {}, user_b, db)
    assert b_result["count"] == 0


def test_redacted_row_only_matches_revealed_fields_on_query(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Free-text query against a redacted candidate can only match the
    revealed fields. If a teammate searches for the contact's NAME
    (which is hidden), the row must NOT surface."""
    user_a = user_factory(email="alice@test.fake")
    user_b = user_factory(email="bob@test.fake")
    contact = Contact(
        name="Marcus Smith",  # hidden
        company_name="Ironclad",  # revealed (default)
        primary_fund="Maritime",
        contact_type="LP",
        is_private=True,
        owner_id=user_a.id,
    )
    db.add(contact)
    db.commit()

    # Bob searches by Marcus's NAME — should miss (name is hidden).
    r1 = dispatch_tool_call("search_contacts", {"query": "Marcus"}, user_b, db)
    assert r1["count"] == 0

    # Bob searches by company_name (revealed) — should hit, as redacted.
    r2 = dispatch_tool_call("search_contacts", {"query": "Ironclad"}, user_b, db)
    assert r2["count"] == 1
    assert r2["results"][0]["is_redacted"] is True


def test_redacted_row_writes_audit_row_in_search(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Each redacted exposure writes an audit row tied to the contact."""
    user_a = user_factory(email="alice@test.fake")
    user_b = user_factory(email="bob@test.fake")
    contact = Contact(
        name="Secret",
        company_name="ADIA",
        primary_fund="Energy",
        is_private=True,
        owner_id=user_a.id,
    )
    db.add(contact)
    db.commit()

    dispatch_tool_call("search_contacts", {}, user_b, db)

    from sqlalchemy import select

    from app.models import AuditLog

    rows = list(
        db.scalars(
            select(AuditLog)
            .where(
                AuditLog.user_id == user_b.id,
                AuditLog.action == "redacted_reveal",
            )
            .order_by(AuditLog.id)
        )
    )
    assert len(rows) == 1
    assert rows[0].target_type == "contact"
    assert rows[0].target_id == contact.id


def test_update_contact_reveal_fields_whitelist_rejects_pii(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Pydantic Literal rejects values outside the whitelist. PII
    columns (notes, email, name) MUST NOT be settable in reveal_fields."""
    user = user_factory()
    contact = _make_contact(db, user, name="Mine", is_private=True)

    for forbidden in ("notes", "email", "name", "cell_phone", "image_url"):
        with pytest.raises(ToolDispatchError, match="Invalid params"):
            dispatch_tool_call(
                "update_contact",
                {"contact_id": contact.id, "reveal_fields": [forbidden]},
                user,
                db,
            )


def test_update_contact_reveal_fields_accepts_whitelisted_values(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Owner can set reveal_fields to any subset of the whitelist."""
    user = user_factory()
    contact = _make_contact(db, user, name="Mine", is_private=True)

    dispatch_tool_call(
        "update_contact",
        {
            "contact_id": contact.id,
            "reveal_fields": ["primary_fund", "country"],
        },
        user,
        db,
    )
    db.refresh(contact)
    assert sorted(contact.reveal_fields) == ["country", "primary_fund"]


def test_search_query_does_ilike_across_fields(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    _make_contact(
        db, user, name="Admiral Barrett", company_name="Mare Island Naval LLC"
    )
    _make_contact(db, user, name="Someone Else", company_name="Other Co")

    result = dispatch_tool_call("search_contacts", {"query": "mare island"}, user, db)
    assert result["count"] == 1
    assert result["results"][0]["name"] == "Admiral Barrett"


def test_search_filters_by_fund_and_type(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    _make_contact(db, user, primary_fund="Maritime", contact_type="Portfolio")
    _make_contact(db, user, primary_fund="Maritime", contact_type="LP")
    _make_contact(db, user, primary_fund="Energy", contact_type="Portfolio")

    result = dispatch_tool_call(
        "search_contacts",
        {"primary_fund": "Maritime", "contact_type": "Portfolio"},
        user,
        db,
    )
    assert result["count"] == 1


def test_create_records_owner_as_current_user(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    result = dispatch_tool_call(
        "create_contact",
        {
            "name": "New Contact",
            "primary_fund": "Energy",
            "fly_status": "Maybe Must Fly",
        },
        user,
        db,
    )
    created_id = result["created"]["id"]
    contact = db.get(Contact, created_id)
    assert contact is not None
    assert contact.owner_id == user.id
    assert contact.primary_fund == "Energy"
    assert contact.fly_status == "Maybe Must Fly"


def test_update_returns_not_found_when_invisible(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user_a = user_factory(email="alice@test.fake")
    user_b = user_factory(email="bob@test.fake")
    private = _make_contact(db, user_a, is_private=True)

    result = dispatch_tool_call(
        "update_contact",
        {"contact_id": private.id, "name": "hijacked"},
        user_b,
        db,
    )
    assert result["error"] == "not_found"
    db.refresh(private)
    assert private.name != "hijacked"


def test_update_returns_forbidden_when_visible_but_not_owned(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user_a = user_factory(email="alice@test.fake")
    user_b = user_factory(email="bob@test.fake")
    public = _make_contact(db, user_a, is_private=False)

    result = dispatch_tool_call(
        "update_contact",
        {"contact_id": public.id, "name": "hijacked"},
        user_b,
        db,
    )
    assert result["error"] == "forbidden"


def test_update_succeeds_for_owner(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    contact = _make_contact(db, user, name="Original")

    result = dispatch_tool_call(
        "update_contact",
        {"contact_id": contact.id, "name": "Renamed"},
        user,
        db,
    )
    assert "updated" in result
    db.refresh(contact)
    assert contact.name == "Renamed"


def test_pipeline_summary_groups_by_fund_and_type(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    _make_contact(db, user, primary_fund="Maritime", contact_type="Portfolio")
    _make_contact(db, user, primary_fund="Maritime", contact_type="Portfolio")
    _make_contact(db, user, primary_fund="Maritime", contact_type="LP")
    _make_contact(db, user, primary_fund="Energy", contact_type="Portfolio")

    result = dispatch_tool_call("get_pipeline_summary", {}, user, db)
    assert result["total"] == 4
    assert result["by_fund_and_type"]["Maritime"]["Portfolio"] == 2
    assert result["by_fund_and_type"]["Maritime"]["LP"] == 1
    assert result["by_fund_and_type"]["Energy"]["Portfolio"] == 1


def test_pipeline_summary_respects_privacy(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user_a = user_factory(email="alice@test.fake")
    user_b = user_factory(email="bob@test.fake")
    _make_contact(db, user_a, primary_fund="Maritime", is_private=True)
    _make_contact(db, user_a, primary_fund="Maritime", is_private=True)

    result = dispatch_tool_call("get_pipeline_summary", {}, user_b, db)
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# New filter dimensions: gender, country, lp_subtype
# ---------------------------------------------------------------------------


def test_search_filters_by_gender(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    _make_contact(db, user, name="Alice", gender="Female")
    _make_contact(db, user, name="Bob", gender="Male")
    _make_contact(db, user, name="Charlie", gender="Unknown")

    result = dispatch_tool_call("search_contacts", {"gender": "Female"}, user, db)
    assert result["count"] == 1
    assert result["results"][0]["name"] == "Alice"


def test_search_filters_by_country_case_insensitive(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    _make_contact(db, user, name="Maple", country="Canada")
    _make_contact(db, user, name="Eagle", country="United States")
    _make_contact(db, user, name="Falcon", country="Saudi Arabia")

    # Exact canonical name
    result = dispatch_tool_call("search_contacts", {"country": "Canada"}, user, db)
    assert {c["name"] for c in result["results"]} == {"Maple"}

    # Different casing — same match (Claude may vary case)
    result = dispatch_tool_call(
        "search_contacts", {"country": "saudi arabia"}, user, db
    )
    assert {c["name"] for c in result["results"]} == {"Falcon"}


def test_search_filters_by_lp_subtype(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    _make_contact(
        db, user, name="SWF1", contact_type="LP", lp_subtype="Sovereign Wealth Fund"
    )
    _make_contact(db, user, name="FO1", contact_type="LP", lp_subtype="Family Office")

    result = dispatch_tool_call(
        "search_contacts", {"lp_subtype": "Sovereign Wealth Fund"}, user, db
    )
    assert result["count"] == 1
    assert result["results"][0]["name"] == "SWF1"


def test_search_combines_country_and_contact_type(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """'show me all U.S. government contacts' should narrow to both."""
    user = user_factory()
    _make_contact(
        db, user, name="USGov", country="United States", contact_type="Government"
    )
    _make_contact(db, user, name="USOther", country="United States", contact_type="LP")
    _make_contact(
        db, user, name="SaudiGov", country="Saudi Arabia", contact_type="Government"
    )

    result = dispatch_tool_call(
        "search_contacts",
        {"country": "United States", "contact_type": "Government"},
        user,
        db,
    )
    assert {c["name"] for c in result["results"]} == {"USGov"}


def test_search_results_include_new_fields(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Frontend needs gender + country + lp_subtype on every result."""
    user = user_factory()
    _make_contact(
        db,
        user,
        name="Person",
        gender="Female",
        country="Canada",
        contact_type="LP",
        lp_subtype="Pension",
    )
    result = dispatch_tool_call("search_contacts", {}, user, db)
    row = result["results"][0]
    assert row["gender"] == "Female"
    assert row["country"] == "Canada"
    assert row["lp_subtype"] == "Pension"


def test_search_filters_by_fly_status(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    must = _make_contact(db, user, name="Must")
    must.fly_status = "Must Fly"
    fly = _make_contact(db, user, name="Fly")
    fly.fly_status = "Fly List"
    _make_contact(db, user, name="Unsure")  # default Unknown
    db.commit()

    result = dispatch_tool_call("search_contacts", {"fly_status": "Must Fly"}, user, db)
    assert result["count"] == 1
    assert result["results"][0]["name"] == "Must"


def test_search_results_include_fly_status(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    _make_contact(db, user, name="Default")
    result = dispatch_tool_call("search_contacts", {}, user, db)
    assert result["results"][0]["fly_status"] == "Unknown"


# ---------------------------------------------------------------------------
# Off Fly List — owner-only update + result ordering
# ---------------------------------------------------------------------------


def test_search_orders_by_fly_status_priority(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Must Fly > Fly List > Maybe Must Fly > Unknown > Off Fly List."""
    user = user_factory()
    # Insert in shuffled order to prove sort isn't insertion-order.
    off = _make_contact(db, user, name="Z-off")
    off.fly_status = "Off Fly List"
    fly = _make_contact(db, user, name="A-fly")
    fly.fly_status = "Fly List"
    must = _make_contact(db, user, name="M-must")
    must.fly_status = "Must Fly"
    _make_contact(db, user, name="N-unsure")  # default Unknown
    db.commit()

    result = dispatch_tool_call("search_contacts", {}, user, db)
    names = [r["name"] for r in result["results"]]
    assert names == ["M-must", "A-fly", "N-unsure", "Z-off"]


def test_off_fly_list_appears_last_but_still_visible(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Off Fly List contacts are not hidden — just last."""
    user = user_factory()
    off = _make_contact(db, user, name="Removed")
    off.fly_status = "Off Fly List"
    db.commit()

    result = dispatch_tool_call("search_contacts", {}, user, db)
    assert any(r["name"] == "Removed" for r in result["results"])


def test_take_off_fly_list_blocked_for_non_owner(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Only the owner can change a contact to Off Fly List."""
    user_a = user_factory(email="alice@test.fake")
    user_b = user_factory(email="bob@test.fake")
    contact = _make_contact(db, user_a, name="Marcus", is_private=False)

    result = dispatch_tool_call(
        "update_contact",
        {"contact_id": contact.id, "fly_status": "Off Fly List"},
        user_b,
        db,
    )
    assert result["error"] == "forbidden_owner_only"
    db.refresh(contact)
    assert contact.fly_status != "Off Fly List"


def test_take_off_fly_list_succeeds_for_owner(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    must = _make_contact(db, user, name="Marcus")
    must.fly_status = "Must Fly"
    db.commit()

    result = dispatch_tool_call(
        "update_contact",
        {"contact_id": must.id, "fly_status": "Off Fly List"},
        user,
        db,
    )
    assert "updated" in result
    db.refresh(must)
    assert must.fly_status == "Off Fly List"


# ---------------------------------------------------------------------------
# Patina overrides — owner-only edits + result shape
# ---------------------------------------------------------------------------


def test_patina_overrides_blocked_for_non_owner(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user_a = user_factory(email="alice@test.fake")
    user_b = user_factory(email="bob@test.fake")
    contact = _make_contact(db, user_a, name="Marcus", is_private=False)

    result = dispatch_tool_call(
        "update_contact",
        {
            "contact_id": contact.id,
            "patina_overrides": [{"kind": "sticker", "shape": "smiley"}],
        },
        user_b,
        db,
    )
    assert result["error"] == "forbidden_owner_only"
    assert "patina" in result["message"].lower()
    db.refresh(contact)
    assert contact.patina_overrides is None


def test_patina_overrides_succeed_for_owner(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    contact = _make_contact(db, user, name="Marcus")

    result = dispatch_tool_call(
        "update_contact",
        {
            "contact_id": contact.id,
            "patina_overrides": [
                {"kind": "pencilNote", "text": "Ghostbusters"},
                {"kind": "pencilNote", "text": "ha ha ha"},
            ],
        },
        user,
        db,
    )
    assert "updated" in result
    db.refresh(contact)
    assert contact.patina_overrides is not None
    assert len(contact.patina_overrides) == 2
    assert contact.patina_overrides[0]["text"] == "Ghostbusters"


def test_patina_overrides_clearable_with_empty_list(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """[] means 'show no patina' — distinct from null which means auto-pick."""
    user = user_factory()
    contact = _make_contact(db, user, name="Marcus")
    contact.patina_overrides = [{"kind": "sticker", "shape": "smiley"}]
    db.commit()

    result = dispatch_tool_call(
        "update_contact",
        {"contact_id": contact.id, "patina_overrides": []},
        user,
        db,
    )
    assert "updated" in result
    db.refresh(contact)
    assert contact.patina_overrides == []


def test_patina_overrides_resettable_to_null(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Explicit null restores auto-pick behavior."""
    user = user_factory()
    contact = _make_contact(db, user, name="Marcus")
    contact.patina_overrides = [{"kind": "sticker", "shape": "smiley"}]
    db.commit()

    result = dispatch_tool_call(
        "update_contact",
        {"contact_id": contact.id, "patina_overrides": None},
        user,
        db,
    )
    assert "updated" in result
    db.refresh(contact)
    assert contact.patina_overrides is None


def test_patina_overrides_capped_at_max(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    contact = _make_contact(db, user, name="Marcus")
    too_many = [{"kind": "sticker"} for _ in range(5)]

    with pytest.raises(ToolDispatchError, match="Invalid params"):
        dispatch_tool_call(
            "update_contact",
            {"contact_id": contact.id, "patina_overrides": too_many},
            user,
            db,
        )


def test_search_results_include_patina_overrides(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    contact = _make_contact(db, user, name="Marcus")
    contact.patina_overrides = [{"kind": "sticker", "shape": "star"}]
    db.commit()

    result = dispatch_tool_call("search_contacts", {}, user, db)
    row = next(r for r in result["results"] if r["name"] == "Marcus")
    assert row["patina_overrides"] == [{"kind": "sticker", "shape": "star"}]


# ---------------------------------------------------------------------------
# Audit log rows for tool-dispatched writes (Day 6 Slice 8 regression)
# ---------------------------------------------------------------------------
#
# The @audit_log decorator only runs on HTTP endpoint calls. When Claude
# reaches into the DB via tool dispatch (create/update/request_change/
# resolve_change_request) those writes used to bypass the audit trail —
# the audit log recorded the chat_request but not what the chat actually
# did. Fixed by adding explicit write_audit_row() calls to the handlers.
# These tests pin that coverage so the gap can't silently return.


def _audit_rows_for(db: Session, user: User, action: str) -> list:
    from sqlalchemy import select

    from app.models import AuditLog

    return list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.user_id == user.id, AuditLog.action == action)
            .order_by(AuditLog.id)
        )
    )


def test_tool_create_contact_writes_audit_row(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    result = dispatch_tool_call(
        "create_contact",
        {
            "name": "Audit Me",
            "primary_fund": "Energy",
            "fly_status": "Maybe Must Fly",
        },
        user,
        db,
    )
    created_id = result["created"]["id"]

    rows = _audit_rows_for(db, user, "create_contact")
    assert len(rows) == 1
    assert rows[0].target_type == "contact"
    assert rows[0].target_id == created_id
    assert rows[0].payload_hash is not None


def test_tool_update_contact_writes_audit_row(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    contact = _make_contact(db, user, name="Original")

    dispatch_tool_call(
        "update_contact",
        {"contact_id": contact.id, "name": "Renamed via chat"},
        user,
        db,
    )

    rows = _audit_rows_for(db, user, "update_contact")
    assert len(rows) == 1
    assert rows[0].target_type == "contact"
    assert rows[0].target_id == contact.id
    assert rows[0].payload_hash is not None


def test_tool_update_failure_writes_no_audit_row(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """If the update is refused (forbidden / not_found), nothing changed
    in the DB, so there's nothing to audit — confirm no row is emitted."""
    owner = user_factory(email="owner@test.fake")
    other = user_factory(email="other@test.fake")
    contact = _make_contact(db, owner, is_private=False)

    result = dispatch_tool_call(
        "update_contact",
        {"contact_id": contact.id, "name": "hijacked"},
        other,
        db,
    )
    assert result["error"] == "forbidden"

    rows = _audit_rows_for(db, other, "update_contact")
    assert rows == []


# ---------------------------------------------------------------------------
# delete_contact tool (Day 6 polish slice)
# ---------------------------------------------------------------------------


def test_tool_delete_soft_deletes_owned_contact(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Delete sets deleted_at but keeps the row; the contact disappears
    from subsequent search results."""
    user = user_factory()
    contact = _make_contact(db, user, name="Doomed")

    result = dispatch_tool_call(
        "delete_contact",
        {"contact_id": contact.id},
        user,
        db,
    )
    assert result == {"deleted": {"id": contact.id, "name": "Doomed"}}

    db.refresh(contact)
    assert contact.deleted_at is not None

    # Not in subsequent search results (visible_contacts_query filters
    # deleted_at IS NOT NULL).
    search = dispatch_tool_call("search_contacts", {"query": "Doomed"}, user, db)
    assert search["count"] == 0


def test_tool_delete_returns_not_found_when_invisible(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Someone else's private contact: 404, NOT 403 — don't reveal existence."""
    user_a = user_factory(email="alice@test.fake")
    user_b = user_factory(email="bob@test.fake")
    private = _make_contact(db, user_a, is_private=True)

    result = dispatch_tool_call(
        "delete_contact", {"contact_id": private.id}, user_b, db
    )
    assert result["error"] == "not_found"

    db.refresh(private)
    assert private.deleted_at is None


def test_tool_delete_returns_forbidden_when_visible_but_not_owned(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Public contact owned by someone else: 403."""
    user_a = user_factory(email="alice@test.fake")
    user_b = user_factory(email="bob@test.fake")
    public = _make_contact(db, user_a, is_private=False)

    result = dispatch_tool_call("delete_contact", {"contact_id": public.id}, user_b, db)
    assert result["error"] == "forbidden"

    db.refresh(public)
    assert public.deleted_at is None


def test_tool_delete_writes_audit_row(
    db: Session, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    contact = _make_contact(db, user, name="Audit Delete Me")

    dispatch_tool_call("delete_contact", {"contact_id": contact.id}, user, db)

    rows = _audit_rows_for(db, user, "delete_contact")
    assert len(rows) == 1
    assert rows[0].target_type == "contact"
    assert rows[0].target_id == contact.id
    assert rows[0].payload_hash is not None


def test_tool_delete_failure_writes_no_audit_row(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Refused deletes (forbidden/not_found) must not write audit rows."""
    owner = user_factory(email="owner@test.fake")
    other = user_factory(email="other@test.fake")
    contact = _make_contact(db, owner, is_private=False)

    result = dispatch_tool_call("delete_contact", {"contact_id": contact.id}, other, db)
    assert result["error"] == "forbidden"

    rows = _audit_rows_for(db, other, "delete_contact")
    assert rows == []


# ---------------------------------------------------------------------------
# Phase 2 Slice 5 — create_google_task
#
# The Google API call itself is mocked at the service entry point
# (`create_talk_to_task`). These tests pin the dispatcher's behaviour:
# visibility / ownership rules, token presence, error mapping, audit.
# The Google API surface itself is covered by test_google_tasks.py.
# ---------------------------------------------------------------------------


def _seed_token(user: User, db: Session) -> User:
    """Give the user a Google access token so the no_google_token early
    return doesn't fire. Fernet round-trips via the EncryptedString
    TypeDecorator so a plain string is fine here."""
    user.google_access_token = "fake-access-token"
    db.commit()
    db.refresh(user)
    return user


def test_create_google_task_not_visible_returns_not_found(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Private contact owned by Alice — Bob can't even see it, so the
    request should be refused with not_found (NOT self_owned, NOT
    forbidden — those would leak existence)."""
    from unittest.mock import patch

    alice = user_factory(email="alice@test.fake")
    bob = _seed_token(user_factory(email="bob@test.fake"), db)
    private = _make_contact(db, alice, name="Hidden", is_private=True)

    with patch("app.services.tool_dispatch.create_talk_to_task") as svc:
        result = dispatch_tool_call(
            "create_google_task", {"contact_id": private.id}, bob, db
        )

    assert result["error"] == "not_found"
    svc.assert_not_called()


def test_create_google_task_self_owned_refuses(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Reminding yourself to talk to yourself makes no sense — refuse."""
    from unittest.mock import patch

    alice = _seed_token(user_factory(email="alice@test.fake"), db)
    mine = _make_contact(db, alice, name="My Contact")

    with patch("app.services.tool_dispatch.create_talk_to_task") as svc:
        result = dispatch_tool_call(
            "create_google_task", {"contact_id": mine.id}, alice, db
        )

    assert result["error"] == "self_owned"
    svc.assert_not_called()


def test_create_google_task_missing_token_refuses(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """If the calling user never granted Google access (or the column is
    empty), we can't call the API — fail with a clear remedy."""
    from unittest.mock import patch

    alice = user_factory(email="alice@test.fake")
    bob = user_factory(email="bob@test.fake")  # NOT seeded
    public = _make_contact(db, alice, is_private=False, name="Marcus")

    with patch("app.services.tool_dispatch.create_talk_to_task") as svc:
        result = dispatch_tool_call(
            "create_google_task", {"contact_id": public.id}, bob, db
        )

    assert result["error"] == "no_google_token"
    svc.assert_not_called()


def test_create_google_task_happy_path_uses_owner_name_from_db(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Owner name MUST come from the User row, not the LLM. The task
    title and list-name both derive from owner.name."""
    from unittest.mock import patch

    from app.services.google_tasks import CreatedTask

    alice = user_factory(email="alice@test.fake", name="Alex Rivera")
    bob = _seed_token(user_factory(email="bob@test.fake"), db)
    contact = _make_contact(db, alice, name="Marcus", is_private=False)

    fake_created = CreatedTask(
        task_id="task-xyz",
        task_list_id="list-abc",
        task_list_title="DIN: Talk to Alex Rivera",
        self_link="https://t/task-xyz",
    )

    with patch(
        "app.services.tool_dispatch.create_talk_to_task", return_value=fake_created
    ) as svc:
        result = dispatch_tool_call(
            "create_google_task",
            {"contact_id": contact.id, "note": "Ask about Maritime deck"},
            bob,
            db,
        )

    assert result["task_id"] == "task-xyz"
    assert result["task_list_title"] == "DIN: Talk to Alex Rivera"
    assert result["owner_name"] == "Alex Rivera"
    assert result["contact_name"] == "Marcus"

    svc.assert_called_once()
    kwargs = svc.call_args.kwargs
    assert kwargs["owner_name"] == "Alex Rivera"
    assert kwargs["task_title"] == "Talk to Alex Rivera about Marcus"
    assert kwargs["notes"] == "Ask about Maritime deck"
    assert kwargs["access_token"] == "fake-access-token"


def test_create_google_task_maps_scope_error_to_dispatch_error(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Service-level GoogleTasksScopeError -> handler returns a structured
    dict with error=google_tasks_scope (NOT a bare exception)."""
    from unittest.mock import patch

    from app.services.google_tasks import GoogleTasksScopeError

    alice = user_factory(email="alice@test.fake", name="Alex Rivera")
    bob = _seed_token(user_factory(email="bob@test.fake"), db)
    contact = _make_contact(db, alice, name="Marcus", is_private=False)

    with patch(
        "app.services.tool_dispatch.create_talk_to_task",
        side_effect=GoogleTasksScopeError("revoked"),
    ):
        result = dispatch_tool_call(
            "create_google_task", {"contact_id": contact.id}, bob, db
        )

    assert result["error"] == "google_tasks_scope"
    assert "sign out" in result["message"].lower()


def test_create_google_task_writes_audit_row(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Successful task creation writes an audit row tied to the contact."""
    from unittest.mock import patch

    from app.services.google_tasks import CreatedTask

    alice = user_factory(email="alice@test.fake", name="Alex Rivera")
    bob = _seed_token(user_factory(email="bob@test.fake"), db)
    contact = _make_contact(db, alice, name="Marcus", is_private=False)

    with patch(
        "app.services.tool_dispatch.create_talk_to_task",
        return_value=CreatedTask(
            task_id="t",
            task_list_id="L",
            task_list_title="DIN: Talk to Alex Rivera",
            self_link=None,
        ),
    ):
        dispatch_tool_call("create_google_task", {"contact_id": contact.id}, bob, db)

    rows = _audit_rows_for(db, bob, "create_google_task")
    assert len(rows) == 1
    assert rows[0].target_type == "contact"
    assert rows[0].target_id == contact.id


def test_create_google_task_failure_writes_no_audit_row(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Scope errors are user-resolvable — no audit row, so the user can
    retry cleanly after re-granting without polluting the log."""
    from unittest.mock import patch

    from app.services.google_tasks import GoogleTasksScopeError

    alice = user_factory(email="alice@test.fake", name="Alex Rivera")
    bob = _seed_token(user_factory(email="bob@test.fake"), db)
    contact = _make_contact(db, alice, name="Marcus", is_private=False)

    with patch(
        "app.services.tool_dispatch.create_talk_to_task",
        side_effect=GoogleTasksScopeError("nope"),
    ):
        dispatch_tool_call("create_google_task", {"contact_id": contact.id}, bob, db)

    rows = _audit_rows_for(db, bob, "create_google_task")
    assert rows == []


# ---------------------------------------------------------------------------
# Phase 2 Slice 5 follow-up — owner_name / is_self_owned in result rows
#
# Goddess needs to know the owner BEFORE offering a Tasks reminder. Bug
# observed in prod: she offered "Talk to Marcus" (the contact) instead
# of "Talk to <Owner>" because the result rows had no owner info.
# ---------------------------------------------------------------------------


def test_search_results_include_owner_name_and_self_owned_flag(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Both fields must be present on every result row, and is_self_owned
    must reflect the caller's relationship to each contact independently."""
    alice = user_factory(email="alice@test.fake", name="Alex Rivera")
    bob = user_factory(email="bob@test.fake", name="Sam Chen")
    _make_contact(db, alice, name="Alice Contact", is_private=False)
    _make_contact(db, bob, name="Bob Contact", is_private=False)

    result = dispatch_tool_call("search_contacts", {}, alice, db)
    by_name = {r["name"]: r for r in result["results"]}

    assert by_name["Alice Contact"]["owner_name"] == "Alex Rivera"
    assert by_name["Alice Contact"]["is_self_owned"] is True

    assert by_name["Bob Contact"]["owner_name"] == "Sam Chen"
    assert by_name["Bob Contact"]["is_self_owned"] is False


def test_create_result_marks_caller_as_self_owner(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """create_contact's result row should always show is_self_owned=True
    (the caller just created it, so they own it)."""
    alice = user_factory(email="alice@test.fake", name="Alex Rivera")
    result = dispatch_tool_call(
        "create_contact",
        {"name": "New Person", "fly_status": "Must Fly"},
        alice,
        db,
    )
    assert result["created"]["owner_name"] == "Alex Rivera"
    assert result["created"]["is_self_owned"] is True


def test_initials_helper_matches_din_team() -> None:
    """AR / SC / JB — the three demo teammates. Each is a First-Last name,
    so the heuristic alone would yield a single letter; the explicit
    overrides give the fuller two-letter badge."""
    from app.services.tool_dispatch import _initials_for

    assert _initials_for("Alex Rivera") == "AR"
    assert _initials_for("Sam Chen") == "SC"
    assert _initials_for("Jordan Blake") == "JB"
    assert _initials_for(None) is None
    assert _initials_for("") is None
    # Single-word non-team name still gets a sensible single-letter badge.
    assert _initials_for("Madonna") == "M"


def test_search_results_include_owner_initials(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Every result row carries owner_initials alongside owner_name so
    the frontend can render the corner badge without re-deriving."""
    alice = user_factory(email="alice@test.fake", name="Alex Rivera")
    bob = user_factory(email="bob@test.fake", name="Jordan Blake")
    _make_contact(db, alice, name="Alice Contact", is_private=False)
    _make_contact(db, bob, name="Bob Contact", is_private=False)

    result = dispatch_tool_call("search_contacts", {}, alice, db)
    by_name = {r["name"]: r for r in result["results"]}

    assert by_name["Alice Contact"]["owner_initials"] == "AR"
    assert by_name["Bob Contact"]["owner_initials"] == "JB"


# ---------------------------------------------------------------------------
# Phase 2 Slice 6.6 — transfer_contact
# Owner OR admin can move a contact to another teammate. Admin can also
# transfer a contact they couldn't otherwise see (private + not theirs)
# — the use case is "teammate leaves, redistribute their contacts."
# ---------------------------------------------------------------------------


def test_transfer_unknown_teammate_refuses(
    db: Session, user_factory: Callable[..., User]
) -> None:
    owner = user_factory(email="alice@test.fake")
    contact = _make_contact(db, owner, name="Mine")

    result = dispatch_tool_call(
        "transfer_contact",
        {"contact_id": contact.id, "new_owner_email": "stranger@nowhere.fake"},
        owner,
        db,
    )
    assert result["error"] == "unknown_teammate"
    db.refresh(contact)
    assert contact.owner_id == owner.id  # untouched


def test_transfer_to_current_owner_is_noop_with_clear_error(
    db: Session, user_factory: Callable[..., User]
) -> None:
    owner = user_factory(email="alice@test.fake")
    contact = _make_contact(db, owner, name="Mine")

    result = dispatch_tool_call(
        "transfer_contact",
        {"contact_id": contact.id, "new_owner_email": owner.email},
        owner,
        db,
    )
    assert result["error"] == "already_owner"


def test_owner_can_transfer_own_contact(
    db: Session, user_factory: Callable[..., User]
) -> None:
    owner = user_factory(email="alice@test.fake")
    new_owner = user_factory(email="bob@test.fake", name="Bob")
    contact = _make_contact(db, owner, name="Marcus")

    result = dispatch_tool_call(
        "transfer_contact",
        {"contact_id": contact.id, "new_owner_email": new_owner.email},
        owner,
        db,
    )
    assert "transferred" in result
    assert result["transferred"]["new_owner_id"] == new_owner.id
    assert result["transferred"]["by_admin"] is False
    db.refresh(contact)
    assert contact.owner_id == new_owner.id


def test_non_owner_non_admin_cannot_transfer(
    db: Session, user_factory: Callable[..., User]
) -> None:
    owner = user_factory(email="alice@test.fake")
    other = user_factory(email="bob@test.fake", name="Bob")
    third = user_factory(email="carol@test.fake", name="Carol")
    contact = _make_contact(db, owner, name="Marcus", is_private=False)

    result = dispatch_tool_call(
        "transfer_contact",
        {"contact_id": contact.id, "new_owner_email": third.email},
        other,
        db,
    )
    assert result["error"] == "forbidden"
    db.refresh(contact)
    assert contact.owner_id == owner.id


def test_admin_can_transfer_any_contact_including_private(
    db: Session, user_factory: Callable[..., User]
) -> None:
    """Admin bypass: a private contact owned by Alice can be moved by
    the admin to Carol even though the admin can't otherwise see it.
    Use case: Alice leaves the team, admin reassigns her contacts."""
    owner = user_factory(email="alice@test.fake")
    admin = user_factory(email="admin@test.fake", name="Admin")
    admin.role = "admin"
    db.commit()
    new_owner = user_factory(email="carol@test.fake", name="Carol")
    contact = _make_contact(db, owner, name="Secret", is_private=True)

    result = dispatch_tool_call(
        "transfer_contact",
        {"contact_id": contact.id, "new_owner_email": new_owner.email},
        admin,
        db,
    )
    assert "transferred" in result
    assert result["transferred"]["by_admin"] is True
    db.refresh(contact)
    assert contact.owner_id == new_owner.id


def test_transfer_writes_audit_row(
    db: Session, user_factory: Callable[..., User]
) -> None:
    owner = user_factory(email="alice@test.fake")
    new_owner = user_factory(email="bob@test.fake", name="Bob")
    contact = _make_contact(db, owner, name="Marcus")

    dispatch_tool_call(
        "transfer_contact",
        {"contact_id": contact.id, "new_owner_email": new_owner.email},
        owner,
        db,
    )

    rows = _audit_rows_for(db, owner, "transfer_contact")
    assert len(rows) == 1
    assert rows[0].target_type == "contact"
    assert rows[0].target_id == contact.id


def test_transfer_missing_contact_returns_not_found(
    db: Session, user_factory: Callable[..., User]
) -> None:
    owner = user_factory(email="alice@test.fake")
    new_owner = user_factory(email="bob@test.fake")
    result = dispatch_tool_call(
        "transfer_contact",
        {"contact_id": 999_999, "new_owner_email": new_owner.email},
        owner,
        db,
    )
    assert result["error"] == "not_found"
