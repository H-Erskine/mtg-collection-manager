# Web Auth Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a FastAPI web service with Google OAuth login, backed by a generalized (Discord + web) multi-user registry, so a whitelisted Google account can log in locally and receive their own live collection/decks data — no static JSON export, no manual owner intervention.

**Architecture:** `api/users.py`'s registry (`~/mtg_data/registry.sqlite`) is generalized from a `discord_id`-keyed table to a prefixed `user_id` (`discord:<id>` / `google:<email>`) so Discord and web share one registry. A new `webapp/` package (FastAPI + Authlib + Starlette sessions) authenticates against a `whitelisted_emails` table and serves per-user data live via functions extracted from `web/export.py`. The existing Discord bot and static-export site are untouched in behavior — only the registry's internal key format changes, transparently to `bot.py`.

**Tech Stack:** FastAPI, uvicorn, Authlib (Google OAuth), Starlette `SessionMiddleware`, existing `mtg_manager`/`api` packages, pytest.

## Global Constraints

- No deployment/VM/nginx/TLS changes in this plan — everything runs and is tested locally on branch `feature/web-multiuser-auth`. (Spec: "Deployment (later, not part of local dev)".)
- Discord bot behavior must be unchanged from a user's perspective — only internal registry key format changes. (Spec: "Discord bot is not being retired.")
- No identity linking between Discord and web accounts — `discord:<id>` and `google:<email>` are always distinct registry rows. (Spec: "Identity is not linked across surfaces.")
- No real network calls to Google in automated tests — OAuth exchange is mocked. (Spec: "Auth routes are integration-tested ... no real Google calls in CI.")
- Meta-decklist comparison is not exposed via `webapp` in this or any future sub-project. (Spec: "Descoped from self-service.")

---

## Task 1: Generalize registry to prefixed `user_id`

**Files:**
- Modify: `api/users.py`
- Modify: `tests/test_users.py`
- Modify: `api/bot.py` (single mechanical change, see Task 1 Step 5)

**Interfaces:**
- Produces: `is_owner(user_id: str) -> bool` (now checks both `discord:<OWNER_DISCORD_ID>` and `google:<OWNER_GOOGLE_EMAIL>`), and every existing registry function (`ensure_user`, `is_registered`, `get_user_config`, `add_package`, `remove_package`, `list_packages`, `set_sort`, `set_formats`, `mark_seen`, `mark_synced`, `minutes_since_last_sync`, `list_users_for_eviction`) now takes a prefixed `user_id: str` instead of a bare `discord_id: str`. Signatures and return types are otherwise unchanged.
- Consumes: nothing new — this task only changes the key format of existing functions.

- [ ] **Step 1: Rewrite `tests/test_users.py` to use prefixed `user_id` values**

Replace the entire file contents with:

