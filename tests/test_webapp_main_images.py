from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient


def test_images_route_reachable_from_main_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")

    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "users")

    import webapp.images as images_mod
    monkeypatch.setattr(images_mod, "IMAGE_CACHE_DIR", tmp_path / "image_cache")

    from webapp.main import app
    client = TestClient(app)

    fake_bytes = b"fake-jpeg-bytes"
    fake_response = httpx.Response(200, content=fake_bytes, request=httpx.Request("GET", "https://api.scryfall.com/x"))
    with patch.object(images_mod.httpx.AsyncClient, "get", new=AsyncMock(return_value=fake_response)):
        response = client.get("/images/m10/146")

    assert response.status_code == 200
    assert response.content == fake_bytes
