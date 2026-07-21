import pytest
from starlette.requests import Request

from api.users import ensure_user
from webapp.deps import NotAuthenticated, require_user


def _request_with_session(session: dict) -> Request:
    scope = {
        "type": "http",
        "session": session,
        "headers": [],
        "method": "GET",
        "path": "/",
    }
    return Request(scope)


def test_require_user_raises_when_no_session():
    request = _request_with_session({})
    with pytest.raises(NotAuthenticated):
        require_user(request)


def test_require_user_raises_when_user_not_registered():
    request = _request_with_session({"user_id": "google:ghost@example.com"})
    with pytest.raises(NotAuthenticated):
        require_user(request)


def test_require_user_returns_config_for_known_user(tmp_path, monkeypatch):
    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "users")

    ensure_user("google:alice@example.com")
    request = _request_with_session({"user_id": "google:alice@example.com"})
    cfg = require_user(request)
    assert cfg is not None
