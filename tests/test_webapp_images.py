from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import webapp.images as images_mod


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setattr(images_mod, "IMAGE_CACHE_DIR", tmp_path / "image_cache")
    app = FastAPI()
    app.include_router(images_mod.router)
    return TestClient(app)


def _fake_scryfall_response(content: bytes, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, content=content, request=httpx.Request("GET", "https://api.scryfall.com/x"))


def test_cache_miss_fetches_saves_and_serves(app_client, tmp_path):
    fake_bytes = b"fake-jpeg-bytes"
    with patch.object(
        images_mod.httpx.AsyncClient, "get", new=AsyncMock(return_value=_fake_scryfall_response(fake_bytes))
    ) as mock_get:
        response = app_client.get("/images/m10/146")

    assert response.status_code == 200
    assert response.content == fake_bytes
    mock_get.assert_called_once()
    cached_path = tmp_path / "image_cache" / "m10" / "146.jpg"
    assert cached_path.exists()
    assert cached_path.read_bytes() == fake_bytes


def test_cache_hit_does_not_call_scryfall_again(app_client, tmp_path):
    fake_bytes = b"fake-jpeg-bytes"
    with patch.object(
        images_mod.httpx.AsyncClient, "get", new=AsyncMock(return_value=_fake_scryfall_response(fake_bytes))
    ) as mock_get:
        app_client.get("/images/m10/146")
        response = app_client.get("/images/m10/146")

    assert response.status_code == 200
    assert response.content == fake_bytes
    mock_get.assert_called_once()  # only the first request hit Scryfall


def test_unknown_printing_returns_404_and_caches_nothing(app_client, tmp_path):
    with patch.object(
        images_mod.httpx.AsyncClient, "get", new=AsyncMock(return_value=_fake_scryfall_response(b"", status_code=404))
    ):
        response = app_client.get("/images/zzz/999")

    assert response.status_code == 404
    assert not (tmp_path / "image_cache" / "zzz" / "999.jpg").exists()


def test_network_error_returns_404_and_caches_nothing(app_client, tmp_path):
    with patch.object(
        images_mod.httpx.AsyncClient,
        "get",
        new=AsyncMock(side_effect=httpx.ConnectError("connection failed")),
    ):
        response = app_client.get("/images/m10/146")

    assert response.status_code == 404
    assert not (tmp_path / "image_cache" / "m10" / "146.jpg").exists()


def test_path_traversal_rejected(app_client):
    response = app_client.get("/images/..%2f..%2fetc/passwd")
    assert response.status_code in (400, 404)


def test_collector_number_with_slash_is_sanitized_for_filename(app_client, tmp_path):
    fake_bytes = b"fake-jpeg-bytes"
    with patch.object(
        images_mod.httpx.AsyncClient, "get", new=AsyncMock(return_value=_fake_scryfall_response(fake_bytes))
    ):
        response = app_client.get("/images/lea/12a")

    assert response.status_code == 200
    assert (tmp_path / "image_cache" / "lea" / "12a.jpg").exists()
