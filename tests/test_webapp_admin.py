import contextlib
import json
from base64 import b64encode

import itsdangerous
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

from api.users import add_whitelisted_email, ensure_user, log_failed_login, log_request
import webapp.admin as admin_mod
from webapp.deps import NotAuthenticated


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
    app.include_router(admin_mod.router)

    @app.exception_handler(NotAuthenticated)
    async def _handle_not_authenticated(request: Request, exc: NotAuthenticated):
        return RedirectResponse(url="/login", status_code=302)

    client = TestClient(app)
    _install_session_transaction(client, "test-secret")
    return client


def _as_admin(client, admin_email="boss@example.com"):
    ensure_user(f"google:{admin_email}")
    add_whitelisted_email(admin_email, is_admin=True)
    return admin_email


def test_admin_routes_require_admin(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")  # not an admin

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        assert c.get("/api/admin/users").status_code == 403
        assert c.get("/api/admin/failed-logins").status_code == 403
        assert c.get("/api/admin/activity").status_code == 403
        assert c.get("/api/admin/whitelist").status_code == 403
        assert (
            c.post(
                "/api/admin/whitelist",
                json={"email": "someone@example.com", "is_admin": False},
            ).status_code
            == 403
        )


def test_admin_can_list_users(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    admin_email = _as_admin(client)
    ensure_user("google:friend@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = f"google:{admin_email}"
        response = c.get("/api/admin/users")

    assert response.status_code == 200
    user_ids = [row["user_id"] for row in response.json()["users"]]
    assert f"google:{admin_email}" in user_ids
    assert "google:friend@example.com" in user_ids


def test_admin_can_list_failed_logins(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    admin_email = _as_admin(client)
    log_failed_login("stranger@example.com", "not_whitelisted")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = f"google:{admin_email}"
        response = c.get("/api/admin/failed-logins")

    assert response.status_code == 200
    emails = [row["email"] for row in response.json()["failed_logins"]]
    assert "stranger@example.com" in emails


def test_admin_can_list_activity(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    admin_email = _as_admin(client)
    log_request("google:friend@example.com", "POST", "/api/config/packages", 200)

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = f"google:{admin_email}"
        response = c.get("/api/admin/activity")

    assert response.status_code == 200
    paths = [row["path"] for row in response.json()["activity"]]
    assert "/api/config/packages" in paths


def test_admin_can_list_and_add_whitelist(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    admin_email = _as_admin(client)

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = f"google:{admin_email}"
        add_response = c.post("/api/admin/whitelist", json={"email": "friend@example.com", "is_admin": False})
        list_response = c.get("/api/admin/whitelist")

    assert add_response.status_code == 200
    emails = [row["email"] for row in list_response.json()["whitelist"]]
    assert "friend@example.com" in emails
    assert admin_email in emails


def test_admin_can_remove_whitelisted_user(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    admin_email = _as_admin(client)
    ensure_user("google:friend@example.com")
    add_whitelisted_email("friend@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = f"google:{admin_email}"
        remove_response = c.delete("/api/admin/whitelist/friend@example.com")
        list_response = c.get("/api/admin/whitelist")

    assert remove_response.status_code == 200
    emails = [row["email"] for row in list_response.json()["whitelist"]]
    assert "friend@example.com" not in emails


def test_admin_cannot_remove_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "boss@example.com")
    client = _client(tmp_path, monkeypatch)
    admin_email = _as_admin(client)

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = f"google:{admin_email}"
        response = c.delete(f"/api/admin/whitelist/{admin_email}")

    assert response.status_code == 400


def test_non_admin_cannot_remove_whitelisted_user(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")  # not an admin

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.delete("/api/admin/whitelist/someone@example.com")

    assert response.status_code == 403


def test_admin_user_detail_requires_admin(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")  # not an admin
    ensure_user("google:friend@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        assert c.get("/api/admin/users/google:friend@example.com").status_code == 403
        assert c.post("/api/admin/users/google:friend@example.com/sync").status_code == 403


def test_admin_user_detail_404s_for_unknown_user(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    admin_email = _as_admin(client)

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = f"google:{admin_email}"
        detail_response = c.get("/api/admin/users/google:nobody@example.com")
        sync_response = c.post("/api/admin/users/google:nobody@example.com/sync")

    assert detail_response.status_code == 404
    assert sync_response.status_code == 404


def test_admin_user_detail_returns_packages_and_activity(tmp_path, monkeypatch):
    from api.users import add_package

    client = _client(tmp_path, monkeypatch)
    admin_email = _as_admin(client)
    ensure_user("google:friend@example.com")
    add_package("google:friend@example.com", "collection", "red", "abc123")
    log_request("google:friend@example.com", "GET", "/api/config", 200)
    log_request("google:someone-else@example.com", "GET", "/api/config", 200)

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = f"google:{admin_email}"
        response = c.get("/api/admin/users/google:friend@example.com")

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "google:friend@example.com"
    data = response.json()
    assert data["packages"] == [
        {"id": data["packages"][0]["id"], "section": "collection", "color_group": "red", "public_id": "abc123", "price": None}
    ]
    paths = [row["path"] for row in body["activity"]]
    assert paths == ["/api/config"]  # only friend's row, not someone-else's


def test_admin_sync_bypasses_self_service_throttle(tmp_path, monkeypatch):
    from api.users import add_package, mark_synced

    client = _client(tmp_path, monkeypatch)
    admin_email = _as_admin(client)
    ensure_user("google:friend@example.com")
    add_package("google:friend@example.com", "collection", "red", "abc123")
    mark_synced("google:friend@example.com")  # simulate "just synced" -- would block self-service

    monkeypatch.setattr(admin_mod, "handle_sync", lambda cfg, is_owner: "Synced 1 package.")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = f"google:{admin_email}"
        response = c.post("/api/admin/users/google:friend@example.com/sync")

    assert response.status_code == 200
    assert response.json() == {"message": "Synced 1 package."}
