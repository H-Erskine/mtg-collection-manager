# Admin User Detail Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give admins a per-user detail page showing a user's Moxfield packages and filtered activity log, with a button to force-sync that user immediately (bypassing the self-service throttle).

**Architecture:** Extend the existing `request_log` query with an optional `user_id` filter, add two new `require_admin`-gated routes in `webapp/admin.py` (detail JSON, sync action), add a page route in `webapp/main.py` serving a new static HTML page, and link to it from the existing Users table in `admin.html`.

**Tech Stack:** FastAPI, SQLite (`api/users.py` registry), vanilla JS/HTML (no build step), pytest + FastAPI `TestClient`.

## Global Constraints

- New API routes must depend on `webapp.deps.require_admin`, matching every existing route in `webapp/admin.py`.
- `list_request_log`'s existing call sites (no `user_id` arg) must keep working unchanged — the new parameter is optional and defaults to `None`.
- The admin sync endpoint must NOT check `minutes_since_last_sync` — this is the whole point of the feature (bypass the self-service 60-minute throttle).
- Match existing code style: dict-returning functions in `api/users.py`, Pydantic-free GET/POST-with-path-param routes in `webapp/admin.py` (no request body needed for either new endpoint), inline `<script>` vanilla JS in the static HTML (no framework), same CSS variables/classes as `admin.html`.
- 404 (not 403) is the correct response when `user_id` isn't in the registry — 403 is reserved for "you're not an admin", which `require_admin` already handles.

---

### Task 1: `list_request_log` gains an optional `user_id` filter

**Files:**
- Modify: `api/users.py:766-782` (the `list_request_log` function)
- Test: `tests/test_users.py` (append near the existing `list_request_log` tests, after line 434)

**Interfaces:**
- Consumes: nothing new.
- Produces: `list_request_log(limit: int = 200, user_id: str | None = None) -> list[dict]` — same return shape as before (`{"user_id", "method", "path", "status", "created_at"}` per row), now optionally scoped to one user.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_users.py`:

```python
def test_list_request_log_filters_by_user_id():
    users_mod.ensure_user("google:alice@example.com")
    users_mod.ensure_user("google:bob@example.com")
    users_mod.log_request("google:alice@example.com", "GET", "/api/config", 200)
    users_mod.log_request("google:bob@example.com", "GET", "/api/collection", 200)
    users_mod.log_request(None, "GET", "/login", 302)

    rows = users_mod.list_request_log(user_id="google:alice@example.com")

    assert len(rows) == 1
    assert rows[0]["path"] == "/api/config"
    assert rows[0]["user_id"] == "google:alice@example.com"


