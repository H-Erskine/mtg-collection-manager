# Self-Service Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a whitelisted web user manage their own Moxfield packages, tracked formats, sort order, and trigger a sync — and let an admin manage the email whitelist — entirely from the web app, without the owner's manual intervention.

**Architecture:** A new `webapp/config.py` router wraps the already-existing `api/users.py` registry functions (built for the Discord bot) and `api/handlers.py`'s `handle_sync`. A new `require_admin` dependency in `webapp/deps.py` layers an admin check on top of the existing `require_user`. A new static page `webapp/static/config.html` (same visual style as `app.html`) is the UI.

**Tech Stack:** FastAPI (existing `webapp` package), vanilla HTML/CSS/JS, pytest + FastAPI `TestClient`.

## Global Constraints

- Every `/api/config/*` route requires `require_user`; `/api/admin/*` routes additionally require `require_admin`.
- `POST /api/config/sync` must be a plain `def` route (not `async def`) so FastAPI runs the blocking Moxfield HTTP calls in its thread pool instead of the event loop.
- Sync throttle: same 60-minute rule as `api/bot.py`'s `SYNC_THROTTLE_MINUTES = 60`, using the existing `minutes_since_last_sync`/`mark_synced` functions — no new throttling logic.
- No route for removing/editing a whitelist entry — add-only for v1.
- No changes to the meta-comparison feature or any UI for it.
- No changes to `web/export.py`, the Discord bot, or `webapp/static/app.html`'s Collection/Decks/Missing tabs (only its nav gains a link to `/config`).

---

## Task 1: `require_admin` dependency

**Files:**
- Modify: `webapp/deps.py`
- Modify: `tests/test_webapp_deps.py`

**Interfaces:**
- Consumes: `require_user` (existing), `api.users.is_whitelist_admin` (existing).
- Produces: `require_admin(request: Request) -> Config` — returns the same `Config` as `require_user` if the session's identity is an admin; raises `HTTPException(403)` if authenticated but not admin; raises `NotAuthenticated` (→ redirect to `/login`) if not authenticated at all.

- [ ] **Step 1: Write failing tests in `tests/test_webapp_deps.py`**

Append to the existing file (which already has `_request_with_session` and isolates the registry via `monkeypatch`):

```python
from fastapi import HTTPException

from api.users import add_whitelisted_email
from webapp.deps import require_admin


def test_require_admin_raises_not_authenticated_when_no_session():
    request = _request_with_session({})
    with pytest.raises(NotAuthenticated):
        require_admin(request)


def test_require_admin_raises_403_for_non_admin_user(tmp_path, monkeypatch):
    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "users")

    ensure_user("google:alice@example.com")
    request = _request_with_session({"user_id": "google:alice@example.com"})
    with pytest.raises(HTTPException) as exc_info:
        require_admin(request)
    assert exc_info.value.status_code == 403


def test_require_admin_returns_config_for_admin_user(tmp_path, monkeypatch):
    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "users")

    ensure_user("google:boss@example.com")
    add_whitelisted_email("boss@example.com", is_admin=True)
    request = _request_with_session({"user_id": "google:boss@example.com"})
    cfg = require_admin(request)
    assert cfg is not None
```

Note: `tests/test_webapp_deps.py` already imports `ensure_user` and defines `_request_with_session` — reuse them rather than redefining.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_deps.py -v -k admin`
Expected: FAIL with `ImportError: cannot import name 'require_admin'`.

- [ ] **Step 3: Implement `require_admin` in `webapp/deps.py`**

```python
from fastapi import HTTPException, Request

