import contextlib
import json
from base64 import b64encode
from unittest.mock import patch

import itsdangerous
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

from api.users import add_package, ensure_user
import webapp.config as config_mod
from webapp.deps import NotAuthenticated

# NOTE: Starlette's TestClient (httpx-based) has no `session_transaction()`
# method -- that API is a Flask test-client feature, not a Starlette/FastAPI
# one. To keep the test bodies exactly as specified (interacting with a
# `session_transaction()` context manager), we attach a real, equivalent
# implementation here: it signs a session cookie the same way
# `starlette.middleware.sessions.SessionMiddleware` does (itsdangerous
# `TimestampSigner` over a base64-encoded JSON payload) and sets it on the
# client's cookie jar. This exercises the real signed-cookie mechanism the
# app uses in production -- it is not a mock of authentication. See
# tests/test_webapp_main.py for the identical pattern used elsewhere in this
# repo.


def _install_session_transaction(client, secret_key):
    @contextlib.contextmanager
    def session_transaction():
        session: dict = {}
        yield session
        signer = itsdangerous.TimestampSigner(secret_key)
        data = signer.sign(b64encode(json.dumps(session).encode("utf-8")))
        client.cookies.set("session", data.decode("utf-8"))

    client.session_transaction = session_transaction


def _client(tmp_path, monkeypatch):
    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "users")

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.include_router(config_mod.router)

    @app.exception_handler(NotAuthenticated)
    async def _handle_not_authenticated(request: Request, exc: NotAuthenticated):
        return RedirectResponse(url="/login", status_code=302)

    client = TestClient(app)
    _install_session_transaction(client, "test-secret")
    return client


def _logged_in(client, user_id):
    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = user_id
        yield c


