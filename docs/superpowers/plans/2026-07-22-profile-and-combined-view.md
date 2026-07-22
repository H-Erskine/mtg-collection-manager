# Profile Identity & Combined Collection View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each whitelisted account set a display name + icon, and let any whitelisted user view a combined pool of everyone's cards on the Collection tab, filterable by person via a side panel.

**Architecture:** `api/users.py`'s registry gains `display_name`/`icon` columns (guarded migration, same pattern as the existing `discord_id`→`user_id` rename) and `set_profile`/`list_profiles` functions. A new `webapp/data.py` function, `get_all_collections()`, loops over every registered user's own DB and tags each card with its owner's identity. A new `GET /api/collection/all` route exposes this. `webapp/static/app.html`'s Collection tab gains a person-filter side panel; `webapp/static/config.html` gains a Profile section.

**Tech Stack:** FastAPI (existing `webapp` package), SQLite (existing registry), vanilla HTML/CSS/JS, pytest.

## Global Constraints

- `GET /api/collection/all` requires `require_user` only (not `require_admin`) — any whitelisted user can see the combined pool, per the trusted-friend-group decision.
- The existing `GET /api/collection` (own-collection-only) is unchanged — the combined endpoint is additive, not a replacement.
- Per-user packages/formats/sort remain private — only card data + display name/icon are shared in the combined view.
- The `display_name`/`icon` migration must be safe to run repeatedly and safe against a database that already has the columns (same guard pattern as `_migrate_legacy_discord_id`).
- A user in the combined view whose `get_user_config` returns `None`, or whose DB is unreadable, must be skipped without failing the whole request.

---

## Task 1: Registry schema + `set_profile`/`list_profiles`

**Files:**
- Modify: `api/users.py`
- Modify: `tests/test_users.py`

**Interfaces:**
- Produces: `set_profile(user_id: str, display_name: str, icon: str) -> None`; `list_profiles() -> list[dict]` returning `[{"user_id": str, "display_name": str, "icon": str}, ...]` for every registered user (unset fields returned as `""`, not `None`, to keep the frontend simple).

- [ ] **Step 1: Write failing tests in `tests/test_users.py`**

```python
def test_set_and_get_profile_via_list_profiles():
    users_mod.ensure_user("google:alice@example.com")
    users_mod.set_profile("google:alice@example.com", "Alice", "🐉")

    profiles = users_mod.list_profiles()
    alice = next(p for p in profiles if p["user_id"] == "google:alice@example.com")
    assert alice["display_name"] == "Alice"
    assert alice["icon"] == "🐉"


def test_list_profiles_defaults_to_empty_strings_when_unset():
    users_mod.ensure_user("google:bob@example.com")
    profiles = users_mod.list_profiles()
    bob = next(p for p in profiles if p["user_id"] == "google:bob@example.com")
    assert bob["display_name"] == ""
    assert bob["icon"] == ""


def test_list_profiles_includes_all_registered_users():
    users_mod.ensure_user("google:alice@example.com")
    users_mod.ensure_user("discord:12345")
    profiles = users_mod.list_profiles()
    ids = {p["user_id"] for p in profiles}
    assert "google:alice@example.com" in ids
    assert "discord:12345" in ids


def test_profile_migration_safe_on_fresh_registry(tmp_path, monkeypatch):
    """A brand-new registry (no users table yet) must not fail the display_name/icon migration guard."""
    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "fresh_registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "fresh_users")
    u.ensure_user("google:fresh@example.com")  # must not raise
    assert u.is_registered("google:fresh@example.com")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_users.py -v -k "profile"`
Expected: FAIL with `AttributeError: module 'api.users' has no attribute 'set_profile'` (and similarly for `list_profiles`).

- [ ] **Step 3: Add the migration guard and schema columns**

In `api/users.py`, add a new migration function (call it right after `_migrate_legacy_discord_id` in `_registry_conn`):

```python
def _migrate_profile_columns(conn: sqlite3.Connection) -> None:
    """One-time addition of display_name/icon columns for pre-existing registries."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "users" not in tables:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "display_name" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
    if "icon" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN icon TEXT NOT NULL DEFAULT ''")
```

