# Admin Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/admin` page (admin-only) showing the user roster, failed login attempts, and an activity/action log, and move whitelist management there from `/config`.

**Architecture:** Two new tables in the existing `registry.sqlite` (`failed_logins`, `request_log`), populated by a capture point in `webapp/auth.py` (failed logins) and a new Starlette HTTP middleware in `webapp/main.py` (every request). A new `webapp/admin.py` router exposes read endpoints plus the whitelist CRUD (moved from `webapp/config.py`), gated by the existing `require_admin` dependency. A new `webapp/static/admin.html` renders all of it; `config.html` loses its whitelist section.

**Tech Stack:** FastAPI, Starlette, SQLite (stdlib `sqlite3`), vanilla JS/HTML (matches existing `config.html`/`app.html` — no framework).

## Global Constraints

- Registry lives at `~/mtg_data/registry.sqlite`, accessed only via `api/users.py`'s `_registry_conn()` context manager — never open it directly elsewhere.
- All admin-only routes must depend on `webapp/deps.py`'s `require_admin`.
- Log retention: 30 days, pruned via `scripts/evict_cache.py`'s existing nightly run — no new cron job.
- Follow the existing test pattern: `tests/test_users.py` uses an autouse `isolated_registry` fixture that repoints `_REGISTRY_PATH`/`_USERS_DIR` at `tmp_path`; `tests/test_webapp_*.py` build a `TestClient` with a monkeypatched registry path and a hand-rolled `session_transaction()` helper (see any existing test file for the exact boilerplate — copy it, don't reinvent it).
- No pagination beyond a `limit` query param (default 200) for v1.

---

### Task 1: Registry schema + logging/query functions

**Files:**
- Modify: `api/users.py`
- Test: `tests/test_users.py`

**Interfaces:**
- Produces: `log_failed_login(email: str, reason: str) -> None`, `log_request(user_id: str | None, method: str, path: str, status: int) -> None`, `list_users() -> list[dict]` (each dict has `user_id`, `last_seen_at`, `last_synced_at`, `is_admin`), `list_failed_logins(limit: int = 200) -> list[dict]` (each dict has `email`, `reason`, `created_at`), `list_request_log(limit: int = 200) -> list[dict]` (each dict has `user_id`, `method`, `path`, `status`, `created_at`), `prune_logs(days: int = 30) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_users.py` (below the existing `test_remove_whitelisted_user_is_safe_for_unregistered_email`):

```python
def test_log_failed_login_and_list():
    users_mod.log_failed_login("Stranger@Example.com", "not_whitelisted")
    rows = users_mod.list_failed_logins()
    assert len(rows) == 1
    assert rows[0]["email"] == "stranger@example.com"
    assert rows[0]["reason"] == "not_whitelisted"


def test_list_failed_logins_respects_limit():
    for i in range(5):
        users_mod.log_failed_login(f"user{i}@example.com", "not_whitelisted")
    rows = users_mod.list_failed_logins(limit=2)
    assert len(rows) == 2


def test_log_request_and_list():
    users_mod.ensure_user("google:alice@example.com")
    users_mod.log_request("google:alice@example.com", "GET", "/api/config", 200)
    rows = users_mod.list_request_log()
    assert len(rows) == 1
    assert rows[0]["method"] == "GET"
    assert rows[0]["path"] == "/api/config"
    assert rows[0]["status"] == 200
    assert rows[0]["user_id"] == "google:alice@example.com"


def test_log_request_allows_null_user_id():
    users_mod.log_request(None, "GET", "/login", 302)
    rows = users_mod.list_request_log()
    assert rows[0]["user_id"] is None


def test_list_request_log_respects_limit():
    for i in range(5):
        users_mod.log_request(None, "GET", f"/path{i}", 200)
    rows = users_mod.list_request_log(limit=2)
    assert len(rows) == 2


def test_list_users_includes_admin_flag():
    users_mod.ensure_user("google:boss@example.com")
    users_mod.add_whitelisted_email("boss@example.com", is_admin=True)
    users_mod.ensure_user("google:alice@example.com")

    rows = {r["user_id"]: r for r in users_mod.list_users()}
    assert rows["google:boss@example.com"]["is_admin"] is True
    assert rows["google:alice@example.com"]["is_admin"] is False


def test_prune_logs_deletes_rows_older_than_cutoff():
    from api.users import _REGISTRY_PATH

    users_mod.log_failed_login("old@example.com", "not_whitelisted")
    users_mod.log_request(None, "GET", "/old", 200)

    conn = sqlite3.connect(_REGISTRY_PATH)
    conn.execute("UPDATE failed_logins SET created_at = datetime('now', '-40 days')")
    conn.execute("UPDATE request_log SET created_at = datetime('now', '-40 days')")
    conn.commit()
    conn.close()

    users_mod.log_failed_login("recent@example.com", "not_whitelisted")
    users_mod.log_request(None, "GET", "/recent", 200)

    users_mod.prune_logs(days=30)

    failed_emails = [r["email"] for r in users_mod.list_failed_logins()]
    assert "old@example.com" not in failed_emails
    assert "recent@example.com" in failed_emails

    paths = [r["path"] for r in users_mod.list_request_log()]
    assert "/old" not in paths
    assert "/recent" in paths
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_users.py -k "failed_login or request_log or list_users_includes_admin or prune_logs" -v`
Expected: FAIL with `AttributeError: module 'api.users' has no attribute 'log_failed_login'` (and similar for the others).

- [ ] **Step 3: Add the tables to `REGISTRY_SCHEMA`**

In `api/users.py`, extend `REGISTRY_SCHEMA` (around line 27-47) to add two more `CREATE TABLE IF NOT EXISTS` statements after the existing `whitelisted_emails` table:

```python
REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id        TEXT PRIMARY KEY,
    pick_list_sort TEXT NOT NULL DEFAULT 'colour',
    formats        TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_synced_at TEXT
);
CREATE TABLE IF NOT EXISTS user_packages (
    user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    color_group TEXT NOT NULL,
    public_id   TEXT NOT NULL,
    PRIMARY KEY (user_id, color_group)
);
CREATE TABLE IF NOT EXISTS whitelisted_emails (
    email    TEXT PRIMARY KEY,
    is_admin INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS failed_logins (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT NOT NULL,
    reason     TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS request_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT,
    method     TEXT NOT NULL,
    path       TEXT NOT NULL,
    status     INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_request_log_created_at ON request_log(created_at);
CREATE INDEX IF NOT EXISTS idx_failed_logins_created_at ON failed_logins(created_at);
"""
```

Since `_registry_conn()` runs `conn.executescript(REGISTRY_SCHEMA)` on every connection (line ~104), no separate migration function is needed — `CREATE TABLE IF NOT EXISTS` is enough, matching how `whitelisted_emails` itself was added.

- [ ] **Step 4: Add the functions**

Add near the bottom of `api/users.py` (after `list_profiles`):

```python
def log_failed_login(email: str, reason: str) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "INSERT INTO failed_logins (email, reason) VALUES (?, ?)",
            (email.lower().strip(), reason),
        )


def log_request(user_id: str | None, method: str, path: str, status: int) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "INSERT INTO request_log (user_id, method, path, status) VALUES (?, ?, ?, ?)",
            (user_id, method, path, status),
        )


def list_users() -> list[dict]:
    with _registry_conn() as conn:
        rows = conn.execute(
            """
            SELECT u.user_id, u.last_seen_at, u.last_synced_at,
                   COALESCE(w.is_admin, 0) AS is_admin
            FROM users u
            LEFT JOIN whitelisted_emails w
                ON u.user_id = 'google:' || w.email
            ORDER BY u.user_id
            """
        ).fetchall()
    return [
        {
            "user_id": r["user_id"],
            "last_seen_at": r["last_seen_at"],
            "last_synced_at": r["last_synced_at"],
            "is_admin": bool(r["is_admin"]),
        }
        for r in rows
    ]


def list_failed_logins(limit: int = 200) -> list[dict]:
    with _registry_conn() as conn:
        rows = conn.execute(
            "SELECT email, reason, created_at FROM failed_logins "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"email": r["email"], "reason": r["reason"], "created_at": r["created_at"]}
        for r in rows
    ]


def list_request_log(limit: int = 200) -> list[dict]:
    with _registry_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, method, path, status, created_at FROM request_log "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "user_id": r["user_id"],
            "method": r["method"],
            "path": r["path"],
            "status": r["status"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def prune_logs(days: int = 30) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "DELETE FROM request_log WHERE created_at < datetime('now', ? || ' days')",
            (f"-{days}",),
        )
        conn.execute(
            "DELETE FROM failed_logins WHERE created_at < datetime('now', ? || ' days')",
            (f"-{days}",),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_users.py -v`
Expected: all PASS (including the pre-existing tests — this confirms the schema change didn't break anything).

- [ ] **Step 6: Commit**

```bash
git add api/users.py tests/test_users.py
git commit -m "feat: add failed_logins/request_log tables and query functions"
```

---

### Task 2: Log failed logins from the OAuth callback

**Files:**
- Modify: `webapp/auth.py`
- Test: `tests/test_webapp_auth.py`

**Interfaces:**
- Consumes: `api.users.log_failed_login(email: str, reason: str) -> None` (Task 1).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_webapp_auth.py`:

```python
def test_callback_rejects_non_whitelisted_email_logs_failed_login(app_client):
    fake_token = {"userinfo": {"email": "stranger@example.com"}}
    with patch.object(
        auth_mod.oauth.google, "authorize_access_token", new=AsyncMock(return_value=fake_token)
    ):
        app_client.get("/auth/callback", follow_redirects=False)

    from api.users import list_failed_logins
    rows = list_failed_logins()
    assert len(rows) == 1
    assert rows[0]["email"] == "stranger@example.com"
    assert rows[0]["reason"] == "not_whitelisted"


def test_callback_oauth_error_logs_failed_login(app_client):
    with patch.object(
        auth_mod.oauth.google, "authorize_access_token",
        new=AsyncMock(side_effect=OAuthError(error="mismatching_state")),
    ):
        app_client.get("/auth/callback", follow_redirects=False)

    from api.users import list_failed_logins
    rows = list_failed_logins()
    assert len(rows) == 1
    assert rows[0]["reason"] == "oauth_error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_auth.py -k "logs_failed_login" -v`
Expected: FAIL — `list_failed_logins()` returns an empty list (no logging happens yet).

- [ ] **Step 3: Add logging calls in `auth_callback`**

In `webapp/auth.py`, update the import and the callback body:

```python
from api.users import ensure_user, is_whitelisted, log_failed_login
```

```python
@router.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        log_failed_login("", "oauth_error")
        return RedirectResponse(url="/login", status_code=302)
    userinfo = token.get("userinfo") or {}
    email = (userinfo.get("email") or "").lower()

    if not email or not is_whitelisted(email):
        log_failed_login(email, "not_whitelisted")
        return HTMLResponse(
            "<h1>Not authorized</h1><p>This email is not on the whitelist. "
            "Contact the owner.</p>",
            status_code=403,
        )

    user_id = f"google:{email}"
    ensure_user(user_id)
    request.session["user_id"] = user_id
    return RedirectResponse(url="/app", status_code=302)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_auth.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp/auth.py tests/test_webapp_auth.py
git commit -m "feat: log failed login attempts from the OAuth callback"
```

---

### Task 3: Request-logging middleware

**Files:**
- Modify: `webapp/main.py`
- Test: `tests/test_webapp_main.py`

**Interfaces:**
- Consumes: `api.users.log_request(user_id, method, path, status) -> None` (Task 1).

Note: the design spec mentioned excluding `/static/*` paths, but this app has no generic static-file mount (HTML pages are served individually via `FileResponse` at `/app`/`/config`/`/admin`, not through a `/static` route). The one genuinely noisy, asset-like route is `/images/{set_code}/{collector_number}` (card image proxy/cache in `webapp/images.py`) — exclude that prefix instead.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_webapp_main.py`:

```python
def test_activity_is_logged_for_authenticated_requests(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, list_request_log
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        c.get("/api/whoami")

    rows = list_request_log()
    matching = [r for r in rows if r["path"] == "/api/whoami"]
    assert len(matching) == 1
    assert matching[0]["method"] == "GET"
    assert matching[0]["status"] == 200
    assert matching[0]["user_id"] == "google:alice@example.com"


def test_activity_log_records_anonymous_requests(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import list_request_log

    client.get("/api/whoami", follow_redirects=False)

    rows = list_request_log()
    matching = [r for r in rows if r["path"] == "/api/whoami"]
    assert len(matching) == 1
    assert matching[0]["user_id"] is None
    assert matching[0]["status"] in (302, 307)


def test_image_route_requests_are_not_logged(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import list_request_log

    client.get("/images/badset/1")  # invalid collector_number-ish but exercises the route

    rows = list_request_log()
    assert all(not r["path"].startswith("/images/") for r in rows)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_main.py -k "activity or image_route" -v`
Expected: FAIL — `list_request_log()` returns an empty list (no middleware yet).

- [ ] **Step 3: Add the middleware**

In `webapp/main.py`, update the import and add the middleware function right after the `app.include_router(config_router)` line:

```python
from api.users import get_user_config, log_request, seed_owner_whitelist
```

```python
@app.middleware("http")
async def _log_requests(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/images/"):
        user_id = request.session.get("user_id")
        try:
            log_request(user_id, request.method, request.url.path, response.status_code)
        except Exception:
            pass
    return response
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_main.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp/main.py tests/test_webapp_main.py
git commit -m "feat: log all HTTP requests to the registry for the admin activity log"
```

---

### Task 4: `webapp/admin.py` router — roster, failed logins, activity, whitelist (moved)

**Files:**
- Create: `webapp/admin.py`
- Modify: `webapp/config.py` (remove the three `/api/admin/whitelist*` routes and their now-unused imports)
- Modify: `webapp/main.py` (register the new router)
- Create: `tests/test_webapp_admin.py`
- Modify: `tests/test_webapp_config.py` (remove the whitelist tests that moved)

**Interfaces:**
- Consumes: `api.users.list_users`, `list_failed_logins`, `list_request_log`, `add_whitelisted_email`, `remove_whitelisted_user` (Task 1 + pre-existing), `webapp.deps.require_admin`.
- Produces: `webapp.admin.router` (a `fastapi.APIRouter`), mounted in `webapp/main.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_webapp_admin.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_admin.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'webapp.admin'`.

- [ ] **Step 3: Create `webapp/admin.py`**

```python
"""Admin-only routes: user roster, failed logins, activity log, whitelist management."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.users import (
    add_whitelisted_email,
    list_failed_logins,
    list_request_log,
    list_users,
    remove_whitelisted_user,
)
from mtg_manager.config import Config
from webapp.deps import require_admin

router = APIRouter()


class WhitelistIn(BaseModel):
    email: str
    is_admin: bool = False


@router.get("/api/admin/users")
async def get_users(cfg: Config = Depends(require_admin)):
    return {"users": list_users()}


@router.get("/api/admin/failed-logins")
async def get_failed_logins(limit: int = 200, cfg: Config = Depends(require_admin)):
    return {"failed_logins": list_failed_logins(limit=limit)}


@router.get("/api/admin/activity")
async def get_activity(limit: int = 200, cfg: Config = Depends(require_admin)):
    return {"activity": list_request_log(limit=limit)}


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


@router.delete("/api/admin/whitelist/{email}")
async def remove_from_whitelist(email: str, cfg: Config = Depends(require_admin)):
    try:
        remove_whitelisted_user(email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}
```

- [ ] **Step 4: Remove the moved routes from `webapp/config.py`**

Delete the `@router.get("/api/admin/whitelist")`, `@router.post("/api/admin/whitelist")`, and `@router.delete("/api/admin/whitelist/{email}")` functions (lines 136-164) from `webapp/config.py`.

Update its imports: remove `add_whitelisted_email`, `is_whitelist_admin` is still used by `_is_admin`, `remove_whitelisted_user` from the `api.users` import line, and remove `require_admin` from the `webapp.deps` import line (only `require_user` is still used):

```python
from api.users import (
    add_package,
    is_owner,
    is_whitelist_admin,
    list_packages,
    list_profiles,
    mark_synced,
    minutes_since_last_sync,
    remove_package,
    set_formats,
    set_profile,
    set_sort,
)
from mtg_manager.config import Config
from webapp.deps import require_user
```

Also delete the now-unused `WhitelistIn` class from `webapp/config.py`.

- [ ] **Step 5: Move the whitelist tests out of `tests/test_webapp_config.py`**

Delete these test functions from `tests/test_webapp_config.py` (they're now covered by the equivalent tests in `tests/test_webapp_admin.py`): `test_whitelist_routes_require_admin`, `test_admin_can_list_and_add_whitelist`, `test_admin_can_remove_whitelisted_user`, `test_admin_cannot_remove_owner`, `test_non_admin_cannot_remove_whitelisted_user`.

- [ ] **Step 6: Register the router in `webapp/main.py`**

```python
from webapp.admin import router as admin_router
```

```python
app.include_router(admin_router)
```

(add alongside the other `app.include_router(...)` calls)

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_webapp_admin.py tests/test_webapp_config.py tests/test_webapp_main.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add webapp/admin.py webapp/config.py webapp/main.py tests/test_webapp_admin.py tests/test_webapp_config.py
git commit -m "feat: add admin router with roster/failed-login/activity endpoints, move whitelist routes"
```

---

### Task 5: Wire log pruning into the eviction cron

**Files:**
- Modify: `scripts/evict_cache.py`
- Test: `tests/test_evict_cache.py`

**Interfaces:**
- Consumes: `api.users.prune_logs(days: int = 30) -> None` (Task 1).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_evict_cache.py`:

```python
def test_evict_prunes_logs(tmp_path, monkeypatch):
    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path)

    import scripts.evict_cache as ec
    monkeypatch.setattr(ec, "_USERS_DIR", tmp_path)
    monkeypatch.setattr(ec, "list_users_for_eviction", lambda threshold_days=7: [])

    u.log_failed_login("old@example.com", "not_whitelisted")
    import sqlite3
    conn = sqlite3.connect(u._REGISTRY_PATH)
    conn.execute("UPDATE failed_logins SET created_at = datetime('now', '-40 days')")
    conn.commit()
    conn.close()

    ec.evict(threshold_days=7)

    assert u.list_failed_logins() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_evict_cache.py -k test_evict_prunes_logs -v`
Expected: FAIL — the row is still present because `evict()` doesn't call `prune_logs` yet.

- [ ] **Step 3: Call `prune_logs` from `evict()`**

In `scripts/evict_cache.py`, update the import and add a call at the top of `evict()`:

```python
from api.users import list_users_for_eviction, prune_logs, _USERS_DIR, _safe_filename
```

```python
def evict(threshold_days: int = 7, dry_run: bool = False) -> None:
    if dry_run:
        logger.info("DRY RUN — would prune failed_logins/request_log rows older than 30 days.")
    else:
        prune_logs(days=30)
        logger.info("Pruned failed_logins/request_log rows older than 30 days.")

    stale_ids = list_users_for_eviction(threshold_days)
    ...
```

(keep the rest of the function body unchanged — this only adds the new block before the existing `stale_ids = ...` line)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_evict_cache.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/evict_cache.py tests/test_evict_cache.py
git commit -m "feat: prune old failed-login and request-log rows during nightly eviction"
```

---

### Task 6: `/admin` page UI, and strip whitelist UI out of `/config`

**Files:**
- Create: `webapp/static/admin.html`
- Modify: `webapp/main.py` (serve the new page)
- Modify: `webapp/static/config.html` (remove whitelist section/JS, keep nav link)

**Interfaces:**
- Consumes: `GET /api/config` (existing, for `is_admin`), `GET /api/admin/users`, `GET /api/admin/failed-logins`, `GET /api/admin/activity`, `GET/POST/DELETE /api/admin/whitelist[...]` (Task 4).

This task is UI-only glue code with no unit tests of its own (no existing precedent for testing static HTML/JS in this repo — `config.html`/`app.html` aren't covered by `pytest` either). Verify manually per Step 4.

- [ ] **Step 1: Add the `/admin` route in `webapp/main.py`**

```python
@app.get("/admin")
async def admin_page(cfg: Config = Depends(require_admin)):
    return FileResponse(_STATIC_DIR / "admin.html")
```

(add this near the existing `/config` route; import `require_admin` alongside the existing `require_user` import from `webapp.deps`)

- [ ] **Step 2: Create `webapp/static/admin.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MTG Collection — Admin</title>
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

  .page { padding: 24px; max-width: 1000px; margin: 0 auto; }
  section { background: var(--surface); border: 1px solid var(--surface2); border-radius: var(--radius); padding: 20px; margin-bottom: 20px; }
  h2 { font-size: 1rem; color: var(--gold); margin-bottom: 14px; }
  label { display: block; font-size: 0.85rem; color: var(--muted); margin-bottom: 4px; }
  input { background: var(--bg); border: 1px solid var(--surface2); color: var(--text); padding: 8px 12px; border-radius: var(--radius); font-size: 0.9rem; margin-bottom: 10px; width: 100%; }
  .btn { padding: 8px 16px; background: var(--accent); color: white; border: none; border-radius: var(--radius); font-size: 0.9rem; font-weight: 600; cursor: pointer; }
  .btn:hover { background: #c73652; }
  .btn-secondary { padding: 6px 12px; background: var(--surface2); color: var(--text); border: none; border-radius: var(--radius); font-size: 0.82rem; cursor: pointer; }
  .btn-toggle { padding: 6px 12px; background: var(--bg); color: var(--muted); border: 1px solid var(--surface2); border-radius: var(--radius); font-size: 0.82rem; cursor: pointer; }
  .btn-toggle.active { color: var(--text); border-color: var(--accent); }
  .row { display: flex; gap: 8px; align-items: center; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--surface2); }
  .row:last-child { border-bottom: none; }
  .msg { font-size: 0.85rem; margin-top: 8px; }
  .msg.ok { color: var(--green); }
  .msg.err { color: var(--red); }
  .inline-form { display: flex; gap: 8px; align-items: flex-end; }
  .inline-form input { margin-bottom: 0; }
  .checkbox-row { display: flex; align-items: center; gap: 6px; }
  .checkbox-row input { width: auto; margin-bottom: 0; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--surface2); }
  th { color: var(--muted); font-weight: 600; }
  .empty { color: var(--muted); font-size: 0.85rem; }
</style>
</head>
<body>

<nav>
  <div class="nav-brand">⚔ MTG Collection</div>
  <a class="nav-link" href="/app#collection">Collection</a>
  <a class="nav-link" href="/app#decks">Decks</a>
  <a class="nav-link" href="/app#missing">Missing</a>
  <a class="nav-link" href="/config">Config</a>
  <a class="nav-link" href="/admin">Admin</a>
  <div class="nav-meta" id="nav-identity">Loading…</div>
</nav>

<div class="page">
  <section>
    <h2>Users</h2>
    <table id="users-table">
      <thead><tr><th>Email</th><th>Admin</th><th>Last seen</th><th>Last synced</th></tr></thead>
      <tbody></tbody>
    </table>
  </section>

  <section>
    <h2>Whitelist</h2>
    <div id="whitelist-list"></div>
    <div class="inline-form" style="margin-top:12px">
      <div style="flex:1"><label>Email</label><input id="wl-email" placeholder="someone@example.com"></div>
      <div class="checkbox-row"><input type="checkbox" id="wl-admin"><label style="margin:0">Admin</label></div>
      <button class="btn" onclick="addWhitelist()">Add</button>
    </div>
    <div id="wl-msg" class="msg"></div>
  </section>

  <section>
    <h2>Failed logins</h2>
    <table id="failed-logins-table">
      <thead><tr><th>Time</th><th>Email</th><th>Reason</th></tr></thead>
      <tbody></tbody>
    </table>
  </section>

  <section>
    <h2>Activity</h2>
    <div style="margin-bottom:10px">
      <button class="btn-toggle active" id="activity-toggle" onclick="setActivityFilter('activity')">Activity</button>
      <button class="btn-toggle" id="actions-toggle" onclick="setActivityFilter('actions')">Actions</button>
    </div>
    <table id="activity-table">
      <thead><tr><th>Time</th><th>User</th><th>Action</th><th>Status</th></tr></thead>
      <tbody></tbody>
    </table>
  </section>
</div>

<script>
const ACTION_LABELS = [
  { pattern: /^POST \/api\/config\/packages$/, label: 'Added Moxfield package' },
  { pattern: /^DELETE \/api\/config\/packages\//, label: 'Removed Moxfield package' },
  { pattern: /^POST \/api\/config\/sort$/, label: 'Changed pick list sort' },
  { pattern: /^POST \/api\/config\/formats$/, label: 'Changed tracked formats' },
  { pattern: /^POST \/api\/config\/profile$/, label: 'Updated profile' },
  { pattern: /^POST \/api\/config\/sync$/, label: 'Ran sync' },
  { pattern: /^POST \/api\/admin\/whitelist$/, label: 'Added user to whitelist' },
  { pattern: /^DELETE \/api\/admin\/whitelist\//, label: 'Removed whitelisted user' },
  { pattern: /^GET \/api\/config$/, label: 'Loaded config' },
  { pattern: /^GET \/api\/collection$/, label: 'Loaded collection' },
  { pattern: /^GET \/api\/decks$/, label: 'Loaded decks' },
  { pattern: /^GET \/api\/whoami$/, label: 'Checked session' },
];

function friendlyLabel(method, path) {
  const key = `${method} ${path}`;
  for (const { pattern, label } of ACTION_LABELS) {
    if (pattern.test(key)) return label;
  }
  return key;
}

let allActivity = [];
let activityFilter = 'activity';

async function loadAdmin() {
  const [usersRes, failedRes, activityRes] = await Promise.all([
    fetch('/api/admin/users'),
    fetch('/api/admin/failed-logins'),
    fetch('/api/admin/activity'),
  ]);
  if (!usersRes.ok) { location.href = '/login'; return; }

  renderUsers((await usersRes.json()).users);
  renderFailedLogins((await failedRes.json()).failed_logins);
  allActivity = (await activityRes.json()).activity;
  renderActivity();
  loadWhitelist();
}

function renderUsers(users) {
  const tbody = document.querySelector('#users-table tbody');
  tbody.innerHTML = '';
  for (const u of users) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${u.user_id}</td><td>${u.is_admin ? '✓' : ''}</td><td>${u.last_seen_at || ''}</td><td>${u.last_synced_at || ''}</td>`;
    tbody.appendChild(tr);
  }
}

function renderFailedLogins(rows) {
  const tbody = document.querySelector('#failed-logins-table tbody');
  tbody.innerHTML = '';
  for (const r of rows) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${r.created_at}</td><td>${r.email}</td><td>${r.reason}</td>`;
    tbody.appendChild(tr);
  }
}

function setActivityFilter(filter) {
  activityFilter = filter;
  document.getElementById('activity-toggle').classList.toggle('active', filter === 'activity');
  document.getElementById('actions-toggle').classList.toggle('active', filter === 'actions');
  renderActivity();
}

function renderActivity() {
  const tbody = document.querySelector('#activity-table tbody');
  tbody.innerHTML = '';
  const rows = activityFilter === 'actions'
    ? allActivity.filter(r => ['POST', 'PUT', 'DELETE'].includes(r.method))
    : allActivity;
  for (const r of rows) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${r.created_at}</td><td>${r.user_id || 'anonymous'}</td><td>${friendlyLabel(r.method, r.path)}</td><td>${r.status}</td>`;
    tbody.appendChild(tr);
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
    const label = document.createElement('span');
    label.textContent = row.email + (row.is_admin ? ' (admin)' : '');
    el.appendChild(label);
    const btn = document.createElement('button');
    btn.className = 'btn-secondary';
    btn.textContent = 'Remove';
    btn.onclick = () => removeWhitelistedUser(row.email);
    el.appendChild(btn);
    list.appendChild(el);
  }
}

async function removeWhitelistedUser(email) {
  if (!confirm(`Permanently delete ${email}'s account and collection? This cannot be undone.`)) return;
  const res = await fetch(`/api/admin/whitelist/${encodeURIComponent(email)}`, { method: 'DELETE' });
  if (res.ok) {
    loadAdmin();
  } else {
    const data = await res.json().catch(() => ({}));
    alert(data.detail || 'Failed to remove user.');
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

loadAdmin();
loadWhoami();
</script>
</body>
</html>
```

- [ ] **Step 3: Strip the whitelist section out of `webapp/static/config.html`, add the Admin nav link**

Remove the `<section id="admin-section" ...>...</section>` block (lines 115-124).

Add an Admin nav link that's hidden by default, shown when `is_admin` is true — change the `<nav>` block:

```html
<nav>
  <div class="nav-brand">⚔ MTG Collection</div>
  <a class="nav-link" href="/app#collection">Collection</a>
  <a class="nav-link" href="/app#decks">Decks</a>
  <a class="nav-link" href="/app#missing">Missing</a>
  <a class="nav-link" href="/config">Config</a>
  <a class="nav-link" id="admin-nav-link" href="/admin" style="display:none">Admin</a>
  <div class="nav-meta" id="nav-identity">Loading…</div>
</nav>
```

In the `<script>` block, replace the `if (data.is_admin) { ... }` branch inside `loadConfig()`:

```javascript
  if (data.is_admin) {
    document.getElementById('admin-nav-link').style.display = '';
  }
```

Remove the now-unused `loadWhitelist()`, `removeWhitelistedUser()`, and `addWhitelist()` functions and the whitelist call sites (the entire block from `async function loadWhitelist()` through the end of `async function addWhitelist()`).

- [ ] **Step 4: Manual verification**

Run the app locally (`uvicorn webapp.main:app --reload`, with a valid `.env` for Google OAuth or by temporarily using an existing dev session) and confirm:
1. A non-admin visiting `/admin` directly gets redirected/rejected (403 or redirect via `NotAuthenticated`).
2. An admin sees `/admin` with populated Users, Whitelist, Failed logins (trigger one by visiting `/auth/callback` unauthenticated, or just check the table renders empty), and Activity sections.
3. The Activity/Actions toggle actually filters rows.
4. `/config` no longer shows a whitelist section, but shows an "Admin" nav link for admins only.
5. Adding/removing a whitelist entry from `/admin` works exactly as it did before on `/config`.

- [ ] **Step 5: Commit**

```bash
git add webapp/main.py webapp/static/admin.html webapp/static/config.html
git commit -m "feat: add /admin page UI, remove whitelist section from /config"
```

---

### Task 7: Full test suite sanity check

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `pytest`
Expected: all tests PASS, no regressions from the moved/removed whitelist tests or the new middleware affecting unrelated tests (the middleware wraps every request in the real app, so this step catches any interaction the earlier task-by-task test runs might have missed, e.g. in `tests/test_webapp_main.py`'s pre-existing tests).

- [ ] **Step 2: No commit needed** — this is a verification-only task. If anything fails, fix it in the relevant task's files and re-run.
