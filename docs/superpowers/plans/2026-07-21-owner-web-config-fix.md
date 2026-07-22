# Owner Web-Config Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix a real bug found in production: the web owner's (`google:<OWNER_GOOGLE_EMAIL>`) config edits via `/config` (packages/formats/sort) were silently ignored by sync, because `get_user_config()` always short-circuited the owner to `load_config()` (reading `config.toml`), bypassing the registry entirely. This makes the Google owner identity registry-backed (writable via `/config`, no `config.toml` editing) while keeping the Discord owner shortcut completely untouched, and pointing the Google owner's `db_path` at the real existing `collection.db` so no data is duplicated or lost.

**Architecture:** `api/users.py`'s `get_user_config()` splits its owner short-circuit into two cases: the Discord owner identity keeps the exact existing `load_config()` behavior (zero risk to the Discord bot); the Google owner identity now falls through to the same registry-backed code path every other user uses, with one override — `db_path` is taken from `load_config().db_path` instead of the per-user file formula. `webapp/config.py`'s sync route is fixed to check the real `is_owner` status (currently hardcoded `False`) so the owner is correctly exempt from the sync throttle, matching the Discord bot's existing behavior.

## Global Constraints

- The Discord owner shortcut (`discord:<OWNER_DISCORD_ID>` → `load_config()`, no registry involvement at all) must be **completely unchanged** — this plan must not alter Discord bot behavior in any way.
- The Google owner's `db_path` must resolve to the exact same file `load_config().db_path` currently points at (your real `collection.db`) — never a new/different file.
- No change to how any non-owner user's `Config` is built.
- A one-time, manually-run migration (not part of the automated test suite) copies the current `config.toml` packages/formats/sort into the registry for the Google owner identity, so `/config` reflects real data immediately after deploy — this step is run by the controller directly against the registry, with a verification step before and after, not delegated to a subagent.

---

## Task 1: Split the owner short-circuit in `get_user_config`

**Files:**
- Modify: `api/users.py`
- Modify: `tests/test_users.py`

**Interfaces:**
- Produces: `get_user_config(user_id)` behavior change — for `discord:<OWNER_DISCORD_ID>`, returns `load_config()` exactly as before (untouched code path). For `google:<OWNER_GOOGLE_EMAIL>`, now requires a registry row to exist (like any other user) and returns a `Config` built from the registry's packages/formats/pick_list_sort, EXCEPT `db_path`, which is overridden to `load_config().db_path` when `load_config()` succeeds (falls back to the normal per-user path formula if `config.toml` is missing, so this never hard-fails for a non-owner-configured environment like tests).
- Consumes: existing `load_config`, `_registry_conn`, `_safe_filename`, `MoxfieldPackage`.

- [ ] **Step 1: Write failing tests in `tests/test_users.py`**

Append to the file:

```python
def test_get_user_config_discord_owner_unchanged(tmp_path, monkeypatch):
    """Discord owner shortcut must stay exactly as before: no registry row needed."""
    monkeypatch.setenv("OWNER_DISCORD_ID", "999")
    toml = tmp_path / "config.toml"
    toml.write_text(
        "[moxfield]\npackages = []\nrequest_delay_seconds = 1.0\n"
        "[mtgtop8]\nrequest_delay_seconds = 1.5\ncache_ttl_hours = 24\n"
        f"[database]\npath = '{(tmp_path / 'owner.db').as_posix()}'\n"
    )
    monkeypatch.setattr(
        "mtg_manager.config.DEFAULT_CONFIG", toml
    )

    # No registry row exists for this user at all — the Discord owner path must not need one.
    cfg = users_mod.get_user_config("discord:999")
    assert cfg is not None
    assert cfg.db_path == tmp_path / "owner.db"


def test_get_user_config_google_owner_uses_registry_packages(tmp_path, monkeypatch):
    """Google owner must read packages/formats/sort from the registry, not config.toml."""
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "owner@example.com")
    toml = tmp_path / "config.toml"
    real_db = tmp_path / "real_collection.db"
    toml.write_text(
        "[moxfield]\n"
        "packages = [{color_group = 'Red', public_id = 'toml-real-package'}]\n"
        "request_delay_seconds = 1.0\n"
        "[mtgtop8]\nrequest_delay_seconds = 1.5\ncache_ttl_hours = 24\n"
        f"[database]\npath = '{real_db.as_posix()}'\n"
    )
    monkeypatch.setattr("mtg_manager.config.DEFAULT_CONFIG", toml)

    users_mod.ensure_user("google:owner@example.com")
    users_mod.add_package("google:owner@example.com", "Blue", "registry-package")

    cfg = users_mod.get_user_config("google:owner@example.com")
    assert cfg is not None
    assert len(cfg.packages) == 1
    assert cfg.packages[0].color_group == "Blue"
    assert cfg.packages[0].public_id == "registry-package"


def test_get_user_config_google_owner_db_path_points_at_real_collection(tmp_path, monkeypatch):
    """The whole point of this fix: sync must write to the REAL collection.db, not a fresh per-user file."""
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "owner@example.com")
    toml = tmp_path / "config.toml"
    real_db = tmp_path / "real_collection.db"
    toml.write_text(
        "[moxfield]\npackages = []\nrequest_delay_seconds = 1.0\n"
        "[mtgtop8]\nrequest_delay_seconds = 1.5\ncache_ttl_hours = 24\n"
        f"[database]\npath = '{real_db.as_posix()}'\n"
    )
    monkeypatch.setattr("mtg_manager.config.DEFAULT_CONFIG", toml)

    users_mod.ensure_user("google:owner@example.com")
    cfg = users_mod.get_user_config("google:owner@example.com")
    assert cfg is not None
    assert cfg.db_path == real_db


def test_get_user_config_google_owner_returns_none_without_registry_row(monkeypatch):
    """Google owner still needs ensure_user() to have run (via login) before config exists — same as any user."""
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "owner@example.com")
    assert users_mod.get_user_config("google:owner@example.com") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_users.py -v -k "owner_unchanged or owner_uses_registry or owner_db_path or owner_returns_none"`