```python
"""Tests for api/users.py registry CRUD."""

import sqlite3

import pytest

# Point registry + user DBs at a temp dir for every test
@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "users")
    # Ensure no owner leak between tests
    monkeypatch.delenv("OWNER_DISCORD_ID", raising=False)
    monkeypatch.delenv("OWNER_GOOGLE_EMAIL", raising=False)
    yield


import api.users as users_mod


def test_ensure_user_creates_row():
    users_mod.ensure_user("discord:111")
    assert users_mod.is_registered("discord:111")


def test_ensure_user_idempotent():
    users_mod.ensure_user("discord:111")
    users_mod.ensure_user("discord:111")  # should not raise
    assert users_mod.is_registered("discord:111")


def test_not_registered_by_default():
    assert not users_mod.is_registered("discord:999")


def test_get_user_config_returns_none_for_unknown():
    assert users_mod.get_user_config("discord:999") is None


def test_get_user_config_returns_config_for_known(tmp_path):
    users_mod.ensure_user("discord:222")
    users_mod.add_package("discord:222", "Red", "abc123")
    cfg = users_mod.get_user_config("discord:222")
    assert cfg is not None
    assert len(cfg.packages) == 1
    assert cfg.packages[0].color_group == "Red"
    assert cfg.packages[0].public_id == "abc123"


def test_add_and_list_packages():
    users_mod.ensure_user("discord:333")
    users_mod.add_package("discord:333", "White", "w1")
    users_mod.add_package("discord:333", "Blue", "u1")
    pkgs = users_mod.list_packages("discord:333")
    assert ("Blue", "u1") in pkgs
    assert ("White", "w1") in pkgs


def test_add_package_upserts():
    users_mod.ensure_user("discord:333")
    users_mod.add_package("discord:333", "White", "w1")
    users_mod.add_package("discord:333", "White", "w2")  # update
    pkgs = dict(users_mod.list_packages("discord:333"))
    assert pkgs["White"] == "w2"


def test_remove_package():
    users_mod.ensure_user("discord:444")
    users_mod.add_package("discord:444", "Green", "g1")
    removed = users_mod.remove_package("discord:444", "Green")
    assert removed
    assert users_mod.list_packages("discord:444") == []


def test_remove_package_case_insensitive():
    users_mod.ensure_user("discord:444")
    users_mod.add_package("discord:444", "Green", "g1")
    assert users_mod.remove_package("discord:444", "green")


def test_remove_nonexistent_package_returns_false():
    users_mod.ensure_user("discord:444")
    assert not users_mod.remove_package("discord:444", "Nonexistent")


def test_set_sort_valid():
    users_mod.ensure_user("discord:555")
    users_mod.set_sort("discord:555", "cmc")
    cfg = users_mod.get_user_config("discord:555")
    assert cfg.pick_list_sort == "cmc"


def test_set_sort_invalid():
    users_mod.ensure_user("discord:555")
    with pytest.raises(ValueError):
        users_mod.set_sort("discord:555", "random")


def test_set_formats():
    users_mod.ensure_user("discord:666")
    users_mod.set_formats("discord:666", ["modern", "legacy"])
    cfg = users_mod.get_user_config("discord:666")
    assert "modern" in cfg.formats
    assert "legacy" in cfg.formats


def test_mark_seen_updates_timestamp():
    users_mod.ensure_user("discord:777")
    users_mod.mark_seen("discord:777")  # should not raise


def test_mark_synced_and_throttle():
    users_mod.ensure_user("discord:888")
    assert users_mod.minutes_since_last_sync("discord:888") is None
    users_mod.mark_synced("discord:888")
    mins = users_mod.minutes_since_last_sync("discord:888")
    assert mins is not None
    assert mins < 1  # just synced


def test_minutes_since_sync_returns_none_for_unknown():
    assert users_mod.minutes_since_last_sync("discord:nobody") is None


def test_list_users_for_eviction(tmp_path):
    users_mod.ensure_user("discord:active")
    users_mod.ensure_user("discord:stale")

    # Backdate stale user's last_seen_at
    from api.users import _REGISTRY_PATH
    conn = sqlite3.connect(_REGISTRY_PATH)
    conn.execute(
        "UPDATE users SET last_seen_at = datetime('now', '-8 days') WHERE user_id = 'discord:stale'"
    )
    conn.commit()
    conn.close()

    evictable = users_mod.list_users_for_eviction(threshold_days=7)
    assert "discord:stale" in evictable
    assert "discord:active" not in evictable


def test_is_owner_matches_discord_env(monkeypatch):
    monkeypatch.setenv("OWNER_DISCORD_ID", "42")
    assert users_mod.is_owner("discord:42")
    assert not users_mod.is_owner("discord:43")


def test_is_owner_matches_google_env(monkeypatch):
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "owner@example.com")
    assert users_mod.is_owner("google:owner@example.com")
    assert not users_mod.is_owner("google:someone-else@example.com")


def test_is_owner_unset(monkeypatch):
    monkeypatch.delenv("OWNER_DISCORD_ID", raising=False)
    monkeypatch.delenv("OWNER_GOOGLE_EMAIL", raising=False)
    assert not users_mod.is_owner("discord:42")


def test_get_user_config_db_path_uses_users_dir(tmp_path):
    users_mod.ensure_user("discord:999")
    cfg = users_mod.get_user_config("discord:999")
    assert cfg is not None
    assert "999" in str(cfg.db_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_users.py -v`
Expected: Multiple FAILs — `is_registered("discord:111")` returns `False` because the current schema stores bare `"discord:111"` as a literal (unprefixed) key that was never inserted, and `test_is_owner_matches_google_env` fails with `AttributeError` since `OWNER_GOOGLE_EMAIL` support doesn't exist yet.

- [ ] **Step 3: Update `api/users.py` schema and connection setup**

