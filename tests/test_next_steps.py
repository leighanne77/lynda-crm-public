"""Tests for the next-steps activity log (Phase 2 Slice 6.10)."""

from typing import Any, Callable
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.models import Contact, NextStep, User
from app.security import create_access_token
from app.services.tool_dispatch import dispatch_tool_call


def _auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id=user.id)}"}


def _make_contact(db, owner: User, **overrides: Any) -> Contact:
    payload: dict[str, Any] = {
        "name": "Marcus",
        "owner_id": owner.id,
        "primary_fund": "Maritime",
        "fly_status": "Must Fly",
    }
    payload.update(overrides)
    contact = Contact(**payload)
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def test_create_next_step_persists_row_with_independent_owner(
    db, user_factory: Callable[..., User]
) -> None:
    """Step owner is independent of contact owner — the whole point."""
    leigh_anne = user_factory(email="alice@test.fake", name="Alex Rivera")
    heather_jo = user_factory(email="bob@test.fake", name="Jordan Blake")
    contact = _make_contact(db, leigh_anne, name="Marcus")  # owned by LA

    with patch("app.services.tool_dispatch.create_next_step_task") as mock_tasks:
        mock_tasks.return_value = type(
            "FakeTask",
            (),
            {
                "task_id": "t1",
                "task_list_id": "list-abc",
                "task_list_title": "DIN: Next Steps",
                "self_link": None,
            },
        )
        # HJ has a token so the Tasks mirror gets attempted.
        heather_jo.google_access_token = "fake-token"
        db.commit()

        result = dispatch_tool_call(
            "create_next_step",
            {
                "contact_id": contact.id,
                "title": "call about Maritime deck",
                "owner_email": heather_jo.email,
            },
            leigh_anne,
            db,
        )

    assert result["next_step"]["owner_id"] == heather_jo.id
    assert result["next_step"]["owner_name"] == "Jordan Blake"
    assert result["next_step"]["title"] == "call about Maritime deck"
    # Tasks mirror was attempted on HJ's account, not LA's.
    call_kwargs = mock_tasks.call_args.kwargs
    assert call_kwargs["access_token"] == "fake-token"
    assert call_kwargs["contact_name"] == "Marcus"