def test_list_request_log_user_filter_respects_limit():
    users_mod.ensure_user("google:alice@example.com")
    for i in range(5):
        users_mod.log_request("google:alice@example.com", "GET", f"/path{i}", 200)

    rows = users_mod.list_request_log(limit=2, user_id="google:alice@example.com")

    assert len(rows) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_users.py::test_list_request_log_filters_by_user_id tests/test_users.py::test_list_request_log_user_filter_respects_limit -v`
Expected: FAIL with `TypeError: list_request_log() got an unexpected keyword argument 'user_id'`

- [ ] **Step 3: Implement the filter**

Replace the existing function in `api/users.py`:

```python
def list_request_log(limit: int = 200, user_id: str | None = None) -> list[dict]:
    with _registry_conn() as conn:
        query = "SELECT user_id, method, path, status, created_at FROM request_log"
        params: list = []
        if user_id is not None:
            query += " WHERE user_id = ?"
            params.append(user_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_users.py -v -k list_request_log`
Expected: PASS (all 4: the 2 pre-existing plus the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add api/users.py tests/test_users.py
git commit -m "feat: filter request log by user_id"
```

---

### Task 2: Admin API routes — user detail and forced sync

**Files:**
- Modify: `webapp/admin.py`
- Test: `tests/test_webapp_admin.py` (append after the existing tests)

**Interfaces:**
- Consumes: `api.users.list_packages(user_id) -> list[tuple[str, str]]`, `api.users.list_request_log(limit, user_id) -> list[dict]` (Task 1), `api.users.is_registered(user_id) -> bool`, `api.users.get_user_config(user_id) -> Config | None`, `api.users.is_owner(user_id) -> bool`, `api.users.mark_synced(user_id) -> None`, `api.handlers.handle_sync(cfg, is_owner) -> str`.
- Produces: `GET /api/admin/users/{user_id}` → `{"user_id": str, "packages": [{"color_group": str, "public_id": str}], "activity": [...]}`; `POST /api/admin/users/{user_id}/sync` → `{"message": str}`. Both 404 when `user_id` isn't registered, 403 when caller isn't an admin (via `require_admin`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_webapp_admin.py`:

```python
def test_admin_user_detail_requires_admin(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")  # not an admin
    ensure_user("google:friend@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        assert c.get("/api/admin/users/google:friend@example.com").status_code == 403
        assert c.post("/api/admin/users/google:friend@example.com/sync").status_code == 403


def test_admin_user_detail_404s_for_unknown_user(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    admin_email = _as_admin(client)

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = f"google:{admin_email}"
        detail_response = c.get("/api/admin/users/google:nobody@example.com")
        sync_response = c.post("/api/admin/users/google:nobody@example.com/sync")

    assert detail_response.status_code == 404
    assert sync_response.status_code == 404


def test_admin_user_detail_returns_packages_and_activity(tmp_path, monkeypatch):
    from api.users import add_package

    client = _client(tmp_path, monkeypatch)
    admin_email = _as_admin(client)
    ensure_user("google:friend@example.com")
    add_package("google:friend@example.com", "red", "abc123")
    log_request("google:friend@example.com", "GET", "/api/config", 200)
    log_request("google:someone-else@example.com", "GET", "/api/config", 200)

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = f"google:{admin_email}"
        response = c.get("/api/admin/users/google:friend@example.com")

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "google:friend@example.com"
    assert body["packages"] == [{"color_group": "red", "public_id": "abc123"}]
    paths = [row["path"] for row in body["activity"]]
    assert paths == ["/api/config"]  # only friend's row, not someone-else's


def test_admin_sync_bypasses_self_service_throttle(tmp_path, monkeypatch):
    from api.users import add_package, mark_synced

    client = _client(tmp_path, monkeypatch)
    admin_email = _as_admin(client)
    ensure_user("google:friend@example.com")
    add_package("google:friend@example.com", "red", "abc123")
    mark_synced("google:friend@example.com")  # simulate "just synced" -- would block self-service

    monkeypatch.setattr(admin_mod, "handle_sync", lambda cfg, is_owner: "Synced 1 package.")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = f"google:{admin_email}"
        response = c.post("/api/admin/users/google:friend@example.com/sync")

    assert response.status_code == 200
    assert response.json() == {"message": "Synced 1 package."}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_webapp_admin.py -v -k "user_detail or admin_sync"`
Expected: FAIL with 404 "Not Found" (routes don't exist yet) for all four tests.

- [ ] **Step 3: Implement the routes**

In `webapp/admin.py`, update the imports and add the two routes:

```python
from api.handlers import handle_sync
from api.users import (
    add_whitelisted_email,
    get_user_config,
    is_owner,
    is_registered,
    list_failed_logins,
    list_packages,
    list_request_log,
    list_users,
    mark_synced,
    remove_whitelisted_user,
)
```

(This replaces the existing `from api.users import (...)` block — add `get_user_config`, `is_owner`, `is_registered`, `list_packages`, `mark_synced` to what's already imported, and add the new `from api.handlers import handle_sync` line.)

Then append at the end of the file:

```python
@router.get("/api/admin/users/{user_id}")
async def get_user_detail(user_id: str, cfg: Config = Depends(require_admin)):
    if not is_registered(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    packages = list_packages(user_id)
    return {
        "user_id": user_id,
        "packages": [{"color_group": cg, "public_id": pid} for cg, pid in packages],
        "activity": list_request_log(limit=200, user_id=user_id),
    }


@router.post("/api/admin/users/{user_id}/sync")
async def admin_sync_user(user_id: str, cfg: Config = Depends(require_admin)):
    if not is_registered(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    target_cfg = get_user_config(user_id)
    if target_cfg is None:
        raise HTTPException(status_code=404, detail="User not found")
    message = handle_sync(target_cfg, is_owner=is_owner(user_id))
    mark_synced(user_id)
    return {"message": message}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_webapp_admin.py -v`
Expected: PASS (all tests in the file, old and new)

- [ ] **Step 5: Commit**

```bash
git add webapp/admin.py tests/test_webapp_admin.py
git commit -m "feat: add admin user-detail and forced-sync API routes"
```

---

### Task 3: Page route for `/admin/users/{user_id}`

**Files:**
- Modify: `webapp/main.py` (near the existing `/admin` route, `webapp/main.py:143-145`)
- Test: `tests/test_webapp_main_config.py` (append; mirrors the existing `/config` page-route tests in that file)

**Interfaces:**
- Consumes: `webapp.deps.require_admin` (already imported in `webapp/main.py`), `_STATIC_DIR` (already defined).
- Produces: `GET /admin/users/{user_id}` — serves `webapp/static/admin-user.html` for admins, 403 for non-admins, redirects to `/login` when logged out (all via the existing `require_admin` dependency, unchanged behavior from `/admin`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_webapp_main_config.py`:

```python
def test_admin_user_detail_page_redirects_when_not_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.get("/admin/users/google:friend@example.com", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_admin_user_detail_page_forbidden_for_non_admin(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/admin/users/google:friend@example.com")

    assert response.status_code == 403


def test_admin_user_detail_page_serves_for_admin(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import add_whitelisted_email, ensure_user
    ensure_user("google:boss@example.com")
    add_whitelisted_email("boss@example.com", is_admin=True)

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:boss@example.com"
        response = c.get("/admin/users/google:friend@example.com")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_webapp_main_config.py -v -k admin_user_detail_page`
Expected: FAIL — first two with 404 (route doesn't exist), third with 404 and/or a missing-file error.

- [ ] **Step 3: Add the route**

In `webapp/main.py`, right after the existing `admin_page` route (after line 145):

```python
@app.get("/admin/users/{user_id}")
async def admin_user_detail_page(user_id: str, cfg: Config = Depends(require_admin)):
    return FileResponse(_STATIC_DIR / "admin-user.html")
```

This also requires the placeholder static file to exist for the 200 test to pass — create an empty `webapp/static/admin-user.html` with minimal valid HTML for now (Task 4 replaces it with the real page):

```html
<!DOCTYPE html>
<html><head><title>MTG Collection — Admin</title></head><body></body></html>
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_webapp_main_config.py -v`
Expected: PASS (all tests in the file, old and new)

- [ ] **Step 5: Commit**

```bash
git add webapp/main.py webapp/static/admin-user.html tests/test_webapp_main_config.py
git commit -m "feat: add /admin/users/{user_id} page route"
```

---

### Task 4: Build the real `admin-user.html` page and link to it from `admin.html`

**Files:**
- Modify: `webapp/static/admin-user.html` (replace the Task 3 placeholder with the full page)
- Modify: `webapp/static/admin.html` (make each Users-table row a link)

**Interfaces:**
- Consumes: `GET /api/admin/users/{user_id}` and `POST /api/admin/users/{user_id}/sync` (Task 2), `GET /api/whoami` (already used by `admin.html`'s `loadWhoami`).
- Produces: nothing consumed by other tasks — this is the final UI layer.

No backend tests apply to static HTML/JS in this codebase (confirmed: `admin.html`, `config.html`, `app.html` have no corresponding JS test files). Verification for this task is manual, via steps 3–4 below.

- [ ] **Step 1: Replace `webapp/static/admin-user.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MTG Collection — Admin — User</title>
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
  h1 { font-size: 1.2rem; margin-bottom: 16px; }
  h2 { font-size: 1rem; color: var(--gold); margin-bottom: 14px; }
  .btn { padding: 8px 16px; background: var(--accent); color: white; border: none; border-radius: var(--radius); font-size: 0.9rem; font-weight: 600; cursor: pointer; }
  .btn:hover { background: #c73652; }
  .btn:disabled { opacity: 0.6; cursor: default; }
  .msg { font-size: 0.85rem; margin-top: 8px; }
  .msg.ok { color: var(--green); }
  .msg.err { color: var(--red); }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--surface2); }
  th { color: var(--muted); font-weight: 600; }
  .empty { color: var(--muted); font-size: 0.85rem; }
  .back-link { display: inline-block; margin-bottom: 16px; color: var(--muted); text-decoration: none; font-size: 0.85rem; }
  .back-link:hover { color: var(--text); }
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
  <a class="back-link" href="/admin">← Back to Admin</a>
  <h1 id="page-title">Loading…</h1>

  <div id="not-found" class="empty" style="display:none">User not found.</div>

  <div id="user-content" style="display:none">
    <section>
      <h2>Moxfield packages</h2>
      <table id="packages-table">
        <thead><tr><th>Color group</th><th>Public ID</th></tr></thead>
        <tbody></tbody>
      </table>
      <div id="packages-empty" class="empty" style="display:none">No packages configured.</div>
    </section>

    <section>
      <h2>Sync</h2>
      <button class="btn" id="sync-btn" onclick="syncUser()">Sync now</button>
      <div id="sync-msg" class="msg"></div>
    </section>

    <section>
      <h2>Activity</h2>
      <table id="activity-table">
        <thead><tr><th>Time</th><th>Action</th><th>Status</th></tr></thead>
        <tbody></tbody>
      </table>
      <div id="activity-empty" class="empty" style="display:none">No activity recorded.</div>
    </section>
  </div>
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

function userIdFromPath() {
  const parts = location.pathname.split('/admin/users/');
  return decodeURIComponent(parts[1] || '');
}

async function loadUserDetail() {
  const userId = userIdFromPath();
  const res = await fetch(`/api/admin/users/${encodeURIComponent(userId)}`);
  if (res.status === 401 || res.status === 403) { location.href = '/login'; return; }
  if (res.status === 404) {
    document.getElementById('page-title').textContent = userId;
    document.getElementById('not-found').style.display = 'block';
    return;
  }
  const data = await res.json();
  document.getElementById('page-title').textContent = data.user_id;
  document.getElementById('user-content').style.display = 'block';
  renderPackages(data.packages);
  renderActivity(data.activity);
}

function renderPackages(packages) {
  const tbody = document.querySelector('#packages-table tbody');
  tbody.innerHTML = '';
  document.getElementById('packages-empty').style.display = packages.length ? 'none' : 'block';
  for (const p of packages) {
    const tr = document.createElement('tr');
    for (const value of [p.color_group, p.public_id]) {
      const td = document.createElement('td');
      td.textContent = value;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

function renderActivity(activity) {
  const tbody = document.querySelector('#activity-table tbody');
  tbody.innerHTML = '';
  document.getElementById('activity-empty').style.display = activity.length ? 'none' : 'block';
  for (const r of activity) {
    const tr = document.createElement('tr');
    const cells = [r.created_at, friendlyLabel(r.method, r.path), r.status];
    for (const value of cells) {
      const td = document.createElement('td');
      td.textContent = String(value);
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

async function syncUser() {
  const userId = userIdFromPath();
  const btn = document.getElementById('sync-btn');
  const msg = document.getElementById('sync-msg');
  btn.disabled = true;
  msg.textContent = '';
  try {
    const res = await fetch(`/api/admin/users/${encodeURIComponent(userId)}/sync`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      msg.textContent = data.message || 'Synced.';
      msg.className = 'msg ok';
      loadUserDetail();
    } else {
      msg.textContent = data.detail || 'Sync failed.';
      msg.className = 'msg err';
    }
  } finally {
    btn.disabled = false;
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

loadUserDetail();
loadWhoami();
</script>
</body>
</html>
```

- [ ] **Step 2: Link each Users-table row to the new page in `webapp/static/admin.html`**

In `admin.html`, find `renderUsers` (around line 151-164) and replace it:

```javascript
function renderUsers(users) {
  const tbody = document.querySelector('#users-table tbody');
  tbody.innerHTML = '';
  for (const u of users) {
    const tr = document.createElement('tr');
    const link = document.createElement('a');
    link.href = `/admin/users/${encodeURIComponent(u.user_id)}`;
    link.textContent = u.user_id;
    link.style.color = 'inherit';
    const linkTd = document.createElement('td');
    linkTd.appendChild(link);
    tr.appendChild(linkTd);
    const cells = [u.is_admin ? '✓' : '', u.last_seen_at || '', u.last_synced_at || ''];
    for (const value of cells) {
      const td = document.createElement('td');
      td.textContent = String(value);
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}
```

- [ ] **Step 3: Manually verify the flow**

Run the dev server per this project's `run` skill / normal startup command, log in as an admin, go to `/admin`, and confirm:
- Each row in the Users table is a clickable link to `/admin/users/<that user's id>`.
- Clicking a friend's row loads their packages and activity.
- Clicking "Sync now" shows a success message and the activity table picks up the new `POST /api/admin/users/.../sync`... (note: that specific admin action isn't in `ACTION_LABELS` yet, so it may show raw — that's fine, it's cosmetic and not part of this plan's scope) — the important check is that the sync succeeds and the page doesn't error.
- Visiting `/admin/users/google:doesnotexist@example.com` directly shows "User not found." instead of a blank/broken page.

- [ ] **Step 4: Run the full test suite once more to confirm nothing regressed**

Run: `pytest -q`
Expected: PASS, no failures.

- [ ] **Step 5: Commit**

```bash
git add webapp/static/admin-user.html webapp/static/admin.html
git commit -m "feat: build admin user detail page UI and link from Users table"
```