Replace the module's schema constant and `_registry_conn` with:

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
"""

VALID_SORT_OPTIONS = ("colour", "alphabetical", "set", "cmc")


def _migrate_legacy_discord_id(conn: sqlite3.Connection) -> None:
    """One-time rename of discord_id -> user_id for pre-existing registries."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    if "users" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "discord_id" in cols and "user_id" not in cols:
            conn.execute("ALTER TABLE users RENAME COLUMN discord_id TO user_id")
            conn.execute(
                "UPDATE users SET user_id = 'discord:' || user_id "
                "WHERE user_id NOT LIKE '%:%'"
            )

    if "user_packages" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(user_packages)").fetchall()}
        if "discord_id" in cols and "user_id" not in cols:
            conn.execute("ALTER TABLE user_packages RENAME COLUMN discord_id TO user_id")
            conn.execute(
                "UPDATE user_packages SET user_id = 'discord:' || user_id "
                "WHERE user_id NOT LIKE '%:%'"
            )


@contextmanager
def _registry_conn():
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_REGISTRY_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        _migrate_legacy_discord_id(conn)
        conn.executescript(REGISTRY_SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

- [ ] **Step 4: Rename `user_id` parameter throughout `api/users.py` and generalize `is_owner`**

In every remaining function, rename the parameter `discord_id` to `user_id` and update the SQL/bind-variables and docstrings accordingly (e.g. `ensure_user`, `is_registered`, `get_user_config`, `add_package`, `remove_package`, `list_packages`, `set_sort`, `set_formats`, `mark_seen`, `mark_synced`, `minutes_since_last_sync`). The SQL column name changes from `discord_id` to `user_id` in every query in these functions (e.g. `"SELECT 1 FROM users WHERE discord_id = ?"` becomes `"SELECT 1 FROM users WHERE user_id = ?"`).

Replace `is_owner`:

```python
def is_owner(user_id: str) -> bool:
    owner_discord_id = os.environ.get("OWNER_DISCORD_ID")
    if owner_discord_id is not None and user_id == f"discord:{owner_discord_id}":
        return True
    owner_google_email = os.environ.get("OWNER_GOOGLE_EMAIL")
    if owner_google_email is not None and user_id == f"google:{owner_google_email}":
        return True
    return False
```

And in `get_user_config`, update the `_USERS_DIR` path construction from `_USERS_DIR / f"{discord_id}.sqlite"` to `_USERS_DIR / f"{user_id}.sqlite"` (the prefix stays part of the filename, e.g. `discord:12345.sqlite` — this is a new local dev DB, not a rename of any existing file, so no migration needed for that path).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_users.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Update `api/bot.py` to pass prefixed Discord IDs**

The variable `discord_id` in `bot.py` is only ever used as an argument to `users.*` functions — never displayed or compared to a raw Discord snowflake elsewhere. Change its construction so it's already prefixed at the point of assignment. Replace all 8 occurrences of:

```python
    discord_id = str(interaction.user.id)
```

with:

```python
    discord_id = f"discord:{interaction.user.id}"
```

(Use a single `replace_all` edit — the string is identical at every call site: lines 157, 196, 223, 250, 280, 302, 325, 364.)

- [ ] **Step 7: Verify the bot module still imports cleanly**

Run: `python -c "import api.bot"`
Expected: No errors (the Discord token/env vars are only required at runtime when `client.run()` is called, not at import time).

- [ ] **Step 8: Commit**

```bash
git add api/users.py api/bot.py tests/test_users.py
git commit -m "refactor: generalize registry to prefixed user_id (discord:/google:)"
```

---

## Task 2: Add `whitelisted_emails` table and owner seeding

**Files:**
- Modify: `api/users.py`
- Modify: `tests/test_users.py`

**Interfaces:**
- Consumes: `_registry_conn()` from Task 1.
- Produces: `is_whitelisted(email: str) -> bool`, `add_whitelisted_email(email: str, is_admin: bool = False) -> None`, `is_whitelist_admin(email: str) -> bool`, `seed_owner_whitelist() -> None`.

- [ ] **Step 1: Add failing tests to `tests/test_users.py`**

Append to the file:

```python
def test_email_not_whitelisted_by_default():
    assert not users_mod.is_whitelisted("nobody@example.com")


def test_add_whitelisted_email():
    users_mod.add_whitelisted_email("alice@example.com")
    assert users_mod.is_whitelisted("alice@example.com")
    assert not users_mod.is_whitelist_admin("alice@example.com")


def test_add_whitelisted_email_case_insensitive():
    users_mod.add_whitelisted_email("Alice@Example.com")
    assert users_mod.is_whitelisted("alice@example.com")


def test_add_whitelisted_email_as_admin():
    users_mod.add_whitelisted_email("boss@example.com", is_admin=True)
    assert users_mod.is_whitelist_admin("boss@example.com")


def test_add_whitelisted_email_upserts_admin_flag():
    users_mod.add_whitelisted_email("carol@example.com", is_admin=False)
    users_mod.add_whitelisted_email("carol@example.com", is_admin=True)
    assert users_mod.is_whitelist_admin("carol@example.com")


def test_seed_owner_whitelist_noop_when_unset(monkeypatch):
    monkeypatch.delenv("OWNER_GOOGLE_EMAIL", raising=False)
    users_mod.seed_owner_whitelist()
    assert not users_mod.is_whitelisted("owner@example.com")


def test_seed_owner_whitelist_adds_admin(monkeypatch):
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "owner@example.com")
    users_mod.seed_owner_whitelist()
    assert users_mod.is_whitelisted("owner@example.com")
    assert users_mod.is_whitelist_admin("owner@example.com")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_users.py -v -k whitelist`
Expected: FAIL with `AttributeError: module 'api.users' has no attribute 'is_whitelisted'` (and similarly for the other new functions).

- [ ] **Step 3: Add `whitelisted_emails` table to the schema**

In `api/users.py`, extend `REGISTRY_SCHEMA` (append inside the same triple-quoted string, after the `user_packages` table):

```sql
CREATE TABLE IF NOT EXISTS whitelisted_emails (
    email    TEXT PRIMARY KEY,
    is_admin INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 4: Implement the whitelist functions**

Add to `api/users.py`:

```python
def is_whitelisted(email: str) -> bool:
    with _registry_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM whitelisted_emails WHERE email = ?", (email.lower(),)
        ).fetchone()
        return row is not None


