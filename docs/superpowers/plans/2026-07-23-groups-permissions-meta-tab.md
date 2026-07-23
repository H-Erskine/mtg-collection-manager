# Personal Groups, Collection Permissions & Meta Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each user maintain a private, one-directional "group" of other users; restrict the combined all-users collection view to admins, replacing it for everyone else with a group-scoped equivalent; and reintroduce a live Meta tab (deck-vs-collection comparison) with a "can my group complete this?" annotation on both the new Meta tab and the existing Missing tab.

**Architecture:** A new `user_groups` registry table plus CRUD functions in `api/users.py`, modeled directly on the existing `user_packages` pattern. Two new data-aggregation functions in `webapp/data.py` (`get_group_collections`, `get_group_ownership`) that reuse the existing `get_all_collections`/`get_all_sale` skip-on-failure iteration pattern but scoped to a caller's group instead of every registered user. A new `get_meta` function in `webapp/data.py` ports the existing static `web/export_meta.py` comparison loop into a live per-request call. New routes added to `webapp/config.py` (group management, directory) and `webapp/main.py` (`/api/collection/group`, `/api/collection/group-check`, `/api/meta`), and `/api/collection/all` moves behind `require_admin`.

**Tech Stack:** Python 3.11+, FastAPI, `sqlite3` (stdlib), pytest.

## Global Constraints

- Groups are one-directional and entirely self-service: adding user B to user A's group requires no consent from B and grants A no capability B didn't already have exposed via existing endpoints — it only changes what A's own client fetches/displays.
- `/api/sale/all` is explicitly **out of scope** — stays `require_user`, unchanged, per the design spec (§3) and the earlier brainstorming decision. Do not touch it.
- No EUR pricing in the new live Meta endpoint (explicit scope decision) — only `name`, `quantity`, `owned` per card, and `total_slots`/`owned_slots` per deck. Adding price is deferred to a future task.
- Every new registry table/column follows the existing `api/users.py` pattern: added to `REGISTRY_SCHEMA` (new tables) or via a new `_migrate_*` function called from `_registry_conn()` (new columns on existing tables), never a manual one-off `ALTER TABLE` elsewhere.
- Display fallback (`display_name` / `icon` default to `''` in the DB) is currently only handled client-side (`p.display_name || p.user_id`, `p.icon || '🂠'` in `app.html`). New server-side aggregation functions in this plan (`get_group_collections`, `get_group_ownership`, the directory endpoint) must apply the same fallback server-side via a single shared helper, so three new UI surfaces don't reimplement it independently.

---

### Task 1: Registry — `user_groups` table and CRUD functions

**Files:**
- Modify: `api/users.py`
- Test: `tests/test_users.py`

**Interfaces:**
- Produces: `add_group_member(owner_user_id: str, member_user_id: str) -> None`, `remove_group_member(owner_user_id: str, member_user_id: str) -> bool`, `list_group_members(owner_user_id: str) -> list[dict]` (each `{"user_id", "display_name", "icon"}`, using the shared display-fallback helper), `_display_profile(row_or_dict) -> dict` (the shared fallback helper consumed by Tasks 2-5).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_users.py
def test_add_group_member_then_list_group_members():
    from api.users import add_group_member, ensure_user, list_group_members

    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    add_group_member("google:alice@example.com", "google:bob@example.com")

    members = list_group_members("google:alice@example.com")

    assert members == [{"user_id": "google:bob@example.com", "display_name": "google:bob@example.com", "icon": "🂠"}]


def test_list_group_members_uses_display_name_and_icon_when_set():
    from api.users import add_group_member, ensure_user, list_group_members, set_profile

    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    set_profile("google:bob@example.com", "Bob", "🐉")
    add_group_member("google:alice@example.com", "google:bob@example.com")

    members = list_group_members("google:alice@example.com")

    assert members == [{"user_id": "google:bob@example.com", "display_name": "Bob", "icon": "🐉"}]


def test_list_group_members_empty_when_no_group():
    from api.users import ensure_user, list_group_members

    ensure_user("google:alice@example.com")

    assert list_group_members("google:alice@example.com") == []


def test_add_group_member_is_one_directional():
    from api.users import add_group_member, ensure_user, list_group_members

    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    add_group_member("google:alice@example.com", "google:bob@example.com")

    assert list_group_members("google:bob@example.com") == []


