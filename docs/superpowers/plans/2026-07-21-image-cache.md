# Server-Side Image Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve card images from a local, shared, lazily-populated disk cache instead of hitting Scryfall's live API on every page load for every user.

**Architecture:** A new `webapp/images.py` module owns the cache lookup/fetch/save logic and exposes a small `APIRouter` with one route, `GET /images/{set_code}/{collector_number}`. `webapp/main.py` mounts that router. `webapp/static/app.html`'s `scryfallImgUrl()` is updated to point at the new route instead of Scryfall directly.

**Tech Stack:** FastAPI, `httpx` (already a dependency via Authlib's Starlette integration), pytest + `unittest.mock`.

## Global Constraints

- No authentication required on `GET /images/...` — this serves publicly-available Scryfall card art, same trust level as the old static site's unauthenticated image directory.
- Cache location: `~/mtg_data/image_cache/{set_code}/{safe_cn}.jpg`, following the existing `~/mtg_data/` convention.
- `set_code` and the sanitized collector number must be validated against unsafe characters (path traversal guard) before being used to build a filesystem path — reject with 400 otherwise.
- The real (unsanitized) `collector_number` is still used for the outbound Scryfall URL — only the on-disk filename is sanitized.
- `namedImgUrl()` in `app.html` is unchanged — it stays pointed directly at Scryfall (no stable cache key for a name-only lookup).
- No cache eviction/expiry logic — printings are immutable once cached.

---

## Task 1: `webapp/images.py` — cache logic and route

**Files:**
- Create: `webapp/images.py`
- Create: `tests/test_webapp_images.py`

**Interfaces:**
- Produces: `router: APIRouter` with `GET /images/{set_code}/{collector_number}`; `IMAGE_CACHE_DIR: Path` (module-level, `Path("~/mtg_data/image_cache").expanduser()`, overridable via monkeypatch in tests the same way `api/users.py`'s `_REGISTRY_PATH`/`_USERS_DIR` are monkeypatched).
- Consumes: `httpx` for the outbound Scryfall request.

- [ ] **Step 1: Write failing tests in `tests/test_webapp_images.py`**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_images.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'webapp.images'`.

- [ ] **Step 3: Implement `webapp/images.py`**

```python
"""Shared, lazily-populated disk cache for Scryfall card images."""

import re
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException
from starlette.responses import FileResponse, Response

IMAGE_CACHE_DIR = Path("~/mtg_data/image_cache").expanduser()

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_-]+$")

router = APIRouter()


@router.get("/images/{set_code}/{collector_number}")
async def get_card_image(set_code: str, collector_number: str):
    safe_cn = collector_number.replace("/", "_")

    if not _SAFE_COMPONENT.match(set_code) or not _SAFE_COMPONENT.match(safe_cn):
        raise HTTPException(status_code=400, detail="Invalid set code or collector number")

    cache_path = IMAGE_CACHE_DIR / set_code / f"{safe_cn}.jpg"
    if cache_path.exists():
        return FileResponse(
            cache_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    url = (
        f"https://api.scryfall.com/cards/{quote(set_code, safe='')}"
        f"/{quote(collector_number, safe='')}?format=image&version=normal"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers={"User-Agent": "mtg-manager/1.0 (personal collection site)"})

    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="Card image not found")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(resp.content)

    return Response(
        content=resp.content,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_images.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add webapp/images.py tests/test_webapp_images.py
git commit -m "feat: add shared server-side image cache for card art"
```

---

## Task 2: Wire the router into the app and update the frontend

**Files:**
- Modify: `webapp/main.py`
- Modify: `webapp/static/app.html`
- Create: `tests/test_webapp_main_images.py`

**Interfaces:**
- Consumes: `webapp.images.router` (Task 1).
- Produces: `GET /images/{set_code}/{collector_number}` reachable from the main app (`webapp.main:app`), not just in isolation.

- [ ] **Step 1: Write a failing integration test in `tests/test_webapp_main_images.py`**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_webapp_main_images.py -v`
Expected: FAIL — `/images/...` isn't mounted on the main app yet (404), even though it works in isolation per Task 1's own tests.

- [ ] **Step 3: Mount the router in `webapp/main.py`**

Add the import alongside the existing `webapp.auth` import, and mount it alongside `auth_router`:

```python
from webapp.images import router as images_router
```

```python
app.include_router(auth_router)
app.include_router(images_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_webapp_main_images.py -v`
Expected: PASS.

- [ ] **Step 5: Update `webapp/static/app.html`'s `scryfallImgUrl()`**

Change:

```js
function scryfallImgUrl(set_code, collector_number) {
  return `https://api.scryfall.com/cards/${set_code}/${collector_number}?format=image&version=normal`;
}
```

to:

```js
function scryfallImgUrl(set_code, collector_number) {
  return `/images/${encodeURIComponent(set_code)}/${encodeURIComponent(collector_number)}`;
}
```

Leave `namedImgUrl()` unchanged.

- [ ] **Step 6: Sanity-check the file is still well-formed HTML**

Run: `python -c "import html.parser; p = html.parser.HTMLParser(); p.feed(open('webapp/static/app.html', encoding='utf-8').read())"`
Expected: No exception raised.

- [ ] **Step 7: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add webapp/main.py webapp/static/app.html tests/test_webapp_main_images.py
git commit -m "feat: serve app.html card images from the local image cache"
```

---

## Self-Review Notes

- **Spec coverage:** cache location, lazy fetch-and-save, sanitized filename vs. real Scryfall lookup value, path-traversal rejection, `namedImgUrl()` left untouched, no auth required — all covered across the two tasks.
- **Type consistency:** `router` (Task 1) is an `APIRouter`, mounted identically to the existing `auth_router` pattern in `webapp/main.py` (Task 2).
- **No placeholders:** both tasks contain complete, runnable code.