def add_whitelisted_email(email: str, is_admin: bool = False) -> None:
    with _registry_conn() as conn:
        conn.execute(
            """
            INSERT INTO whitelisted_emails (email, is_admin) VALUES (?, ?)
            ON CONFLICT (email) DO UPDATE SET is_admin = excluded.is_admin
            """,
            (email.lower().strip(), int(is_admin)),
        )


def is_whitelist_admin(email: str) -> bool:
    with _registry_conn() as conn:
        row = conn.execute(
            "SELECT is_admin FROM whitelisted_emails WHERE email = ?", (email.lower(),)
        ).fetchone()
        return bool(row and row["is_admin"])


def seed_owner_whitelist() -> None:
    """Ensure OWNER_GOOGLE_EMAIL (if set) is whitelisted as admin. Safe to call repeatedly."""
    owner_email = os.environ.get("OWNER_GOOGLE_EMAIL")
    if owner_email:
        add_whitelisted_email(owner_email, is_admin=True)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_users.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add api/users.py tests/test_users.py
git commit -m "feat: add whitelisted_emails table and owner seeding"
```

---

## Task 3: Extract public collection/decks query functions from `web/export.py`

**Files:**
- Modify: `web/export.py`
- Modify: `tests/test_web_export.py` (only if a helper name changed underfoot — verify existing tests still pass unmodified first)

**Interfaces:**
- Produces: `get_collection_data(conn) -> list[dict]`, `get_decks_data(conn) -> list[dict]` (public, importable from `web.export`) — identical behavior to the current private `_get_collection`/`_get_decks`.
- Consumes: nothing new; `conn` is a `sqlite3.Connection` from `mtg_manager.db.get_conn`.

- [ ] **Step 1: Run existing export tests as a baseline**

Run: `pytest tests/test_web_export.py -v`
Expected: All PASS (this establishes the behavior we must not break).

- [ ] **Step 2: Rename the private helpers to public names in `web/export.py`**

Rename `_get_collection` → `get_collection_data` and `_get_decks` → `get_decks_data` (function bodies unchanged). Update their two call sites inside `export_static`:

```python
    with get_conn(cfg.db_path) as conn:
        updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        collection_cards = get_collection_data(conn)
        decks_data = {"updated_at": updated_at, "decks": get_decks_data(conn)}
        sale_data = _get_sale(conn)