Expected: FAIL — `test_get_user_config_google_owner_uses_registry_packages` and the db_path test currently get `config.toml`'s packages/db_path (the old behavior), not the registry's.

- [ ] **Step 3: Implement the split in `api/users.py`**

Replace `get_user_config`:

```python
def get_user_config(user_id: str) -> Config | None:
    """Return a Config for this user.

    The Discord owner identity is routed to ~/.mtg_manager/config.toml directly
    (same DB as CLI) and needs no registry row — this is unchanged from before.
    The Google owner identity is registry-backed like any other user (so
    packages/formats/sort are editable via the web self-service config page),
    except its db_path is overridden to the real collection.db from config.toml
    so sync never creates a separate, empty per-user database for the owner.
    Everyone else is built entirely from registry rows.
    """
    owner_discord_id = os.environ.get("OWNER_DISCORD_ID")
    if owner_discord_id is not None and user_id == f"discord:{owner_discord_id}":
        try:
            return load_config()
        except FileNotFoundError:
            return None

    with _registry_conn() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not user:
            return None

        pkg_rows = conn.execute(
            "SELECT color_group, public_id FROM user_packages "
            "WHERE user_id = ? ORDER BY color_group",
            (user_id,),
        ).fetchall()

    packages = [
        MoxfieldPackage(color_group=r["color_group"], public_id=r["public_id"])
        for r in pkg_rows
    ]
    formats = (
        [f.strip() for f in user["formats"].split(",") if f.strip()]
        if user["formats"]
        else []
    )

    _USERS_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _USERS_DIR / f"{_safe_filename(user_id)}.sqlite"

    if is_owner(user_id):
        # Google owner: keep writing to the real, existing collection.db.
        try:
            db_path = load_config().db_path
        except FileNotFoundError:
            pass

    return Config(
        packages=packages,
        moxfield_delay=1.0,
        mtgtop8_delay=1.5,
        mtgtop8_cache_ttl=24,
        db_path=db_path,
        pick_list_sort=user["pick_list_sort"],
        formats=formats,
    )
```

