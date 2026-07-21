from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from api.users import add_whitelisted_email, is_registered
import webapp.auth as auth_mod


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "users")

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.include_router(auth_mod.router)
    return TestClient(app)


def test_login_redirects_to_google(app_client):
    with patch.object(
        auth_mod.oauth.google, "authorize_redirect", new=AsyncMock(
            return_value=__import__("starlette.responses", fromlist=["RedirectResponse"])
            .RedirectResponse("https://accounts.google.com/fake")
        )
    ):
        response = app_client.get("/login", follow_redirects=False)
    assert response.status_code in (302, 307)


def test_callback_rejects_non_whitelisted_email(app_client):
    fake_token = {"userinfo": {"email": "stranger@example.com"}}
    with patch.object(
        auth_mod.oauth.google, "authorize_access_token", new=AsyncMock(return_value=fake_token)
    ):
        response = app_client.get("/auth/callback", follow_redirects=False)
    assert response.status_code == 403
    assert not is_registered("google:stranger@example.com")


def test_callback_registers_whitelisted_email(app_client):
    add_whitelisted_email("alice@example.com")
    fake_token = {"userinfo": {"email": "alice@example.com"}}
    with patch.object(
        auth_mod.oauth.google, "authorize_access_token", new=AsyncMock(return_value=fake_token)
    ):
        response = app_client.get("/auth/callback", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/app"
    assert is_registered("google:alice@example.com")


def test_logout_clears_session(app_client):
    add_whitelisted_email("alice@example.com")
    fake_token = {"userinfo": {"email": "alice@example.com"}}
    with patch.object(
        auth_mod.oauth.google, "authorize_access_token", new=AsyncMock(return_value=fake_token)
    ):
        app_client.get("/auth/callback")

    response = app_client.post("/logout", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"