```

(`_get_sale` stays private — not needed by `webapp` in this sub-project.)

- [ ] **Step 3: Run tests to verify nothing broke**

Run: `pytest tests/test_web_export.py -v`
Expected: All PASS (pure rename, behavior-preserving).

- [ ] **Step 4: Commit**

```bash
git add web/export.py
git commit -m "refactor: expose get_collection_data/get_decks_data as public functions"
```

---

## Task 4: Scaffold the `webapp` package

**Files:**
- Create: `webapp/__init__.py`
- Create: `webapp/data.py`
- Create: `tests/test_webapp_data.py`
- Create: `requirements-web.txt`

**Interfaces:**
- Consumes: `get_collection_data(conn)`, `get_decks_data(conn)` from Task 3; `mtg_manager.config.Config`; `mtg_manager.db.get_conn`.
- Produces: `get_collection(cfg: Config) -> dict`, `get_decks(cfg: Config) -> dict` — each returns `{"updated_at": <iso str>, "cards"|"decks": [...]}`, computed live from `cfg.db_path`, no files written.

- [ ] **Step 1: Create `webapp/__init__.py`**

```python
```
(empty file — marks `webapp` as a package)

- [ ] **Step 2: Write failing tests in `tests/test_webapp_data.py`**

```python
from mtg_manager.config import Config
from mtg_manager.db import get_conn, upsert_cards
from mtg_manager.models import OwnedCard
from webapp.data import get_collection, get_decks


def _cfg(tmp_path) -> Config:
    return Config(
        packages=[],
        moxfield_delay=0.0,
        mtgtop8_delay=0.0,
        mtgtop8_cache_ttl=0,
        db_path=tmp_path / "test.db",
    )


def test_get_collection_returns_live_data(tmp_path):
    cfg = _cfg(tmp_path)
    with get_conn(cfg.db_path) as conn:
        upsert_cards(conn, [OwnedCard(
            name="Lightning Bolt",
            set_code="m10",
            collector_number="146",
            color_group="red",
            foil=False,
            quantity=4,
        )])

    data = get_collection(cfg)
    assert "updated_at" in data
    assert len(data["cards"]) == 1
    assert data["cards"][0]["name"] == "Lightning Bolt"


def test_get_collection_empty_db(tmp_path):
    cfg = _cfg(tmp_path)
    with get_conn(cfg.db_path):
        pass  # just create the schema, no cards
    data = get_collection(cfg)
    assert data["cards"] == []


def test_get_decks_empty_db(tmp_path):
    cfg = _cfg(tmp_path)
    with get_conn(cfg.db_path):
        pass
    data = get_decks(cfg)
    assert data["decks"] == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_webapp_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'webapp.data'`.

- [ ] **Step 4: Implement `webapp/data.py`**

```python
"""Live (non-exported) per-user collection/deck data for the web app."""

from datetime import datetime, timezone

from mtg_manager.config import Config
from mtg_manager.db import get_conn
from web.export import get_collection_data, get_decks_data


def get_collection(cfg: Config) -> dict:
    with get_conn(cfg.db_path) as conn:
        cards = get_collection_data(conn)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cards": cards,
    }


def get_decks(cfg: Config) -> dict:
    with get_conn(cfg.db_path) as conn:
        decks = get_decks_data(conn)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "decks": decks,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_webapp_data.py -v`
Expected: All PASS.

- [ ] **Step 6: Add web dependencies**

Create `requirements-web.txt`:

```
# Additional dependencies for the web app (Google OAuth + FastAPI)
# Install alongside the main package: pip install -e . && pip install -r requirements-web.txt
fastapi>=0.110.0
uvicorn>=0.29.0
authlib>=1.3.0
itsdangerous>=2.1.2
python-dotenv>=1.0.0
```

- [ ] **Step 7: Install and sanity-check imports**

Run: `pip install -r requirements-web.txt`
Run: `python -c "import fastapi, uvicorn, authlib, itsdangerous"`
Expected: No errors.

- [ ] **Step 8: Commit**

```bash
git add webapp/__init__.py webapp/data.py tests/test_webapp_data.py requirements-web.txt
git commit -m "feat: add webapp package with live collection/decks data functions"
```

---

## Task 5: Auth dependency and session-based user resolution

**Files:**
- Create: `webapp/deps.py`
- Create: `tests/test_webapp_deps.py`

**Interfaces:**
- Consumes: `api.users.get_user_config(user_id) -> Config | None` (Task 1).
- Produces: `NotAuthenticated` (exception class), `require_user(request: Request) -> Config` (FastAPI dependency — raises `NotAuthenticated` if no valid session).

- [ ] **Step 1: Write failing tests in `tests/test_webapp_deps.py`**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_deps.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'webapp.deps'`.