from api.users import get_user_config, is_whitelist_admin
```

(adjust the existing `from api.users import get_user_config` import line to include `is_whitelist_admin` alongside it, rather than adding a second import line for the same module)

```python
def require_admin(request: Request) -> Config:
    cfg = require_user(request)
    user_id = request.session["user_id"]
    is_admin = user_id.startswith("google:") and is_whitelist_admin(user_id.split(":", 1)[1])
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return cfg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_deps.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add webapp/deps.py tests/test_webapp_deps.py
git commit -m "feat: add require_admin dependency for whitelist-admin-gated routes"
```

---

## Task 2: Config CRUD routes (`webapp/config.py`)

**Files:**
- Create: `webapp/config.py`
- Create: `tests/test_webapp_config.py`

**Interfaces:**
- Consumes: `require_user` (existing), `api.users.{list_packages, add_package, remove_package, set_sort, set_formats, minutes_since_last_sync, is_whitelist_admin}` (existing).
- Produces: `router: APIRouter` with `GET /api/config`, `POST /api/config/packages`, `DELETE /api/config/packages/{color_group}`, `POST /api/config/sort`, `POST /api/config/formats`.

- [ ] **Step 1: Write failing tests in `tests/test_webapp_config.py`**

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from api.users import ensure_user
import webapp.config as config_mod


def _client(tmp_path, monkeypatch):
    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "users")

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.include_router(config_mod.router)
    return TestClient(app)


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'webapp.config'`.

- [ ] **Step 3: Implement `webapp/config.py`**

```python
"""Self-service config routes: Moxfield packages, formats, sort order."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.users import (
    add_package,
    is_whitelist_admin,
    list_packages,
    minutes_since_last_sync,
    remove_package,
    set_formats,
    set_sort,
)
from mtg_manager.config import Config
from webapp.deps import require_user

router = APIRouter()


class PackageIn(BaseModel):
    color_group: str
    public_id: str


class SortIn(BaseModel):
    sort_mode: str


class FormatsIn(BaseModel):
    formats: list[str]


def _is_admin(user_id: str) -> bool:
    return user_id.startswith("google:") and is_whitelist_admin(user_id.split(":", 1)[1])


@router.get("/api/config")
async def get_config(request: Request, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    pkgs = list_packages(user_id)
    return {
        "packages": [{"color_group": cg, "public_id": pid} for cg, pid in pkgs],
        "formats": cfg.formats,
        "pick_list_sort": cfg.pick_list_sort,
        "minutes_since_last_sync": minutes_since_last_sync(user_id),
        "is_admin": _is_admin(user_id),
    }


@router.post("/api/config/packages")
async def add_config_package(request: Request, body: PackageIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    add_package(user_id, body.color_group, body.public_id)
    return {"ok": True}


@router.delete("/api/config/packages/{color_group}")
async def remove_config_package(request: Request, color_group: str, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    remove_package(user_id, color_group)
    return {"ok": True}


@router.post("/api/config/sort")
async def set_config_sort(request: Request, body: SortIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    try:
        set_sort(user_id, body.sort_mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.post("/api/config/formats")
async def set_config_formats(request: Request, body: FormatsIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    set_formats(user_id, body.formats)
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_config.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add webapp/config.py tests/test_webapp_config.py
git commit -m "feat: add self-service config CRUD routes for packages/sort/formats"
```

---

## Task 3: Sync route

**Files:**
- Modify: `webapp/config.py`
- Modify: `tests/test_webapp_config.py`

**Interfaces:**
- Consumes: `api.handlers.handle_sync`, `api.users.mark_synced` (existing).
- Produces: `POST /api/config/sync` on the same `router`.

- [ ] **Step 1: Write failing tests in `tests/test_webapp_config.py`**

Append (uses `unittest.mock.patch` to avoid real Moxfield network calls, matching the pattern already used for mocking Authlib in `tests/test_webapp_auth.py`):

```python
from unittest.mock import patch


def test_sync_calls_handle_sync_and_marks_synced(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with patch.object(config_mod, "handle_sync", return_value="Synced 3 cards.") as mock_sync:
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            response = c.post("/api/config/sync")

    assert response.status_code == 200
    assert response.json()["message"] == "Synced 3 cards."
    mock_sync.assert_called_once()


def test_sync_throttled_returns_429(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    from api.users import mark_synced
    mark_synced("google:alice@example.com")

    with patch.object(config_mod, "handle_sync") as mock_sync:
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            response = c.post("/api/config/sync")

    assert response.status_code == 429
    mock_sync.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_config.py -v -k sync`
Expected: FAIL — `/api/config/sync` doesn't exist yet (404).

- [ ] **Step 3: Implement the sync route in `webapp/config.py`**

Add to the imports:

```python
from api.handlers import handle_sync
from api.users import mark_synced
```

