import contextlib
import json
from base64 import b64encode

import itsdangerous
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

from api.users import ensure_user
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
    assert data["packages"] == []
    assert data["formats"] == []
    assert data["pick_list_sort"] == "colour"
    assert data["is_admin"] is False


def test_add_and_list_package(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        add_response = c.post("/api/config/packages", json={"color_group": "Red", "public_id": "abc123"})
        get_response = c.get("/api/config")

    assert add_response.status_code == 200
    assert get_response.json()["packages"] == [{"color_group": "Red", "public_id": "abc123"}]


def test_remove_package(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        c.post("/api/config/packages", json={"color_group": "Red", "public_id": "abc123"})
        remove_response = c.delete("/api/config/packages/Red")
        get_response = c.get("/api/config")

    assert remove_response.status_code == 200
    assert get_response.json()["packages"] == []


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