def test_remove_group_member_returns_true_when_removed():
    from api.users import add_group_member, ensure_user, remove_group_member

    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    add_group_member("google:alice@example.com", "google:bob@example.com")

    removed = remove_group_member("google:alice@example.com", "google:bob@example.com")

    assert removed is True
    from api.users import list_group_members
    assert list_group_members("google:alice@example.com") == []


def test_remove_group_member_returns_false_when_not_present():
    from api.users import ensure_user, remove_group_member

    ensure_user("google:alice@example.com")

    assert remove_group_member("google:alice@example.com", "google:nobody@example.com") is False


def test_add_group_member_twice_is_idempotent():
    from api.users import add_group_member, ensure_user, list_group_members

    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    add_group_member("google:alice@example.com", "google:bob@example.com")
    add_group_member("google:alice@example.com", "google:bob@example.com")

    assert len(list_group_members("google:alice@example.com")) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_users.py -v -k group_member`
Expected: FAIL with `ImportError: cannot import name 'add_group_member'`

- [ ] **Step 3: Write the implementation**

```python
# in api/users.py: add to REGISTRY_SCHEMA (after user_packages, before whitelisted_emails, i.e. after line 42):
CREATE TABLE IF NOT EXISTS user_groups (
    owner_user_id  TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    member_user_id TEXT NOT NULL,
    added_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (owner_user_id, member_user_id)
);


# add a shared display-fallback helper, near list_profiles() (around line 414):
def _display_profile(row: dict) -> dict:
    """Apply the same display_name/icon fallback app.html already does client-side
    (p.display_name || p.user_id, p.icon || '🂠'), so every server-side aggregation
    (directory, group listing, group-scoped collections) is consistent."""
    return {
        "user_id": row["user_id"],
        "display_name": row["display_name"] or row["user_id"],
        "icon": row["icon"] or "🂠",
    }


# add group CRUD functions near add_package/remove_package (after line 255):

def add_group_member(owner_user_id: str, member_user_id: str) -> None:
    """Add another user to owner_user_id's personal group. One-directional and
    idempotent -- does not require member_user_id's consent and does not
    affect member_user_id's own group."""
    with _registry_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_groups (owner_user_id, member_user_id)
            VALUES (?, ?)
            ON CONFLICT (owner_user_id, member_user_id) DO NOTHING
            """,
            (owner_user_id, member_user_id),
        )


def remove_group_member(owner_user_id: str, member_user_id: str) -> bool:
    """Remove a member from owner_user_id's group. Returns True if a row was deleted."""
    with _registry_conn() as conn:
        conn.execute(
            "DELETE FROM user_groups WHERE owner_user_id = ? AND member_user_id = ?",
            (owner_user_id, member_user_id),
        )
        return conn.total_changes > 0