Update `_registry_conn` to call it:

```python
    try:
        _migrate_legacy_discord_id(conn)
        conn.executescript(REGISTRY_SCHEMA)
        _migrate_profile_columns(conn)
        yield conn
        conn.commit()
```

(Note: `_migrate_profile_columns` runs AFTER `conn.executescript(REGISTRY_SCHEMA)` so that on a brand-new registry, the `CREATE TABLE IF NOT EXISTS users (...)` has already created the `users` table — at that point `display_name`/`icon` aren't yet in `REGISTRY_SCHEMA`'s `CREATE TABLE` statement, so the migration function still needs to add them even for a fresh DB. This is intentional: rather than editing the base `CREATE TABLE users (...)` statement in `REGISTRY_SCHEMA` — which would require every existing test's schema assumptions to be re-verified — the guarded `ALTER TABLE` approach handles both fresh and pre-existing databases identically through one code path.)

- [ ] **Step 4: Add `set_profile` and `list_profiles`**

```python
def set_profile(user_id: str, display_name: str, icon: str) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "UPDATE users SET display_name = ?, icon = ? WHERE user_id = ?",
            (display_name.strip(), icon.strip(), user_id),
        )


def list_profiles() -> list[dict]:
    with _registry_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, display_name, icon FROM users ORDER BY user_id"
        ).fetchall()
    return [
        {"user_id": r["user_id"], "display_name": r["display_name"], "icon": r["icon"]}
        for r in rows
    ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_users.py -v`
Expected: All tests pass.

- [ ] **Step 6: Run the full suite**

Run: `pytest tests/ -v`
Expected: All tests pass — this change must not break any existing registry test.

- [ ] **Step 7: Commit**

```bash
git add api/users.py tests/test_users.py
git commit -m "feat: add display_name/icon profile fields to the user registry"
```

---

## Task 2: `POST /api/config/profile` route

**Files:**
- Modify: `webapp/config.py`
- Modify: `tests/test_webapp_config.py`

**Interfaces:**
- Consumes: `api.users.set_profile` (Task 1).
- Produces: `POST /api/config/profile` (`{"display_name": str, "icon": str}` → `set_profile(user_id, ...)`), gated by `require_user`. Also extends the existing `GET /api/config` response with `display_name`/`icon` fields (sourced from `list_profiles()` filtered to the caller, or a small dedicated lookup — implementer's choice, but must return the caller's own current values).

- [ ] **Step 1: Write failing tests in `tests/test_webapp_config.py`**

```python
def test_set_profile(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post("/api/config/profile", json={"display_name": "Alice", "icon": "🐉"})
        get_response = c.get("/api/config")

    assert response.status_code == 200
    data = get_response.json()
    assert data["display_name"] == "Alice"
    assert data["icon"] == "🐉"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_webapp_config.py -v -k test_set_profile`
Expected: FAIL — `/api/config/profile` doesn't exist yet (404), and `GET /api/config` doesn't include `display_name`/`icon` yet.

- [ ] **Step 3: Implement in `webapp/config.py`**

Add `set_profile` to the existing `from api.users import (...)` block, and add a new Pydantic model + route:

```python
class ProfileIn(BaseModel):
    display_name: str
    icon: str
```

```python
@router.post("/api/config/profile")
async def set_config_profile(request: Request, body: ProfileIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    set_profile(user_id, body.display_name, body.icon)
    return {"ok": True}
```

In the existing `get_config` route, add the caller's own profile fields to the response. Import `list_profiles` alongside the other `api.users` imports, and add this lookup:

```python
@router.get("/api/config")
async def get_config(request: Request, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    pkgs = list_packages(user_id)
    profile = next((p for p in list_profiles() if p["user_id"] == user_id), {"display_name": "", "icon": ""})
    return {
        "packages": [{"color_group": cg, "public_id": pid} for cg, pid in pkgs],
        "formats": cfg.formats,
        "pick_list_sort": cfg.pick_list_sort,
        "minutes_since_last_sync": minutes_since_last_sync(user_id),
        "is_admin": _is_admin(user_id),
        "display_name": profile["display_name"],
        "icon": profile["icon"],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_config.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add webapp/config.py tests/test_webapp_config.py
git commit -m "feat: add profile display_name/icon route and expose in GET /api/config"
```

---

## Task 3: `GET /api/collection/all` combined endpoint

**Files:**
- Modify: `webapp/data.py`
- Modify: `webapp/main.py`
- Create: `tests/test_webapp_data_combined.py`
- Modify: `tests/test_webapp_main.py`

**Interfaces:**
- Consumes: `api.users.list_profiles`, `api.users.get_user_config` (existing/Task 1).
- Produces: `get_all_collections() -> dict` in `webapp/data.py` (`{"updated_at": ..., "people": [...], "cards": [...]}`); `GET /api/collection/all` route in `webapp/main.py`, gated by `require_user`.

- [ ] **Step 1: Write failing tests in `tests/test_webapp_data_combined.py`**

```python
from mtg_manager.db import get_conn, upsert_cards
from mtg_manager.models import OwnedCard

import api.users as users_mod
from webapp.data import get_all_collections


def test_get_all_collections_combines_multiple_users(tmp_path, monkeypatch):
    monkeypatch.setattr(users_mod, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(users_mod, "_USERS_DIR", tmp_path / "users")

    users_mod.ensure_user("google:alice@example.com")
    users_mod.set_profile("google:alice@example.com", "Alice", "🐉")
    users_mod.ensure_user("google:bob@example.com")
    users_mod.set_profile("google:bob@example.com", "Bob", "🦊")

    alice_cfg = users_mod.get_user_config("google:alice@example.com")
    with get_conn(alice_cfg.db_path) as conn:
        upsert_cards(conn, [OwnedCard(
            name="Lightning Bolt", set_code="m10", collector_number="146",
            color_group="red", foil=False, quantity=4,
        )])

    bob_cfg = users_mod.get_user_config("google:bob@example.com")
    with get_conn(bob_cfg.db_path) as conn:
        upsert_cards(conn, [OwnedCard(
            name="Counterspell", set_code="mh2", collector_number="269",
            color_group="blue", foil=False, quantity=2,
        )])

    data = get_all_collections()

    people_ids = {p["user_id"] for p in data["people"]}
    assert people_ids == {"google:alice@example.com", "google:bob@example.com"}

    cards_by_name = {c["name"]: c for c in data["cards"]}
    assert cards_by_name["Lightning Bolt"]["owner_user_id"] == "google:alice@example.com"
    assert cards_by_name["Lightning Bolt"]["owner_display_name"] == "Alice"
    assert cards_by_name["Lightning Bolt"]["owner_icon"] == "🐉"
    assert cards_by_name["Counterspell"]["owner_user_id"] == "google:bob@example.com"
    assert cards_by_name["Counterspell"]["owner_display_name"] == "Bob"


def test_get_all_collections_skips_users_with_no_config(tmp_path, monkeypatch):
    """A registry row that get_user_config can't resolve (e.g. no matching data) must not crash the whole request."""
    monkeypatch.setattr(users_mod, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(users_mod, "_USERS_DIR", tmp_path / "users")
    users_mod.ensure_user("google:alice@example.com")

    data = get_all_collections()  # alice has no owned cards yet, should just return empty for her
    assert any(p["user_id"] == "google:alice@example.com" for p in data["people"])
    assert data["cards"] == []
```

Note: if simulating a `get_user_config` failure (returning `None` for a registry row) proves awkward to trigger naturally, it's acceptable to test the skip-on-`None` behavior via a direct monkeypatch of `get_user_config` for one call rather than engineering a real failure condition — the important thing is proving `get_all_collections` doesn't raise when a user's config can't be resolved.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_data_combined.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_all_collections'`.

- [ ] **Step 3: Implement `get_all_collections` in `webapp/data.py`**

```python
from api.users import get_user_config, list_profiles
```

```python
def get_all_collections() -> dict:
    people = list_profiles()
    cards: list[dict] = []

    for person in people:
        cfg = get_user_config(person["user_id"])
        if cfg is None:
            continue
        try:
            with get_conn(cfg.db_path) as conn:
                person_cards = get_collection_data(conn)
        except Exception:
            continue
        for card in person_cards:
            cards.append({
                **card,
                "owner_user_id": person["user_id"],
                "owner_display_name": person["display_name"],
                "owner_icon": person["icon"],
            })

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "people": people,
        "cards": cards,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_data_combined.py -v`
Expected: All tests pass.

- [ ] **Step 5: Add the route in `webapp/main.py`**

```python
from webapp.data import get_all_collections, get_collection, get_decks
```

```python
@app.get("/api/collection/all")
async def api_collection_all(cfg: Config = Depends(require_user)):
    return get_all_collections()
```

- [ ] **Step 6: Write a failing integration test in `tests/test_webapp_main.py`**

```python
def test_api_collection_all_requires_auth(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/collection/all", follow_redirects=False)
    assert response.status_code in (302, 307)


def test_api_collection_all_returns_combined_data(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/collection/all")

    assert response.status_code == 200
    data = response.json()
    assert "people" in data
    assert "cards" in data
```

- [ ] **Step 7: Run tests to verify they pass, then the full suite**

Run: `pytest tests/test_webapp_main.py -v`
Run: `pytest tests/ -v`
Expected: All pass.

- [ ] **Step 8: Commit**

```bash
git add webapp/data.py webapp/main.py tests/test_webapp_data_combined.py tests/test_webapp_main.py
git commit -m "feat: add GET /api/collection/all combined multi-person endpoint"
```

---

## Task 4: Frontend — Profile section on `/config`, person filter side panel on `/app`

**Files:**
- Modify: `webapp/static/config.html`
- Modify: `webapp/static/app.html`

**Interfaces:**
- Consumes: `POST /api/config/profile`, `GET /api/config`'s new `display_name`/`icon` fields (Task 2); `GET /api/collection/all` (Task 3).

- [ ] **Step 1: Add a Profile section to `webapp/static/config.html`**

Add this new `<section>` right after the `<nav>` closing tag, before the existing "Moxfield Packages" section:

```html
<section>
  <h2>Profile</h2>
  <label>Display name</label>
  <input id="profile-name" placeholder="e.g. Alice">
  <label>Icon</label>
  <input id="profile-icon" placeholder="e.g. 🐉" maxlength="4">
  <button class="btn" onclick="saveProfile()">Save</button>
  <div id="profile-msg" class="msg"></div>
</section>
```

Add to the `<script>` block, inside `loadConfig()` right after the existing field-population lines (`document.getElementById('formats-input').value = ...` etc.):

```js
document.getElementById('profile-name').value = data.display_name || '';
document.getElementById('profile-icon').value = data.icon || '';
```

Add a new function alongside `saveFormats()`/`saveSort()`:

```js
async function saveProfile() {
  const display_name = document.getElementById('profile-name').value.trim();
  const icon = document.getElementById('profile-icon').value.trim();
  const res = await fetch('/api/config/profile', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_name, icon }),
  });
  const msg = document.getElementById('profile-msg');
  msg.textContent = res.ok ? 'Saved.' : 'Failed to save.';
  msg.className = 'msg ' + (res.ok ? 'ok' : 'err');
}
```

- [ ] **Step 2: Add the person filter side panel to `webapp/static/app.html`**

Add this CSS to the `<style>` block, alongside the existing `.stats-banner`/`.stat-tile` rules:

```css
#people-sidebar { width: 180px; flex-shrink: 0; background: var(--surface); border: 1px solid var(--surface2); border-radius: var(--radius); padding: 14px; }
.people-title { font-size: 0.75rem; font-weight: 700; color: var(--gold); margin-bottom: 10px; letter-spacing: 0.5px; text-transform: uppercase; }
.person-tile { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 6px; cursor: pointer; margin-bottom: 4px; font-size: 0.85rem; }
.person-tile:hover { background: var(--surface2); }
.person-tile.active { background: var(--surface2); outline: 1px solid var(--accent); }
.person-icon { font-size: 1.1rem; }
.owner-badge { position: absolute; top: 6px; left: 6px; background: rgba(0,0,0,0.85); font-size: 0.9rem; border-radius: 4px; padding: 1px 4px; }
```

Change the `#collection-body` div to include the new sidebar, right before `#collection-grid-wrap`:

```html
<div id="collection-body">
  <div id="people-sidebar">
    <div class="people-title">View</div>
    <div id="people-list"></div>
  </div>
  <div id="collection-grid-wrap">
    <div class="card-grid" id="collection-grid"></div>
  </div>
</div>
```

Add to the `<script>` block, near the top with the other module-level state (`let activeGroup = 'all';`):

```js
let viewScope = 'me';          // 'me' | 'all' | a specific owner_user_id
let combinedData = null;       // lazily fetched /api/collection/all payload
```

Add a new function, and call it once from `init()` right after `selectTabFromHash();`:

```js
function renderPeopleSidebar() {
  const list = document.getElementById('people-list');
  list.innerHTML = '';

  function makeTile(scope, icon, label) {
    const tile = document.createElement('div');
    tile.className = 'person-tile' + (viewScope === scope ? ' active' : '');
    tile.innerHTML = `<span class="person-icon">${icon}</span><span>${label}</span>`;
    tile.onclick = () => setViewScope(scope);
    return tile;
  }

  list.appendChild(makeTile('me', '👤', 'Just Me'));
  list.appendChild(makeTile('all', '✦', 'All'));

  if (combinedData) {
    for (const p of combinedData.people) {
      const label = p.display_name || p.user_id;
      const icon = p.icon || '🂠';
      list.appendChild(makeTile(p.user_id, icon, label));
    }
  }
}

async function setViewScope(scope) {
  viewScope = scope;

  if (scope !== 'me' && !combinedData) {
    const res = await fetch('/api/collection/all');
    if (res.ok) combinedData = await res.json();
  }

  renderPeopleSidebar();

  if (scope === 'me') {
    renderCollection(collectionCards);
  } else if (scope === 'all') {
    renderCollection(combinedData ? combinedData.cards : []);
  } else {
    renderCollection(combinedData ? combinedData.cards.filter(c => c.owner_user_id === scope) : []);
  }
}
```

Call `renderPeopleSidebar()` once after `init()`'s existing `selectTabFromHash();` line, so the sidebar shows "Just Me"/"All" immediately even before the combined data is fetched:

```js
    selectTabFromHash();
    renderPeopleSidebar();
```

- [ ] **Step 3: Sanity-check both files are still well-formed**

Run:
```bash
python -c "import html.parser; p = html.parser.HTMLParser(); p.feed(open('webapp/static/app.html', encoding='utf-8').read())"
python -c "import html.parser; p = html.parser.HTMLParser(); p.feed(open('webapp/static/config.html', encoding='utf-8').read())"
```
Expected: No exceptions.

- [ ] **Step 4: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass (this task adds no new Python tests — it's frontend-only — so this just confirms no regression).

- [ ] **Step 5: Commit**

```bash
git add webapp/static/config.html webapp/static/app.html
git commit -m "feat: add profile section on /config and person filter side panel on /app"
```

---

## Self-Review Notes

- **Spec coverage:** registry columns + migration (Task 1), profile route + GET /api/config exposure (Task 2), combined endpoint with per-card owner tagging and skip-on-failure (Task 3), frontend Profile section + side panel (Task 4) — every piece of the spec is covered.
- **Type consistency:** `get_all_collections()` returns the same `{"updated_at", "people", "cards"}` shape the spec defines; `list_profiles()`'s dict shape (`user_id`/`display_name`/`icon`) is used identically by `webapp/config.py`'s `get_config` and `webapp/data.py`'s `get_all_collections`.
- **No placeholders:** all code is complete in every step.