def test_create_next_step_unknown_owner_refused(
    db, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    contact = _make_contact(db, user, name="Marcus")
    result = dispatch_tool_call(
        "create_next_step",
        {
            "contact_id": contact.id,
            "title": "do something",
            "owner_email": "stranger@nowhere.fake",
        },
        user,
        db,
    )
    assert result["error"] == "unknown_teammate"


def test_create_next_step_invisible_contact_returns_not_found(
    db, user_factory: Callable[..., User]
) -> None:
    """Caller can't add a step to a contact they can't see (private,
    owner has hints off)."""
    alice = user_factory(email="alice@test.fake")
    alice.allow_existence_hints = False
    db.commit()
    bob = user_factory(email="bob@test.fake")
    contact = _make_contact(db, alice, name="Secret", is_private=True)

    result = dispatch_tool_call(
        "create_next_step",
        {
            "contact_id": contact.id,
            "title": "thing",
            "owner_email": bob.email,
        },
        bob,
        db,
    )
    assert result["error"] == "not_found"


def test_create_next_step_records_warning_when_owner_has_no_google_token(
    db, user_factory: Callable[..., User]
) -> None:
    """Owner hasn't signed in with Google yet → in-app row still saved
    but the tool result includes a warning string. Voice-first: DESS
    can read this back to the user."""
    user = user_factory(email="alice@test.fake")
    teammate = user_factory(email="bob@test.fake", name="Bob")
    # teammate has NO google_access_token
    contact = _make_contact(db, user, name="Marcus")

    result = dispatch_tool_call(
        "create_next_step",
        {
            "contact_id": contact.id,
            "title": "do something",
            "owner_email": teammate.email,
        },
        user,
        db,
    )
    assert "next_step" in result
    assert result["warning"]
    assert "hasn't signed in with Google" in result["warning"]


def test_complete_next_step_owner_can_complete(
    db, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    contact = _make_contact(db, user, name="Marcus")
    step = NextStep(
        contact_id=contact.id,
        owner_id=user.id,
        created_by_id=user.id,
        title="thing",
    )
    db.add(step)
    db.commit()
    db.refresh(step)

    result = dispatch_tool_call(
        "complete_next_step",
        {"next_step_id": step.id},
        user,
        db,
    )
    assert "completed" in result
    db.refresh(step)
    assert step.done is True
    assert step.done_at is not None


def test_complete_next_step_non_owner_non_contact_owner_refused(
    db, user_factory: Callable[..., User]
) -> None:
    """A teammate who is neither the step's owner NOR the contact's
    owner can't complete it — even if they can see the contact."""
    alice = user_factory(email="alice@test.fake")
    bob = user_factory(email="bob@test.fake")
    carol = user_factory(email="carol@test.fake")
    contact = _make_contact(db, alice, name="Marcus", is_private=False)
    step = NextStep(
        contact_id=contact.id,
        owner_id=bob.id,  # step owned by Bob
        created_by_id=alice.id,
        title="thing",
    )
    db.add(step)
    db.commit()
    db.refresh(step)

    result = dispatch_tool_call(
        "complete_next_step",
        {"next_step_id": step.id},
        carol,
        db,
    )
    assert result["error"] == "forbidden"


def test_complete_next_step_contact_owner_can_complete_teammate_step(
    db, user_factory: Callable[..., User]
) -> None:
    """Contact owner can complete a step even if it belongs to a teammate."""
    alice = user_factory(email="alice@test.fake")
    bob = user_factory(email="bob@test.fake")
    contact = _make_contact(db, alice, name="Marcus")
    step = NextStep(
        contact_id=contact.id,
        owner_id=bob.id,
        created_by_id=alice.id,
        title="thing",
    )
    db.add(step)
    db.commit()

    result = dispatch_tool_call(
        "complete_next_step",
        {"next_step_id": step.id},
        alice,
        db,
    )
    assert "completed" in result


def test_list_next_steps_rest_endpoint_returns_open_first(
    client: TestClient, db, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    contact = _make_contact(db, user, name="Marcus")
    s_done = NextStep(
        contact_id=contact.id,
        owner_id=user.id,
        created_by_id=user.id,
        title="done one",
        done=True,
    )
    s_open = NextStep(
        contact_id=contact.id,
        owner_id=user.id,
        created_by_id=user.id,
        title="open one",
    )
    db.add_all([s_done, s_open])
    db.commit()

    resp = client.get(
        f"/api/contacts/{contact.id}/next-steps", headers=_auth_headers(user)
    )
    assert resp.status_code == 200
    body = resp.json()
    # Open first.
    assert [s["title"] for s in body][0] == "open one"
    # Google Tasks URL helper kicks in when google_task_list_id is set.
    # (Both rows here have no list_id, so URL is null.)
    assert body[0]["google_task_url"] is None


def test_list_next_steps_404_when_contact_not_visible(
    client: TestClient, db, user_factory: Callable[..., User]
) -> None:
    """Redacted-view callers don't see next-steps at all — could leak
    intent. Endpoint returns 404 to mirror the visibility model."""
    alice = user_factory(email="alice@test.fake")
    bob = user_factory(email="bob@test.fake")
    contact = _make_contact(db, alice, name="Secret", is_private=True)

    resp = client.get(
        f"/api/contacts/{contact.id}/next-steps", headers=_auth_headers(bob)
    )
    # Bob sees the contact in redacted form via search, but next-steps
    # endpoint is full-view-only.
    assert resp.status_code == 404


def test_patch_next_step_marks_done_via_rest(
    client: TestClient, db, user_factory: Callable[..., User]
) -> None:
    user = user_factory()
    contact = _make_contact(db, user, name="Marcus")
    step = NextStep(
        contact_id=contact.id,
        owner_id=user.id,
        created_by_id=user.id,
        title="thing",
    )
    db.add(step)
    db.commit()
    db.refresh(step)

    resp = client.patch(
        f"/api/next-steps/{step.id}",
        headers=_auth_headers(user),
        json={"done": True},
    )
    assert resp.status_code == 200
    assert resp.json()["done"] is True
    db.refresh(step)
    assert step.done is True