- [ ] **Step 3: Implement `webapp/deps.py`**

```python
"""FastAPI dependencies for session-based auth."""

from fastapi import Request

from api.users import get_user_config
from mtg_manager.config import Config


class NotAuthenticated(Exception):
    """Raised when a request has no valid session; caught by a handler in webapp/main.py."""


def require_user(request: Request) -> Config:
    user_id = request.session.get("user_id")
    if not user_id:
        raise NotAuthenticated()

    cfg = get_user_config(user_id)
    if cfg is None:
        raise NotAuthenticated()

    return cfg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_deps.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp/deps.py tests/test_webapp_deps.py
git commit -m "feat: add require_user auth dependency for webapp"
```

---

## Task 6: Google OAuth login/callback/logout routes

**Files:**
- Create: `webapp/auth.py`
- Create: `tests/test_webapp_auth.py`

**Interfaces:**
- Consumes: `api.users.ensure_user(user_id)`, `api.users.is_whitelisted(email)` (Task 1/2).
- Produces: `router: APIRouter` with `GET /login`, `GET /auth/callback` (named `auth_callback`), `POST /logout`. Sets `request.session["user_id"]` to `f"google:{email}"` on success.

- [ ] **Step 1: Write failing tests in `tests/test_webapp_auth.py`**

These mock Authlib's token exchange so no real network call to Google happens.

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'webapp.auth'`.

- [ ] **Step 3: Implement `webapp/auth.py`**

```python
"""Google OAuth login/callback/logout routes."""

import os

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, RedirectResponse

from api.users import ensure_user, is_whitelisted

router = APIRouter()

oauth = OAuth()
oauth.register(
    name="google",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
    client_kwargs={"scope": "openid email"},
)


@router.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for("auth_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo") or {}
    email = (userinfo.get("email") or "").lower()

    if not email or not is_whitelisted(email):
        return HTMLResponse(
            "<h1>Not authorized</h1><p>This email is not on the whitelist. "
            "Contact the owner.</p>",
            status_code=403,
        )

    user_id = f"google:{email}"
    ensure_user(user_id)
    request.session["user_id"] = user_id
    return RedirectResponse(url="/app", status_code=302)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_auth.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp/auth.py tests/test_webapp_auth.py
git commit -m "feat: add Google OAuth login/callback/logout routes"
```

---

## Task 7: FastAPI app assembly and authenticated data routes

**Files:**
- Create: `webapp/main.py`
- Create: `tests/test_webapp_main.py`
- Modify: `.env` (local only, not committed — see Step 6)

**Interfaces:**
- Consumes: `webapp.auth.router` (Task 6), `webapp.deps.require_user`/`NotAuthenticated` (Task 5), `webapp.data.get_collection`/`get_decks` (Task 4), `api.users.seed_owner_whitelist` (Task 2).
- Produces: `app: FastAPI` importable as `webapp.main:app`, routes `GET /api/collection`, `GET /api/decks` (each requires an authenticated session, otherwise redirects to `/login`).

- [ ] **Step 1: Write failing tests in `tests/test_webapp_main.py`**

```python
from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")

    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "users")

    from webapp.main import app
    return TestClient(app)


def test_api_collection_redirects_when_not_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/collection", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_api_collection_returns_data_when_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/collection")

    assert response.status_code == 200
    assert response.json()["cards"] == []


def test_api_decks_returns_data_when_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/decks")

    assert response.status_code == 200
    assert response.json()["decks"] == []
```

Note: `TestClient.session_transaction()` requires Starlette's `SessionMiddleware` to already be installed on `app` — this is why the assertion step below implements `webapp/main.py` before these tests can pass.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'webapp.main'`.

- [ ] **Step 3: Implement `webapp/main.py`**

