import contextlib
import json
from base64 import b64encode

import itsdangerous
from fastapi.testclient import TestClient

# NOTE: Starlette's TestClient (httpx-based, installed version 1.0.0) has no
# `session_transaction()` method — that API is a Flask test-client feature,
# not a Starlette/FastAPI one. To keep the test bodies exactly as specified
# (interacting with a `session_transaction()` context manager), we attach a
# real, equivalent implementation here: it signs a session cookie the same
# way `starlette.middleware.sessions.SessionMiddleware` does (itsdangerous
# `TimestampSigner` over a base64-encoded JSON payload) and sets it on the
# client's cookie jar. This exercises the real signed-cookie mechanism the
# app uses in production — it is not a mock of authentication.
# (Same helper as tests/test_webapp_main.py.)


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
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")

    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "users")

    from webapp.main import app
    client = TestClient(app)
    _install_session_transaction(client, "test-secret")
    return client


def test_config_route_redirects_when_not_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/config", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_config_route_serves_page_when_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user, mark_onboarded
    ensure_user("google:alice@example.com")
    mark_onboarded("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/config")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_config_route_redirects_to_onboarding_when_not_onboarded(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/config", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/onboarding"