def test_get_config_requires_auth(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/config", follow_redirects=False)
    assert response.status_code in (302, 307)


def test_get_config_returns_defaults_for_new_user(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/config")

    assert response.status_code == 200
    data = response.json()
    assert data["packages"] == {"collection": [], "sale": [], "wants": [], "decks": []}
    assert data["formats"] == []
    assert data["pick_list_sort"] == "colour"
    assert data["is_admin"] is False


def test_add_and_list_package(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        add_response = c.post(
            "/api/config/packages",
            json={"section": "collection", "color_group": "Red", "public_id": "abc123"},
        )
        get_response = c.get("/api/config")

    assert add_response.status_code == 200
    collection = get_response.json()["packages"]["collection"]
    assert len(collection) == 1
    assert collection[0]["color_group"] == "Red"
    assert collection[0]["public_id"] == "abc123"


def test_add_package_extracts_slug_from_full_url(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        add_response = c.post(
            "/api/config/packages",
            json={"section": "collection", "color_group": "Red", "public_id": "https://www.moxfield.com/decks/abc123"},
        )
        get_response = c.get("/api/config")

    assert add_response.status_code == 200
    collection = get_response.json()["packages"]["collection"]
    assert len(collection) == 1
    assert collection[0]["color_group"] == "Red"
    assert collection[0]["public_id"] == "abc123"


def test_add_sale_package(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post(
            "/api/config/packages",
            json={"section": "sale", "color_group": "Binder A", "public_id": "s1"},
        )
        get_response = c.get("/api/config")

    assert response.status_code == 200
    sale = get_response.json()["packages"]["sale"]
    assert sale[0]["color_group"] == "Binder A"


def test_remove_package(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        add_response = c.post(
            "/api/config/packages",
            json={"section": "collection", "color_group": "Red", "public_id": "abc123"},
        )
        package_id = add_response.json()["id"]
        remove_response = c.delete(f"/api/config/packages/{package_id}")
        get_response = c.get("/api/config")

    assert remove_response.status_code == 200
    assert get_response.json()["packages"]["collection"] == []


def test_set_sort_valid(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post("/api/config/sort", json={"sort_mode": "cmc"})
        get_response = c.get("/api/config")

    assert response.status_code == 200
    assert get_response.json()["pick_list_sort"] == "cmc"


def test_set_sort_invalid_returns_400(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post("/api/config/sort", json={"sort_mode": "not-a-real-mode"})

    assert response.status_code == 400


def test_set_formats(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post("/api/config/formats", json={"formats": ["modern", "legacy"]})
        get_response = c.get("/api/config")

    assert response.status_code == 200
    assert get_response.json()["formats"] == ["modern", "legacy"]


def test_set_profile(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post("/api/config/profile", json={"display_name": "Alice", "icon": "🐉"})
        get_response = c.get("/api/config")

    assert response.status_code == 200
    data = get_response.json()
    assert data["display_name"] == "Alice"
    assert data["icon"] == "🐉"


def test_sync_calls_handle_sync_and_marks_synced(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")
    add_package("google:alice@example.com", "collection", "Red", "some-package-id")

    with patch.object(config_mod, "handle_sync", return_value="Synced 3 cards.") as mock_sync:
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            response = c.post("/api/config/sync")

    assert response.status_code == 200
    assert response.json()["message"] == "Synced 3 cards."
    mock_sync.assert_called_once()


def test_sync_rejects_when_no_packages_configured(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")  # no packages added

    with patch.object(config_mod, "handle_sync") as mock_sync:
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            response = c.post("/api/config/sync")

    assert response.status_code == 400
    assert "No Moxfield packages added yet" in response.json()["detail"]
    mock_sync.assert_not_called()


def test_sync_throttled_returns_429(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")
    add_package("google:alice@example.com", "collection", "Red", "some-package-id")

    from api.users import mark_synced
    mark_synced("google:alice@example.com")

    with patch.object(config_mod, "handle_sync") as mock_sync:
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            response = c.post("/api/config/sync")

    assert response.status_code == 429
    mock_sync.assert_not_called()


def test_sync_skips_throttle_for_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "owner@example.com")
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:owner@example.com")
    add_package("google:owner@example.com", "collection", "Red", "some-package-id")

    from api.users import mark_synced
    mark_synced("google:owner@example.com")  # just synced 0 minutes ago

    with patch.object(config_mod, "handle_sync", return_value="Synced.") as mock_sync:
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:owner@example.com"
            response = c.post("/api/config/sync")

    assert response.status_code == 200
    mock_sync.assert_called_once()
    # Confirm handle_sync was told this IS the owner, not hardcoded False.
    _, kwargs = mock_sync.call_args
    assert kwargs.get("is_owner") is True


def test_complete_onboarding_marks_user_onboarded(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    from api.users import is_onboarded

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        assert not is_onboarded("google:alice@example.com")
        response = c.post("/api/onboarding/complete")

    assert response.status_code == 200
    assert is_onboarded("google:alice@example.com")


def test_complete_onboarding_requires_auth(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post("/api/onboarding/complete", follow_redirects=False)
    assert response.status_code in (302, 307)


def test_sync_still_throttles_non_owner(tmp_path, monkeypatch):
    monkeypatch.delenv("OWNER_GOOGLE_EMAIL", raising=False)
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")
    add_package("google:alice@example.com", "collection", "Red", "some-package-id")

    from api.users import mark_synced
    mark_synced("google:alice@example.com")

    with patch.object(config_mod, "handle_sync") as mock_sync:
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            response = c.post("/api/config/sync")

    assert response.status_code == 429
    mock_sync.assert_not_called()


def test_add_package_triggers_auto_sync(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with patch.object(config_mod, "handle_sync", return_value="Synced.") as mock_sync:
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            response = c.post(
                "/api/config/packages",
                json={"section": "collection", "color_group": "Red", "public_id": "some-id"},
            )

    assert response.status_code == 200
    assert response.json()["auto_sync"] == {"synced": True, "message": "Synced."}
    mock_sync.assert_called_once()


def test_add_package_auto_sync_throttled_within_60_seconds(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, mark_auto_synced
    ensure_user("google:alice@example.com")
    mark_auto_synced("google:alice@example.com")

    with patch.object(config_mod, "handle_sync") as mock_sync:
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            response = c.post(
                "/api/config/packages",
                json={"section": "collection", "color_group": "Blue", "public_id": "another-id"},
            )

    assert response.status_code == 200
    body = response.json()["auto_sync"]
    assert body["synced"] is False
    assert 0 < body["retry_after_seconds"] <= 60
    mock_sync.assert_not_called()


def test_remove_package_triggers_auto_sync(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import add_package, ensure_user
    ensure_user("google:alice@example.com")
    package_id = add_package("google:alice@example.com", "collection", "Red", "some-id")

    with patch.object(config_mod, "handle_sync", return_value="Synced.") as mock_sync:
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            response = c.delete(f"/api/config/packages/{package_id}")

    assert response.status_code == 200
    assert response.json()["auto_sync"]["synced"] is True
    mock_sync.assert_called_once()


def test_upload_manabox_csv_imports_cards(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, get_user_config
    ensure_user("google:alice@example.com")

    csv_text = (
        "Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID,"
        "Purchase price,Misprint,Altered,Condition,Language,Purchase price currency,Added\n"
        "Brainstorm,ice,Ice Age,48,normal,rare,3,1,scry-1,1.00,false,false,near_mint,en,EUR,2026-07-23T00:00:00Z\n"
    )

    with patch.object(config_mod, "import_manabox_csv") as mock_import:
        from mtg_manager.models import OwnedCard
        mock_import.return_value = [
            OwnedCard(name="Brainstorm", quantity=3, color_group="manabox", set_code="ice", collector_number="48", foil=False, cmc=1.0)
        ]
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            response = c.post(
                "/api/config/manabox",
                files={"file": ("collection.csv", csv_text, "text/csv")},
            )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "imported": 1}

    cfg = get_user_config("google:alice@example.com")
    from mtg_manager.db import get_conn
    with get_conn(cfg.db_path) as conn:
        row = conn.execute("SELECT quantity, color_group FROM owned_cards WHERE name = 'Brainstorm'").fetchone()
    assert row["quantity"] == 3
    assert row["color_group"] == "manabox"


def test_upload_manabox_csv_with_utf8_bom_imports_cards(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, get_user_config
    ensure_user("google:alice@example.com")

    csv_text = (
        "Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID,"
        "Purchase price,Misprint,Altered,Condition,Language,Purchase price currency,Added\n"
        "Brainstorm,ice,Ice Age,48,normal,rare,3,1,scry-1,1.00,false,false,near_mint,en,EUR,2026-07-23T00:00:00Z\n"
    )

    with patch.object(config_mod, "import_manabox_csv") as mock_import:
        from mtg_manager.models import OwnedCard
        mock_import.return_value = [
            OwnedCard(name="Brainstorm", quantity=3, color_group="manabox", set_code="ice", collector_number="48", foil=False, cmc=1.0)
        ]
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            response = c.post(
                "/api/config/manabox",
                files={"file": ("collection.csv", csv_text.encode("utf-8-sig"), "text/csv")},
            )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "imported": 1}

    # Confirm the BOM was stripped before reaching import_manabox_csv.
    called_text = mock_import.call_args[0][0]
    assert not called_text.startswith("﻿")
    assert called_text.startswith("Name,Set code")

    cfg = get_user_config("google:alice@example.com")
    from mtg_manager.db import get_conn
    with get_conn(cfg.db_path) as conn:
        row = conn.execute("SELECT quantity, color_group FROM owned_cards WHERE name = 'Brainstorm'").fetchone()
    assert row["quantity"] == 3
    assert row["color_group"] == "manabox"


def test_upload_manabox_csv_rejects_invalid_csv(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post(
            "/api/config/manabox",
            files={"file": ("bad.csv", "Name,Quantity\nBrainstorm,1\n", "text/csv")},
        )

    assert response.status_code == 400
    assert "missing required column" in response.json()["detail"]


def test_upload_manabox_csv_replaces_previous_manabox_import(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, get_user_config
    from mtg_manager.db import get_conn, upsert_cards
    from mtg_manager.models import OwnedCard
    ensure_user("google:alice@example.com")
    cfg = get_user_config("google:alice@example.com")
    with get_conn(cfg.db_path) as conn:
        upsert_cards(conn, [OwnedCard(name="Old Card", quantity=1, color_group="manabox", set_code="xxx", collector_number="1")])

    with patch.object(config_mod, "import_manabox_csv") as mock_import:
        mock_import.return_value = [
            OwnedCard(name="New Card", quantity=1, color_group="manabox", set_code="yyy", collector_number="2")
        ]
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            c.post("/api/config/manabox", files={"file": ("c.csv", "irrelevant", "text/csv")})

    with get_conn(cfg.db_path) as conn:
        names = {r["name"] for r in conn.execute("SELECT name FROM owned_cards WHERE color_group = 'manabox'")}
    assert names == {"New Card"}


def test_directory_lists_all_registered_users_without_collection_data(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, set_profile
    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    set_profile("google:bob@example.com", "Bob", "🐉")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/users/directory")

    assert response.status_code == 200
    people = response.json()["people"]
    assert {"user_id": "google:bob@example.com", "display_name": "Bob", "icon": "🐉"} in people
    assert all("cards" not in p and "quantity" not in p for p in people)


def test_directory_hides_private_users_from_non_admins(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, set_privacy
    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    set_privacy("google:bob@example.com", True)

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/users/directory")

    people_ids = {p["user_id"] for p in response.json()["people"]}
    assert "google:bob@example.com" not in people_ids


def test_directory_excludes_caller_themself(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user
    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/users/directory")

    people_ids = {p["user_id"] for p in response.json()["people"]}
    assert "google:alice@example.com" not in people_ids
    assert "google:bob@example.com" in people_ids


def test_directory_requires_auth(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/users/directory", follow_redirects=False)
    assert response.status_code in (302, 307)


def test_create_group_route(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post("/api/config/groups", json={"name": "Cube Night"})

    assert response.status_code == 200
    from api.users import list_groups
    assert list_groups("google:alice@example.com")[0]["name"] == "Cube Night"


def test_rename_group_route(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import create_group, ensure_user
    ensure_user("google:alice@example.com")
    group_id = create_group("google:alice@example.com", "Cube Night")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.patch(f"/api/config/groups/{group_id}", json={"name": "Legacy Cube"})

    assert response.status_code == 200
    from api.users import list_groups
    assert list_groups("google:alice@example.com")[0]["name"] == "Legacy Cube"


def test_delete_group_route(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import create_group, ensure_user
    ensure_user("google:alice@example.com")
    group_id = create_group("google:alice@example.com", "Cube Night")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.delete(f"/api/config/groups/{group_id}")

    assert response.status_code == 200
    from api.users import list_groups
    assert list_groups("google:alice@example.com") == []


def test_add_group_member_route(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import create_group, ensure_user
    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    group_id = create_group("google:alice@example.com", "Cube Night")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post(f"/api/config/groups/{group_id}/members", json={"member_user_id": "google:bob@example.com"})

    assert response.status_code == 200
    from api.users import list_group_members
    assert list_group_members(group_id)[0]["user_id"] == "google:bob@example.com"


def test_add_group_member_route_rejects_group_not_owned_by_caller(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import create_group, ensure_user
    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    ensure_user("google:mallory@example.com")
    group_id = create_group("google:alice@example.com", "Cube Night")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:mallory@example.com"
        response = c.post(f"/api/config/groups/{group_id}/members", json={"member_user_id": "google:bob@example.com"})

    assert response.status_code == 404


def test_remove_group_member_route(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import add_group_member, create_group, ensure_user
    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    group_id = create_group("google:alice@example.com", "Cube Night")
    add_group_member("google:alice@example.com", group_id, "google:bob@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.delete(f"/api/config/groups/{group_id}/members/google:bob@example.com")

    assert response.status_code == 200
    from api.users import list_group_members
    assert list_group_members(group_id) == []


def test_get_config_includes_groups(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import add_group_member, create_group, ensure_user
    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    group_id = create_group("google:alice@example.com", "Cube Night")
    add_group_member("google:alice@example.com", group_id, "google:bob@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/config")

    groups = response.json()["groups"]
    assert groups == [{
        "id": group_id,
        "name": "Cube Night",
        "members": [{"user_id": "google:bob@example.com", "display_name": "google:bob@example.com", "icon": "🂠"}],
    }]


def test_set_and_get_privacy(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post("/api/config/privacy", json={"is_private": True})
        assert response.status_code == 200
        config_response = c.get("/api/config")

    assert config_response.json()["is_private"] is True