(Note: by the time the `is_owner(user_id)` check is reached, the Discord owner case has already returned above, so this `is_owner` check only ever matches the Google owner in practice — but it's written generically in case a future third owner-identity type is added.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_users.py -v`
Expected: All tests pass, including the 4 new ones.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: All tests pass — this change must not break any existing Discord-bot or webapp test, since the Discord owner path is byte-for-byte unchanged and the registry path for non-owner users is unchanged (only the Google owner's `db_path` override is new).

- [ ] **Step 6: Commit**

```bash
git add api/users.py tests/test_users.py
git commit -m "fix: make Google owner identity registry-backed, pointing db_path at real collection.db"
```

---

## Task 2: Fix the sync route's throttle/owner-flag bug

**Files:**
- Modify: `webapp/config.py`
- Modify: `tests/test_webapp_config.py`

**Interfaces:**
- Produces: `POST /api/config/sync` now checks `is_owner(user_id)` and skips the 60-minute throttle for the owner (matching `api/bot.py`'s `/sync` behavior — "if not owner: check throttle"), and passes the real `is_owner` flag to `handle_sync` instead of a hardcoded `False`.
- Consumes: `api.users.is_owner` (existing).

- [ ] **Step 1: Write failing tests in `tests/test_webapp_config.py`**

```python
def test_sync_skips_throttle_for_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "owner@example.com")
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:owner@example.com")

    from api.users import mark_synced
    mark_synced("google:owner@example.com")  # just synced 0 minutes ago

    with patch.object(config_mod, "handle_sync", return_value="Synced.") as mock_sync:
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:owner@example.com"
            response = c.post("/api/config/sync")

    assert response.status_code == 200
    mock_sync.assert_called_once()
    # Confirm handle_sync was told this IS the owner, not hardcoded False.
    _, kwargs = mock_sync.call_args
    assert kwargs.get("is_owner", mock_sync.call_args.args[1] if len(mock_sync.call_args.args) > 1 else None) is True


def test_sync_still_throttles_non_owner(tmp_path, monkeypatch):
    monkeypatch.delenv("OWNER_GOOGLE_EMAIL", raising=False)
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

Note: if `mock_sync.call_args` inspection for a positional-vs-keyword `is_owner` argument proves awkward given how the brief's Step 3 code below calls `handle_sync`, simplify the assertion to match however the implementation actually calls it (positional or keyword) — the important behavior to prove is that the value passed is `True` for the owner, not `False`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_config.py -v -k "skips_throttle_for_owner or still_throttles_non_owner"`
Expected: FAIL — current code always throttles (even the owner) and always passes `is_owner=False`.

- [ ] **Step 3: Fix `sync_now` in `webapp/config.py`**

Add `is_owner` to the existing `from api.users import (...)` block, then replace the function body:

```python
@router.post("/api/config/sync")
def sync_now(request: Request, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    owner = is_owner(user_id)

    if not owner:
        mins = minutes_since_last_sync(user_id)
        if mins is not None and mins < SYNC_THROTTLE_MINUTES:
            remaining = int(SYNC_THROTTLE_MINUTES - mins)
            raise HTTPException(
                status_code=429,
                detail=f"Already synced {int(mins)} min ago. Try again in {remaining} min.",
            )

    message = handle_sync(cfg, is_owner=owner)
    mark_synced(user_id)
    return {"message": message}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_config.py -v`
Expected: All tests pass.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add webapp/config.py tests/test_webapp_config.py
git commit -m "fix: sync route correctly skips throttle and reports is_owner for the owner"
```

---

## Task 3 (controller-run, not delegated): One-time production migration

This step is NOT a subagent task — it's a careful, one-time data migration against the live registry on the VM, run directly by the controller with explicit before/after verification. Do this only after Tasks 1-2 are reviewed, merged, and deployed.

- [ ] **Step 1:** SSH to the VM, back up `~/mtg_data/registry.sqlite` (copy to a timestamped backup file).
- [ ] **Step 2:** Read the owner's current `~/.mtg_manager/config.toml` on the VM to get the real packages, tracked formats, and pick_list_sort.
- [ ] **Step 3:** For `google:<OWNER_GOOGLE_EMAIL>`, run `ensure_user` (idempotent, likely already exists from login) then `add_package` for each package in `config.toml`, `set_formats` with the real tracked formats, `set_sort` with the real pick_list_sort — using the deployed code's actual functions (e.g. via a short one-off Python snippet run in the VM's venv), not by hand-editing SQL.
- [ ] **Step 4:** Verify: call `get_user_config("google:<OWNER_GOOGLE_EMAIL>")` in the same venv and confirm `packages`/`formats`/`pick_list_sort` match `config.toml`, and `db_path` equals the real `collection.db` path.
- [ ] **Step 5:** Hit `GET /api/config` as the owner (real browser session or an authenticated curl) and confirm the packages shown match `config.toml`'s real packages.
- [ ] **Step 6:** Do a real "Sync now" from `/config` and confirm the collection is unchanged in size/content (a no-op sync against already-correct data) — proving the registry-backed path now round-trips correctly through the real database.

---

## Self-Review Notes

- **Spec coverage:** Discord owner shortcut preserved exactly (Task 1), Google owner made registry-backed with correct `db_path` override (Task 1), sync throttle/owner-flag bug fixed (Task 2), production migration with verification (Task 3, controller-run).
- **Type consistency:** `get_user_config` still returns `Config | None` in both branches; `sync_now` still returns the same response shape.
- **No placeholders:** All code is complete; Task 3's migration commands will be composed live against the VM's actual `config.toml` contents (which aren't known until read), so it's deliberately a runbook rather than literal code — this is appropriate since it's a one-time controller-run operation, not a subagent-implemented task.