(combine with the existing `from api.users import (...)` block rather than adding a second import line for the same module)

```python
SYNC_THROTTLE_MINUTES = 60


@router.post("/api/config/sync")
def sync_now(request: Request, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    mins = minutes_since_last_sync(user_id)
    if mins is not None and mins < SYNC_THROTTLE_MINUTES:
        remaining = int(SYNC_THROTTLE_MINUTES - mins)
        raise HTTPException(
            status_code=429,
            detail=f"Already synced {int(mins)} min ago. Try again in {remaining} min.",
        )

    message = handle_sync(cfg, is_owner=False)
    mark_synced(user_id)
    return {"message": message}
```

(Note: this route is defined as a plain `def`, not `async def` — per the Global Constraints, so FastAPI runs `handle_sync`'s blocking Moxfield HTTP calls in its thread pool.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_config.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add webapp/config.py tests/test_webapp_config.py
git commit -m "feat: add on-demand sync route with existing throttle rule"
```

---

## Task 4: Admin whitelist routes

**Files:**
- Modify: `webapp/config.py`
- Modify: `tests/test_webapp_config.py`

**Interfaces:**
- Consumes: `require_admin` (Task 1), `api.users.add_whitelisted_email` (existing).
- Produces: `GET /api/admin/whitelist`, `POST /api/admin/whitelist` on the same `router`.

- [ ] **Step 1: Write failing tests in `tests/test_webapp_config.py`**

```python
def test_whitelist_routes_require_admin(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")  # not an admin

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        get_response = c.get("/api/admin/whitelist")
        post_response = c.post("/api/admin/whitelist", json={"email": "new@example.com", "is_admin": False})

    assert get_response.status_code == 403
    assert post_response.status_code == 403


def test_admin_can_list_and_add_whitelist(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import add_whitelisted_email
    ensure_user("google:boss@example.com")
    add_whitelisted_email("boss@example.com", is_admin=True)

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:boss@example.com"
        add_response = c.post("/api/admin/whitelist", json={"email": "friend@example.com", "is_admin": False})
        list_response = c.get("/api/admin/whitelist")

    assert add_response.status_code == 200
    emails = [row["email"] for row in list_response.json()["whitelist"]]
    assert "friend@example.com" in emails
    assert "boss@example.com" in emails
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_config.py -v -k whitelist`
Expected: FAIL — `/api/admin/whitelist` doesn't exist yet (404).

- [ ] **Step 3: Implement the whitelist routes in `webapp/config.py`**

Add to the imports:

```python
from api.users import add_whitelisted_email
from webapp.deps import require_admin
```

(combine `add_whitelisted_email` into the existing `api.users` import block; add the `require_admin` name to the existing `from webapp.deps import require_user` line)

Add a new request model and two routes:

```python
class WhitelistIn(BaseModel):
    email: str
    is_admin: bool = False


@router.get("/api/admin/whitelist")
async def list_whitelist(cfg: Config = Depends(require_admin)):
    from api.users import _registry_conn  # reuse existing connection helper

    with _registry_conn() as conn:
        rows = conn.execute(
            "SELECT email, is_admin, added_at FROM whitelisted_emails ORDER BY added_at"
        ).fetchall()
    return {
        "whitelist": [
            {"email": r["email"], "is_admin": bool(r["is_admin"]), "added_at": r["added_at"]}
            for r in rows
        ]
    }


@router.post("/api/admin/whitelist")
async def add_to_whitelist(body: WhitelistIn, cfg: Config = Depends(require_admin)):
    add_whitelisted_email(body.email, is_admin=body.is_admin)
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_config.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add webapp/config.py tests/test_webapp_config.py
git commit -m "feat: add admin-only whitelist management routes"
```

---

## Task 5: `config.html` page, `GET /config` route, nav link

**Files:**
- Create: `webapp/static/config.html`
- Modify: `webapp/main.py`
- Modify: `webapp/static/app.html`
- Create: `tests/test_webapp_main_config.py`

**Interfaces:**
- Consumes: `webapp.config.router` (Tasks 2-4), `require_user` (existing).
- Produces: `GET /config` reachable from the main app, serving `config.html`.

- [ ] **Step 1: Write a failing integration test in `tests/test_webapp_main_config.py`**

```python
def test_config_route_redirects_when_not_logged_in(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")

    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "users")

    from fastapi.testclient import TestClient
    from webapp.main import app
    client = TestClient(app)

    response = client.get("/config", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_config_route_serves_page_when_logged_in(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")

    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "users")

    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    from fastapi.testclient import TestClient
    from webapp.main import app
    client = TestClient(app)

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/config")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_main_config.py -v`
Expected: FAIL — `/config` doesn't exist on the main app yet (404).

- [ ] **Step 3: Create `webapp/static/config.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MTG Collection — Config</title>
<style>
  :root {
    --bg: #1a1a2e;
    --surface: #16213e;
    --surface2: #0f3460;
    --accent: #e94560;
    --text: #eaeaea;
    --muted: #888;
    --green: #4caf50;
    --red: #e94560;
    --gold: #f0c040;
    --radius: 8px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; min-height: 100vh; }

  nav { background: var(--surface); border-bottom: 2px solid var(--surface2); padding: 0 24px; display: flex; align-items: center; gap: 0; }
  .nav-brand { font-weight: 700; font-size: 1.1rem; color: var(--gold); padding: 16px 24px 16px 0; border-right: 1px solid var(--surface2); margin-right: 8px; }
  .nav-link { padding: 16px 20px; color: var(--muted); text-decoration: none; font-size: 0.95rem; }
  .nav-link:hover { color: var(--text); }
  .nav-meta { margin-left: auto; font-size: 0.8rem; color: var(--muted); }
  .nav-logout { color: var(--text); text-decoration: underline; cursor: pointer; margin-left: 8px; }
  .nav-logout:hover { color: var(--accent); }

  .page { padding: 24px; max-width: 800px; margin: 0 auto; }
  section { background: var(--surface); border: 1px solid var(--surface2); border-radius: var(--radius); padding: 20px; margin-bottom: 20px; }
  h2 { font-size: 1rem; color: var(--gold); margin-bottom: 14px; }
  label { display: block; font-size: 0.85rem; color: var(--muted); margin-bottom: 4px; }
  input, select { background: var(--bg); border: 1px solid var(--surface2); color: var(--text); padding: 8px 12px; border-radius: var(--radius); font-size: 0.9rem; margin-bottom: 10px; width: 100%; }
  input:focus, select:focus { outline: none; border-color: var(--accent); }
  .btn { padding: 8px 16px; background: var(--accent); color: white; border: none; border-radius: var(--radius); font-size: 0.9rem; font-weight: 600; cursor: pointer; }
  .btn:hover { background: #c73652; }
  .btn-secondary { padding: 6px 12px; background: var(--surface2); color: var(--text); border: none; border-radius: var(--radius); font-size: 0.82rem; cursor: pointer; }
  .row { display: flex; gap: 8px; align-items: center; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--surface2); }
  .row:last-child { border-bottom: none; }
  .msg { font-size: 0.85rem; margin-top: 8px; }
  .msg.ok { color: var(--green); }
  .msg.err { color: var(--red); }
  .inline-form { display: flex; gap: 8px; align-items: flex-end; }
  .inline-form input { margin-bottom: 0; }
  .checkbox-row { display: flex; align-items: center; gap: 6px; }
  .checkbox-row input { width: auto; margin-bottom: 0; }
</style>
</head>
<body>

<nav>
  <div class="nav-brand">⚔ MTG Collection</div>
  <a class="nav-link" href="/app">Collection</a>
  <a class="nav-link" href="/config">Config</a>
  <div class="nav-meta" id="nav-identity">Loading…</div>
</nav>

<div class="page">
  <section>
    <h2>Moxfield Packages</h2>
    <div id="packages-list"></div>
    <div class="inline-form" style="margin-top:12px">
      <div style="flex:1"><label>Color group</label><input id="pkg-color" placeholder="e.g. Red"></div>
      <div style="flex:1"><label>Public ID</label><input id="pkg-id" placeholder="Moxfield deck slug"></div>
      <button class="btn" onclick="addPackage()">Add</button>
    </div>
    <div id="pkg-msg" class="msg"></div>
  </section>

  <section>
    <h2>Tracked Formats</h2>
    <label>Comma-separated (e.g. modern,legacy)</label>
    <input id="formats-input">
    <button class="btn" onclick="saveFormats()">Save</button>
    <div id="formats-msg" class="msg"></div>
  </section>

  <section>
    <h2>Pick List Sort</h2>
    <select id="sort-select">
      <option value="colour">Colour</option>
      <option value="alphabetical">Alphabetical</option>
      <option value="set">Set</option>
      <option value="cmc">CMC</option>
    </select>
    <button class="btn" onclick="saveSort()">Save</button>
    <div id="sort-msg" class="msg"></div>
  </section>

  <section>
    <h2>Sync</h2>
    <button class="btn" onclick="syncNow()">Sync now</button>
    <div id="sync-msg" class="msg"></div>
  </section>

  <section id="admin-section" style="display:none">
    <h2>Whitelist (Admin)</h2>
    <div id="whitelist-list"></div>
    <div class="inline-form" style="margin-top:12px">
      <div style="flex:1"><label>Email</label><input id="wl-email" placeholder="someone@example.com"></div>
      <div class="checkbox-row"><input type="checkbox" id="wl-admin"><label style="margin:0">Admin</label></div>
      <button class="btn" onclick="addWhitelist()">Add</button>
    </div>
    <div id="wl-msg" class="msg"></div>
  </section>
</div>

<script>
async function loadConfig() {
  const res = await fetch('/api/config');
  if (!res.ok) { location.href = '/login'; return; }
  const data = await res.json();

  renderPackages(data.packages);
  document.getElementById('formats-input').value = data.formats.join(',');
  document.getElementById('sort-select').value = data.pick_list_sort;

  if (data.is_admin) {
    document.getElementById('admin-section').style.display = 'block';
    loadWhitelist();
  }
}

function renderPackages(packages) {
  const list = document.getElementById('packages-list');
  list.innerHTML = '';
  if (!packages.length) {
    list.innerHTML = '<p style="color:var(--muted);font-size:0.85rem">No packages yet.</p>';
    return;
  }
  for (const p of packages) {
    const row = document.createElement('div');
    row.className = 'row';
    row.innerHTML = `<span>${p.color_group} → <code>${p.public_id}</code></span>`;
    const btn = document.createElement('button');
    btn.className = 'btn-secondary';
    btn.textContent = 'Remove';
    btn.onclick = () => removePackage(p.color_group);
    row.appendChild(btn);
    list.appendChild(row);
  }
}

async function addPackage() {
  const color_group = document.getElementById('pkg-color').value.trim();
  const public_id = document.getElementById('pkg-id').value.trim();
  if (!color_group || !public_id) return;
  const res = await fetch('/api/config/packages', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ color_group, public_id }),
  });
  const msg = document.getElementById('pkg-msg');
  msg.textContent = res.ok ? 'Added.' : 'Failed to add package.';
  msg.className = 'msg ' + (res.ok ? 'ok' : 'err');
  if (res.ok) {
    document.getElementById('pkg-color').value = '';
    document.getElementById('pkg-id').value = '';
    loadConfig();
  }
}

async function removePackage(color_group) {
  await fetch(`/api/config/packages/${encodeURIComponent(color_group)}`, { method: 'DELETE' });
  loadConfig();
}

async function saveFormats() {
  const formats = document.getElementById('formats-input').value.split(',').map(s => s.trim()).filter(Boolean);
  const res = await fetch('/api/config/formats', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ formats }),
  });
  const msg = document.getElementById('formats-msg');
  msg.textContent = res.ok ? 'Saved.' : 'Failed to save.';
  msg.className = 'msg ' + (res.ok ? 'ok' : 'err');
}

async function saveSort() {
  const sort_mode = document.getElementById('sort-select').value;
  const res = await fetch('/api/config/sort', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sort_mode }),
  });
  const msg = document.getElementById('sort-msg');
  msg.textContent = res.ok ? 'Saved.' : 'Failed to save.';
  msg.className = 'msg ' + (res.ok ? 'ok' : 'err');
}

async function syncNow() {
  const msg = document.getElementById('sync-msg');
  msg.textContent = 'Syncing…';
  msg.className = 'msg';
  const res = await fetch('/api/config/sync', { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (res.status === 429) {
    msg.textContent = data.detail || 'Sync throttled.';
    msg.className = 'msg err';
  } else if (res.ok) {
    msg.textContent = data.message || 'Sync complete.';
    msg.className = 'msg ok';
  } else {
    msg.textContent = 'Sync failed.';
    msg.className = 'msg err';
  }
}

async function loadWhitelist() {
  const res = await fetch('/api/admin/whitelist');
  if (!res.ok) return;
  const data = await res.json();
  const list = document.getElementById('whitelist-list');
  list.innerHTML = '';
  for (const row of data.whitelist) {
    const el = document.createElement('div');
    el.className = 'row';
    el.innerHTML = `<span>${row.email}${row.is_admin ? ' (admin)' : ''}</span>`;
    list.appendChild(el);
  }
}

async function addWhitelist() {
  const email = document.getElementById('wl-email').value.trim();
  const is_admin = document.getElementById('wl-admin').checked;
  if (!email) return;
  const res = await fetch('/api/admin/whitelist', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, is_admin }),
  });
  const msg = document.getElementById('wl-msg');
  msg.textContent = res.ok ? 'Added.' : 'Failed to add.';
  msg.className = 'msg ' + (res.ok ? 'ok' : 'err');
  if (res.ok) {
    document.getElementById('wl-email').value = '';
    document.getElementById('wl-admin').checked = false;
    loadWhitelist();
  }
}

async function loadWhoami() {
  try {
    const res = await fetch('/api/whoami');
    const data = await res.json();
    const el = document.getElementById('nav-identity');
    el.innerHTML = '';
    el.append(data.email + ' · ');
    const logout = document.createElement('span');
    logout.className = 'nav-logout';
    logout.textContent = 'Logout';
    logout.onclick = () => fetch('/logout', { method: 'POST' }).then(() => { location.href = '/login'; });
    el.appendChild(logout);
  } catch {}
}

loadConfig();
loadWhoami();
</script>
</body>
</html>
```

- [ ] **Step 4: Add the `GET /config` route and mount the router in `webapp/main.py`**

Add the import alongside the other `webapp.*` imports:

```python
from webapp.config import router as config_router
```

Mount it alongside the existing routers:

```python
app.include_router(auth_router)
app.include_router(images_router)
app.include_router(config_router)
```

Add the route itself near `app_page`:

```python
@app.get("/config")
async def config_page(cfg: Config = Depends(require_user)):
    return FileResponse(_STATIC_DIR / "config.html")
```

- [ ] **Step 5: Add a "Config" nav link to `webapp/static/app.html`**

In the `<nav>` block, add a link after the three tabs and before `#nav-identity`:

```html
<a class="tab" href="/config" style="text-decoration:none">Config</a>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_webapp_main_config.py -v`
Expected: All tests pass.

- [ ] **Step 7: Sanity-check both HTML files are still well-formed**

Run:
```bash
python -c "import html.parser; p = html.parser.HTMLParser(); p.feed(open('webapp/static/config.html', encoding='utf-8').read())"
python -c "import html.parser; p = html.parser.HTMLParser(); p.feed(open('webapp/static/app.html', encoding='utf-8').read())"
```
Expected: No exceptions.

- [ ] **Step 8: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 9: Commit**

```bash
git add webapp/main.py webapp/static/config.html webapp/static/app.html tests/test_webapp_main_config.py
git commit -m "feat: add config.html page, GET /config route, and nav link"
```

---

## Self-Review Notes

- **Spec coverage:** `require_admin` (Task 1), package/format/sort CRUD (Task 2), throttled sync (Task 3), admin-only whitelist add/list (Task 4), the page itself + routing + nav link (Task 5) — every piece of the spec is covered. Whitelist removal is explicitly out of scope and no task builds it.
- **Type consistency:** every route in `webapp/config.py` depends on `Config = Depends(require_user)` (or `require_admin`), matching the existing pattern in `webapp/main.py`'s routes.
- **No placeholders:** all HTML/CSS/JS and Python code is written out completely in each task.