```python
"""FastAPI app entry point: uvicorn webapp.main:app"""

import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

load_dotenv()

from api.users import seed_owner_whitelist
from mtg_manager.config import Config
from webapp.auth import router as auth_router
from webapp.data import get_collection, get_decks
from webapp.deps import NotAuthenticated, require_user

app = FastAPI()
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET_KEY", "dev-only-insecure-key"),
)
app.include_router(auth_router)


@app.exception_handler(NotAuthenticated)
async def _handle_not_authenticated(request: Request, exc: NotAuthenticated):
    return RedirectResponse(url="/login", status_code=302)


@app.on_event("startup")
async def _on_startup():
    seed_owner_whitelist()


@app.get("/api/collection")
async def api_collection(cfg: Config = Depends(require_user)):
    return get_collection(cfg)


@app.get("/api/decks")
async def api_decks(cfg: Config = Depends(require_user)):
    return get_decks(cfg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_main.py -v`
Expected: All PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: All PASS (Tasks 1–7 combined, plus all pre-existing tests untouched by this plan).

- [ ] **Step 6: Document local-only `.env` additions**

Add to your local `.env` (this file is already gitignored — do not commit real secrets):

```
GOOGLE_CLIENT_ID=<from Google Cloud Console>
GOOGLE_CLIENT_SECRET=<from Google Cloud Console>
SESSION_SECRET_KEY=<any long random string>
OWNER_GOOGLE_EMAIL=<your Google account email>
```

- [ ] **Step 7: Commit**

```bash
git add webapp/main.py tests/test_webapp_main.py
git commit -m "feat: assemble FastAPI app with authenticated collection/decks routes"
```

---

## Task 8: Manual local end-to-end verification

This task has no automated steps — it exercises the real Google OAuth consent screen, which cannot be scripted in CI. Do this after Task 7 is committed.

- [ ] **Step 1: Create a throwaway Google OAuth client**

In Google Cloud Console → APIs & Services → Credentials → Create OAuth client ID → "Web application". Add `http://localhost:8000/auth/callback` as an authorized redirect URI. Copy the client ID/secret into your local `.env` (from Task 7 Step 6).

- [ ] **Step 2: Seed a local registry and whitelist yourself**

```bash
python -c "from dotenv import load_dotenv; load_dotenv(); from api.users import add_whitelisted_email; import os; add_whitelisted_email(os.environ['OWNER_GOOGLE_EMAIL'], is_admin=True)"
```

- [ ] **Step 3: Run the app**

```bash
uvicorn webapp.main:app --reload
```

- [ ] **Step 4: Log in via browser**

Visit `http://localhost:8000/login`, complete the Google consent screen with your whitelisted account, and confirm you land on `http://localhost:8000/app` (a plain 404 is expected here — `/app` has no route yet, that's sub-project C's job; what matters is you were *not* redirected back to `/login`).

- [ ] **Step 5: Confirm authenticated data routes work**

Visit `http://localhost:8000/api/collection` and `http://localhost:8000/api/decks` in the same browser session — expect JSON responses (`{"updated_at": ..., "cards": []}` / `{"updated_at": ..., "decks": []}` for a fresh account with no synced data yet).

- [ ] **Step 6: Confirm the whitelist gate actually rejects**

Log out (`POST /logout` — e.g. via browser devtools or `curl -X POST http://localhost:8000/logout --cookie-jar cookies.txt --cookie cookies.txt`), then log in with a Google account whose email is *not* whitelisted. Confirm you see the "Not authorized" page and that no new row was created (`sqlite3 ~/mtg_data/registry.sqlite "SELECT user_id FROM users"` does not list it).

- [ ] **Step 7: Confirm the Discord bot is unaffected**

Run: `pytest tests/ -v` one more time, and if you have a Discord test server available, exercise `/setup`, `/addpackage`, `/sync` once to confirm the bot still works end-to-end after the registry key format change.

---

## Self-Review Notes

- **Spec coverage:** Architecture (Task 4/webapp package), registry generalization (Task 1), whitelist table (Task 2), live data instead of static export (Task 3/4), auth flow (Task 6), session/`get_current_user` (Task 5/7), new secrets (Task 7 Step 6), local testing (Task 8), error handling for non-whitelisted/invalid session (Task 6/7) are each covered by a task. Deployment/nginx/TLS and sub-projects B/C are explicitly out of scope per the spec and not included here.
- **Type consistency:** `require_user` (Task 5) returns `Config`, consumed identically by `webapp/main.py` (Task 7) via `Depends(require_user)`. `get_collection`/`get_decks` (Task 4) both take `cfg: Config` and return `dict`, matching their use in Task 7's routes.
- **No placeholders:** every step includes complete, runnable code — no TODOs or "add error handling" style steps.