def list_group_members(owner_user_id: str) -> list[dict]:
    """Return [{"user_id", "display_name", "icon"}, ...] for owner_user_id's group."""
    with _registry_conn() as conn:
        rows = conn.execute(
            """
            SELECT u.user_id, u.display_name, u.icon
            FROM user_groups g
            JOIN users u ON u.user_id = g.member_user_id
            WHERE g.owner_user_id = ?
            ORDER BY u.user_id
            """,
            (owner_user_id,),
        ).fetchall()
        return [_display_profile(dict(r)) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_users.py -v`
Expected: PASS (all existing tests plus the 7 new ones)

- [ ] **Step 5: Commit**

```bash
git add api/users.py tests/test_users.py
git commit -m "feat: add one-directional personal groups to the user registry"
```

---

### Task 2: Directory endpoint + group management routes

**Files:**
- Modify: `webapp/config.py`
- Test: `tests/test_webapp_config.py`

**Interfaces:**
- Consumes: `list_profiles`, `_display_profile`, `add_group_member`, `remove_group_member`, `list_group_members` (Task 1).
- Produces: `GET /api/users/directory` → `{"people": [{"user_id","display_name","icon"}, ...]}` (no collection data); `POST /api/config/group` (body `{"member_user_id": str}`) and `DELETE /api/config/group/{member_user_id}`; `GET /api/config` response gains a `"group"` key.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_webapp_config.py
def test_directory_lists_all_registered_users_without_collection_data(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, set_profile
    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    set_profile("google:bob@example.com", "Bob", "🐉")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/users/directory")

    assert response.status_code == 200
    people = response.json()["people"]
    assert {"user_id": "google:bob@example.com", "display_name": "Bob", "icon": "🐉"} in people
    assert all("cards" not in p and "quantity" not in p for p in people)


def test_directory_requires_auth(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/users/directory", follow_redirects=False)
    assert response.status_code in (302, 307)


def test_add_group_member_route(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user
    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post("/api/config/group", json={"member_user_id": "google:bob@example.com"})

    assert response.status_code == 200
    from api.users import list_group_members
    assert list_group_members("google:alice@example.com")[0]["user_id"] == "google:bob@example.com"


def test_remove_group_member_route(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import add_group_member, ensure_user
    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    add_group_member("google:alice@example.com", "google:bob@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.delete("/api/config/group/google:bob@example.com")

    assert response.status_code == 200
    from api.users import list_group_members
    assert list_group_members("google:alice@example.com") == []


def test_get_config_includes_group(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import add_group_member, ensure_user
    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    add_group_member("google:alice@example.com", "google:bob@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/config")

    assert response.json()["group"] == [{"user_id": "google:bob@example.com", "display_name": "google:bob@example.com", "icon": "🂠"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_config.py -v -k "directory or group"`
Expected: FAIL — 404 (routes don't exist), `KeyError: 'group'` (config response missing key)

- [ ] **Step 3: Write the implementation**

```python
# in webapp/config.py: extend the api.users import block (from Task 6/7 of the prior plan, or the original
# lines 7-20 if that plan hasn't run yet) to add:
from api.users import (
    add_group_member,
    list_group_members,
    list_profiles,
    remove_group_member,
    _display_profile,
    # ...(existing names unchanged)
)

# add a new request model near the other *In models (after ProfileIn, line 43-45):
class GroupMemberIn(BaseModel):
    member_user_id: str


# extend get_config() (line 52-65) to add a "group" key:
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
        "group": list_group_members(user_id),
    }


# add new routes after the onboarding route (end of file):

@router.get("/api/users/directory")
async def users_directory(cfg: Config = Depends(require_user)):
    return {"people": [_display_profile(p) for p in list_profiles()]}


@router.post("/api/config/group")
async def add_config_group_member(request: Request, body: GroupMemberIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    add_group_member(user_id, body.member_user_id)
    return {"ok": True}


@router.delete("/api/config/group/{member_user_id}")
async def remove_config_group_member(request: Request, member_user_id: str, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    remove_group_member(user_id, member_user_id)
    return {"ok": True}
```

Note: `list_profiles()` returns raw dicts with `user_id`/`display_name`/`icon` keys, and `_display_profile` (Task 1) accepts anything indexable by those same keys — it works directly on `list_profiles()`'s output without conversion.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_config.py -v`
Expected: PASS (all existing config tests plus the 5 new ones)

- [ ] **Step 5: Commit**

```bash
git add webapp/config.py tests/test_webapp_config.py
git commit -m "feat: add user directory and group management endpoints"
```

---

### Task 3: Restrict `/api/collection/all` to admins; add `/api/collection/group`

**Files:**
- Modify: `webapp/data.py`, `webapp/main.py`
- Test: `tests/test_webapp_main.py`

**Interfaces:**
- Consumes: `list_group_members`, `_display_profile` (Task 1); existing `list_profiles`, `get_user_config`, `get_conn`, `get_collection_data`.
- Produces: `get_group_collections(user_id: str) -> dict` in `webapp/data.py` (same shape as `get_all_collections()`: `{"updated_at", "people", "cards"}`, but `people`/`cards` restricted to `{user_id} ∪ group members`); route `GET /api/collection/group` (`require_user`); `GET /api/collection/all` moves to `require_admin`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_webapp_main.py
def test_api_collection_all_requires_admin_now(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user
    ensure_user("google:alice@example.com")  # not whitelisted as admin

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/collection/all")

    assert response.status_code == 403


def test_api_collection_all_returns_combined_data_for_admin(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import add_whitelisted_email, ensure_user
    ensure_user("google:admin@example.com")
    add_whitelisted_email("admin@example.com", is_admin=True)

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:admin@example.com"
        response = c.get("/api/collection/all")

    assert response.status_code == 200
    data = response.json()
    assert "people" in data
    assert "cards" in data


def test_api_collection_group_requires_auth(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/collection/group", follow_redirects=False)
    assert response.status_code in (302, 307)


def test_api_collection_group_scoped_to_caller_and_group_members(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import add_group_member, ensure_user
    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    ensure_user("google:carol@example.com")  # not in alice's group
    add_group_member("google:alice@example.com", "google:bob@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/collection/group")

    assert response.status_code == 200
    data = response.json()
    people_ids = {p["user_id"] for p in data["people"]}
    assert people_ids == {"google:alice@example.com", "google:bob@example.com"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_main.py -v -k "collection_all or collection_group"`
Expected: FAIL — `test_api_collection_all_requires_admin_now` currently gets 200 not 403; `/api/collection/group` gives 404

- [ ] **Step 3: Write the implementation**

```python
# in webapp/data.py: add imports at top (extend line 5):
from api.users import get_user_config, list_group_members, list_profiles, _display_profile

# add new function after get_all_collections() (end of file):

def get_group_collections(user_id: str) -> dict:
    """Combined collection view scoped to the caller plus their personal group,
    instead of every registered user (see get_all_collections). Same
    skip-on-failure resilience: a member whose config/DB can't be read is
    dropped rather than failing the whole request."""
    member_ids = {user_id} | {m["user_id"] for m in list_group_members(user_id)}
    people = [_display_profile(p) for p in list_profiles() if p["user_id"] in member_ids]
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


# in webapp/main.py: update the import (line 20) to add require_admin (already imported) and
# change the /api/collection/all route (lines 69-71):
@app.get("/api/collection/all")
async def api_collection_all(cfg: Config = Depends(require_admin)):
    return get_all_collections()


# add a new route after it:
@app.get("/api/collection/group")
async def api_collection_group(request: Request, cfg: Config = Depends(require_user)):
    return get_group_collections(request.session["user_id"])


# update the webapp.data import (line 19) to include get_group_collections:
from webapp.data import get_all_collections, get_all_sale, get_collection, get_decks, get_group_collections, get_sale
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_main.py -v`
Expected: PASS. Note `test_api_collection_all_returns_combined_data` (the pre-existing test) must be deleted or renamed — it asserted 200 for a non-admin, which is now correct behavior for `test_api_collection_all_requires_admin_now` instead; remove the old test to avoid a duplicate/contradictory assertion.

- [ ] **Step 5: Commit**

```bash
git add webapp/data.py webapp/main.py tests/test_webapp_main.py
git commit -m "feat: restrict combined collection view to admins, add group-scoped equivalent"
```

---

### Task 4: Group-completion check (`/api/collection/group-check`)

**Files:**
- Modify: `webapp/data.py`, `webapp/main.py`
- Test: `tests/test_webapp_main.py`

**Interfaces:**
- Consumes: `list_group_members`, `_display_profile` (Task 1); `get_owned_quantity` (existing, `mtg_manager/db.py:212`).
- Produces: `get_group_ownership(user_id: str, card_needs: list[dict]) -> dict[str, list[dict]]` in `webapp/data.py` — `card_needs` is `[{"name": str, "quantity": int}, ...]`; returns `{card_name: [{"owner_user_id","owner_display_name","owner_icon","owned"}, ...]}` for group members (excluding the caller) who own at least 1 copy. Route `POST /api/collection/group-check` (`require_user`), body `{"cards": [{"name","quantity"}, ...]}`, response `{"ownership": {...}}`. This is the shared endpoint both the Missing tab and the Meta tab call (Task 6).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_webapp_main.py
def test_group_check_reports_which_members_own_missing_cards(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import add_group_member, ensure_user, get_user_config, set_profile
    from mtg_manager.db import get_conn, upsert_cards
    from mtg_manager.models import OwnedCard

    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    set_profile("google:bob@example.com", "Bob", "🐉")
    add_group_member("google:alice@example.com", "google:bob@example.com")

    bob_cfg = get_user_config("google:bob@example.com")
    with get_conn(bob_cfg.db_path) as conn:
        upsert_cards(conn, [OwnedCard(name="Brainstorm", quantity=2, color_group="Blue")])

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post("/api/collection/group-check", json={"cards": [{"name": "Brainstorm", "quantity": 1}]})

    assert response.status_code == 200
    ownership = response.json()["ownership"]
    assert ownership["Brainstorm"] == [{"owner_user_id": "google:bob@example.com", "owner_display_name": "Bob", "owner_icon": "🐉", "owned": 2}]


def test_group_check_omits_cards_no_group_member_owns(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import add_group_member, ensure_user
    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    add_group_member("google:alice@example.com", "google:bob@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post("/api/collection/group-check", json={"cards": [{"name": "Nonexistent Card", "quantity": 1}]})

    assert response.status_code == 200
    assert response.json()["ownership"] == {}


def test_group_check_requires_auth(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post("/api/collection/group-check", json={"cards": []}, follow_redirects=False)
    assert response.status_code in (302, 307)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_main.py -v -k group_check`
Expected: FAIL — 404 (route doesn't exist)

- [ ] **Step 3: Write the implementation**

```python
# in webapp/data.py: add import at top:
from mtg_manager.db import get_conn, get_owned_quantity

# add new function after get_group_collections() (Task 3):

def get_group_ownership(user_id: str, card_needs: list[dict]) -> dict[str, list[dict]]:
    """For each {"name","quantity"} need, return which of the caller's group
    members (never the caller themself) own at least 1 copy, and how many."""
    members = list_group_members(user_id)
    result: dict[str, list[dict]] = {}

    for person in members:
        cfg = get_user_config(person["user_id"])
        if cfg is None:
            continue
        try:
            with get_conn(cfg.db_path) as conn:
                for need in card_needs:
                    owned = get_owned_quantity(conn, need["name"])
                    if owned > 0:
                        result.setdefault(need["name"], []).append({
                            "owner_user_id": person["user_id"],
                            "owner_display_name": person["display_name"],
                            "owner_icon": person["icon"],
                            "owned": owned,
                        })
        except Exception:
            continue

    return result


# in webapp/main.py: add a request model and route.
# add near the top, after imports (a Pydantic model, mirroring PackageIn's style in webapp/config.py):
from pydantic import BaseModel


class CardNeed(BaseModel):
    name: str
    quantity: int


class GroupCheckIn(BaseModel):
    cards: list[CardNeed]


# update the webapp.data import (line 19) to include get_group_ownership:
from webapp.data import get_all_collections, get_all_sale, get_collection, get_decks, get_group_collections, get_group_ownership, get_sale

# add route after api_collection_group (Task 3):
@app.post("/api/collection/group-check")
async def api_collection_group_check(request: Request, body: GroupCheckIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    card_needs = [{"name": c.name, "quantity": c.quantity} for c in body.cards]
    return {"ownership": get_group_ownership(user_id, card_needs)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_main.py -v`
Expected: PASS (all tests, including the 3 new ones)

- [ ] **Step 5: Commit**

```bash
git add webapp/data.py webapp/main.py tests/test_webapp_main.py
git commit -m "feat: add group-completion check endpoint for Missing/Meta tabs"
```

---

### Task 5: Live Meta data endpoint

**Files:**
- Modify: `webapp/data.py`, `webapp/main.py`
- Test: `tests/test_webapp_main.py`

**Interfaces:**
- Consumes: `get_meta_decks`, `get_owned_quantity` (existing, `mtg_manager/db.py`); `cfg.formats` (existing `Config` field).
- Produces: `get_meta(cfg: Config) -> dict` in `webapp/data.py` — `{"formats": [{"format": str, "decks": [{"name","url","meta_share","total_slots","owned_slots","cards": [{"name","quantity","owned"}, ...]}, ...]}, ...]}`. Route `GET /api/meta` (`require_user`).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_webapp_main.py
def test_api_meta_requires_auth(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/meta", follow_redirects=False)
    assert response.status_code in (302, 307)


def test_api_meta_compares_saved_decklists_against_own_collection(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, get_user_config, set_formats
    from mtg_manager.db import get_conn, upsert_cards
    from mtg_manager.models import DeckCard, Decklist, OwnedCard
    from mtg_manager.db import replace_meta_decks

    ensure_user("google:alice@example.com")
    set_formats("google:alice@example.com", ["modern"])
    cfg = get_user_config("google:alice@example.com")

    with get_conn(cfg.db_path) as conn:
        upsert_cards(conn, [OwnedCard(name="Brainstorm", quantity=1, color_group="Blue")])
        replace_meta_decks(conn, "modern", [
            Decklist(deck_id="d1", name="Mono Blue", url="https://example.com/d1", meta_share=10.0,
                      cards=[DeckCard(name="Brainstorm", quantity=4), DeckCard(name="Force of Will", quantity=4)])
        ])

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/meta")

    assert response.status_code == 200
    data = response.json()
    assert data["formats"][0]["format"] == "modern"
    deck = data["formats"][0]["decks"][0]
    assert deck["name"] == "Mono Blue"
    assert deck["total_slots"] == 8
    assert deck["owned_slots"] == 1
    card_names_missing_first = [c["name"] for c in deck["cards"]]
    assert card_names_missing_first[0] == "Force of Will"  # missing sorts before owned


def test_api_meta_skips_formats_with_no_saved_decklists(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, set_formats
    ensure_user("google:alice@example.com")
    set_formats("google:alice@example.com", ["standard"])  # no saved meta_decks for this format

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/meta")

    assert response.status_code == 200
    assert response.json()["formats"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_main.py -v -k api_meta`
Expected: FAIL — 404 (route doesn't exist)

- [ ] **Step 3: Write the implementation**

```python
# in webapp/data.py: add imports at top:
from collections import defaultdict

from mtg_manager.db import get_meta_decks

# add new function after get_group_ownership() (Task 4):

def get_meta(cfg: Config) -> dict:
    """Live per-request deck-vs-collection comparison, ported from the batch
    logic in web/export_meta.py (which only runs as a static export job).
    No EUR pricing here by design -- meta decklists carry only card names
    (from MTGGoldfish), not Scryfall IDs, so pricing would need its own
    name-keyed cache; deferred to a later task."""
    format_results = []
    with get_conn(cfg.db_path) as conn:
        for fmt in cfg.formats:
            decklists = get_meta_decks(conn, fmt)
            if not decklists:
                continue

            decks = []
            for dl in decklists:
                card_totals: dict[str, int] = defaultdict(int)
                for card in dl.cards:
                    card_totals[card.name] += card.quantity

                total_slots = sum(card_totals.values())
                owned_slots = 0
                cards = []
                for name, qty in card_totals.items():
                    owned = get_owned_quantity(conn, name)
                    owned_slots += min(owned, qty)
                    cards.append({"name": name, "quantity": qty, "owned": owned})

                cards.sort(key=lambda c: (c["owned"] >= c["quantity"], c["name"]))

                decks.append({
                    "name": dl.name,
                    "url": dl.url,
                    "meta_share": dl.meta_share,
                    "total_slots": total_slots,
                    "owned_slots": owned_slots,
                    "cards": cards,
                })

            if any(d["meta_share"] > 0 for d in decks):
                decks.sort(key=lambda d: -d["meta_share"])
            else:
                decks.sort(key=lambda d: -(d["owned_slots"] / d["total_slots"]) if d["total_slots"] else 0)

            format_results.append({"format": fmt, "decks": decks})

    return {"formats": format_results}


# in webapp/main.py: update the webapp.data import (line 19) to include get_meta:
from webapp.data import get_all_collections, get_all_sale, get_collection, get_decks, get_group_collections, get_group_ownership, get_meta, get_sale

# add route after api_decks (line 64-66):
@app.get("/api/meta")
async def api_meta(cfg: Config = Depends(require_user)):
    return get_meta(cfg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_main.py -v`
Expected: PASS (all tests, including the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add webapp/data.py webapp/main.py tests/test_webapp_main.py
git commit -m "feat: add live Meta endpoint comparing saved decklists to a user's collection"
```

---

### Task 6: Frontend — Meta tab, group-completion checkbox on Missing + Meta

**Files:**
- Modify: `webapp/static/app.html`

**Interfaces:**
- Consumes: `GET /api/meta` (Task 5), `POST /api/collection/group-check` (Task 4), `GET /api/collection/group` (Task 3, for the "All"/scope sidebar's non-admin path).
- No automated test — this repo has no JS test harness; verified manually (Step 5), matching this project's existing convention for `/config`-adjacent UI work.

- [ ] **Step 1: Read the current file to get exact current line numbers**

Read `webapp/static/app.html` in full before editing — the line numbers noted below (from planning-time inspection) may have shifted since.

- [ ] **Step 2: Add the Meta tab to the nav bar and a new page panel**

```html
<!-- in the nav (around line 133, after the Missing tab): -->
<div class="tab" data-tab="meta" onclick="showTab('meta', this)">Meta</div>

<!-- new page panel, inserted after the Missing page block (after line 194, before the For Sale block): -->
<div id="page-meta" class="page">
  <label><input type="checkbox" id="meta-group-check" onchange="loadMeta()"> Can my group complete this?</label>
  <div id="meta-formats"></div>
</div>
```

- [ ] **Step 3: Add `loadMeta()` / `renderMeta()`, and a shared `annotateGroupOwnership()` helper**

```javascript
async function annotateGroupOwnership(cardNeeds) {
  // cardNeeds: [{name, quantity}, ...] for cards the user is missing/short on.
  // Returns {name: [{owner_display_name, owner_icon, owned}, ...]}
  if (!cardNeeds.length) return {};
  const res = await fetch('/api/collection/group-check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cards: cardNeeds }),
  });
  if (!res.ok) return {};
  const data = await res.json();
  return data.ownership;
}

function renderOwnershipBadge(card, ownership) {
  const owners = ownership[card.name];
  if (!owners || !owners.length) return '';
  const names = owners.map(o => `${o.owner_icon} ${o.owner_display_name} x${o.owned}`).join(', ');
  return `<div class="group-owners">${names}</div>`;
}

async function loadMeta() {
  const container = document.getElementById('meta-formats');
  const groupCheck = document.getElementById('meta-group-check').checked;
  const res = await fetch('/api/meta');
  if (!res.ok) {
    container.textContent = 'Failed to load meta data.';
    return;
  }
  const data = await res.json();

  let ownership = {};
  if (groupCheck) {
    const missing = data.formats
      .flatMap(f => f.decks)
      .flatMap(d => d.cards)
      .filter(c => c.owned < c.quantity)
      .map(c => ({ name: c.name, quantity: c.quantity - c.owned }));
    ownership = await annotateGroupOwnership(missing);
  }

  container.innerHTML = '';
  for (const fmt of data.formats) {
    const fmtEl = document.createElement('div');
    fmtEl.innerHTML = `<h3>${fmt.format}</h3>`;
    for (const deck of fmt.decks) {
      const deckEl = document.createElement('div');
      const pct = deck.total_slots ? Math.round((deck.owned_slots / deck.total_slots) * 100) : 0;
      deckEl.innerHTML = `<h4><a href="${deck.url}" target="_blank">${deck.name}</a> (${pct}% owned)</h4>`;
      for (const card of deck.cards) {
        const cardEl = document.createElement('div');
        cardEl.textContent = `${card.quantity}x ${card.name} (owned: ${card.owned})`;
        if (groupCheck) cardEl.innerHTML += renderOwnershipBadge(card, ownership);
        deckEl.appendChild(cardEl);
      }
      fmtEl.appendChild(deckEl);
    }
    container.appendChild(fmtEl);
  }
}
```

- [ ] **Step 4: Hook `loadMeta()` into tab switching and add the same checkbox/annotation to Missing**

Find `showTab(tab, el)` (the function called by every nav `<div class="tab">`'s `onclick`) and add a case that calls `loadMeta()` the first time the `meta` tab is shown (same lazy-load pattern already used for `combinedData` in `setViewScope`). Then in the Missing tab: add `<label><input type="checkbox" id="missing-group-check" onchange="checkMissing()"> Can my group complete this?</label>` near the `#decklist-input` textarea (inside the `page-missing` block, lines 171-194), and extend `checkMissing()` (lines 814-854) so that when `#missing-group-check` is checked, it calls `annotateGroupOwnership()` with the missing cards (same `{name, quantity}` shape) and renders `renderOwnershipBadge()` alongside each missing card in `#missing-grid`.

- [ ] **Step 5: Manually verify in the browser**

Run the app locally, log in, go to `/app`:
- Click the Meta tab → confirm saved meta decklists render with owned/total percentages (requires `scripts/refresh_meta.py` to have populated `meta_decks` at least once for a tracked format — if none exist, confirm the tab shows an empty state rather than erroring).
- Check "Can my group complete this?" on Meta → confirm missing cards owned by a group member show an annotation (set up two test accounts with different collections and a group relationship to verify end-to-end).
- Paste a decklist into Missing, check its group-completion checkbox → confirm the same annotation behavior.

- [ ] **Step 6: Commit**

```bash
git add webapp/static/app.html
git commit -m "feat: add live Meta tab and group-completion annotations to Missing/Meta"
```

---

### Task 7: Config page UI — group management

**Files:**
- Modify: `webapp/static/config.html`

**Interfaces:**
- Consumes: `GET /api/users/directory`, `POST /api/config/group`, `DELETE /api/config/group/{member_user_id}` (Task 2); `GET /api/config`'s new `"group"` key (Task 2).
- No automated test — manual verification (Step 4), same convention as Task 6.

- [ ] **Step 1: Read the current file to get exact current line numbers**

Read `webapp/static/config.html` in full before editing.

- [ ] **Step 2: Add a "My Group" section, after the Moxfield Packages section**

```html
<section>
  <h2>My Group</h2>
  <p class="muted">Add friends/teammates you want "can my group complete this?" checks to include. This is private to you — adding someone doesn't change what they can see.</p>
  <div id="group-list"></div>
  <input id="group-search" placeholder="Search by name or email" oninput="searchDirectory()">
  <div id="group-search-results"></div>
</section>
```

- [ ] **Step 3: Add the corresponding JS**

```javascript
let directoryCache = null;

async function loadGroup() {
  const cfg = await (await fetch('/api/config')).json();
  const listEl = document.getElementById('group-list');
  listEl.innerHTML = '';
  for (const member of cfg.group) {
    const row = document.createElement('div');
    row.textContent = `${member.icon} ${member.display_name} `;
    const removeBtn = document.createElement('button');
    removeBtn.textContent = 'Remove';
    removeBtn.onclick = () => removeGroupMember(member.user_id);
    row.appendChild(removeBtn);
    listEl.appendChild(row);
  }
}

async function searchDirectory() {
  const query = document.getElementById('group-search').value.trim().toLowerCase();
  const resultsEl = document.getElementById('group-search-results');
  if (!query) { resultsEl.innerHTML = ''; return; }

  if (!directoryCache) {
    const res = await fetch('/api/users/directory');
    directoryCache = (await res.json()).people;
  }

  const matches = directoryCache.filter(p =>
    p.display_name.toLowerCase().includes(query) || p.user_id.toLowerCase().includes(query)
  );

  resultsEl.innerHTML = '';
  for (const person of matches) {
    const row = document.createElement('div');
    row.textContent = `${person.icon} ${person.display_name} `;
    const addBtn = document.createElement('button');
    addBtn.textContent = 'Add';
    addBtn.onclick = () => addGroupMember(person.user_id);
    row.appendChild(addBtn);
    resultsEl.appendChild(row);
  }
}

async function addGroupMember(memberUserId) {
  await fetch('/api/config/group', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ member_user_id: memberUserId }),
  });
  await loadGroup();
}

async function removeGroupMember(memberUserId) {
  await fetch(`/api/config/group/${encodeURIComponent(memberUserId)}`, { method: 'DELETE' });
  await loadGroup();
}
```

- [ ] **Step 4: Wire `loadGroup()` into the page's existing init flow and manually verify**

Add a `loadGroup()` call alongside whatever existing function loads `/api/config` on page load (e.g. inside the same `init()`/`DOMContentLoaded` handler that already calls `renderPackages()`). Then run the app locally with two test accounts: search the directory from account A, add account B to A's group, confirm it appears in A's `/config` "My Group" list and that account B's own `/config` page is unaffected.

- [ ] **Step 5: Commit**

```bash
git add webapp/static/config.html
git commit -m "feat: add group management UI to /config page"
```

---

## Plan Self-Review Notes

- **Spec coverage:** Personal groups (§2) → Tasks 1, 2, 7. Permission restriction (§3) → Task 3. Meta tab + group-completion (§4) → Tasks 4, 5, 6. `/api/sale/all` explicitly untouched per Global Constraints.
- **Type consistency:** `list_group_members`/`_display_profile` (Task 1) return `{"user_id","display_name","icon"}` dicts consumed identically by the directory endpoint (Task 2), `get_group_collections` (Task 3), and `get_group_ownership` (Task 4) — no shape drift. `get_meta`'s per-card shape (`{"name","quantity","owned"}`, Task 5) matches what the Task 6 frontend's `loadMeta()`/`annotateGroupOwnership()` expect.
- **Existing-test conflict flagged and resolved:** Task 3 explicitly calls out removing the now-contradictory pre-existing `test_api_collection_all_returns_combined_data` test (asserted 200 for a non-admin) rather than leaving both it and the new admin-restriction test in place.
