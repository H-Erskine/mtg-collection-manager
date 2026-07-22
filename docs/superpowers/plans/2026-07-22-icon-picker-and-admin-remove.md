# Icon Picker & Admin User Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the free-text icon field on `/config` with a fixed emoji palette, and let an admin fully remove a whitelisted user's account (whitelist entry, registry row, per-user DB file) from the Whitelist admin section.

**Architecture:** Task 1 is frontend-only (no backend change — `POST /api/config/profile` already stores any string). Task 2 adds `remove_whitelisted_user(email)` to `api/users.py` (with an owner-protection guard), a `DELETE /api/admin/whitelist/{email}` route gated by `require_admin`, and a "Remove" button + confirmation dialog in `config.html`'s Whitelist section.

**Tech Stack:** FastAPI (existing `webapp` package), SQLite (existing registry, relying on the existing `ON DELETE CASCADE` foreign key from `user_packages` to `users`), vanilla HTML/CSS/JS.

## Global Constraints

- `remove_whitelisted_user` must refuse to remove the owner's own email (`OWNER_GOOGLE_EMAIL`) — server-side guard, not just a UI-level one.
- Deletion order matters for the cascade to work: delete the `users` row (which cascades `user_packages`) using a connection with `PRAGMA foreign_keys = ON` (already the case in `_registry_conn()`).
- The per-user SQLite file on disk must also be deleted — a DB row delete alone leaves an orphaned file.
- No change to how Discord-identity users are handled — this only affects Google/whitelist-based accounts, consistent with the existing whitelist model.
- The frontend removal action must be gated behind a confirmation dialog before calling the delete endpoint.

---

## Task 1: Icon picker on `/config`

**Files:**
- Modify: `webapp/static/config.html`

**Interfaces:**
- No backend/API changes — `POST /api/config/profile`'s `icon` field is unchanged in shape (still a plain string).

- [ ] **Step 1: Replace the icon input with a palette in `webapp/static/config.html`**

Replace:

```html
<label>Icon</label>
<input id="profile-icon" placeholder="e.g. 🐉" maxlength="4">
```

with:

```html
<label>Icon</label>
<div id="profile-icon-palette" class="icon-palette"></div>
<input type="hidden" id="profile-icon">
```

Add this CSS alongside the existing `.person-tile`/`.stat-tile` rules:

```css
.icon-palette { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.icon-option { font-size: 1.3rem; padding: 6px 10px; border-radius: 6px; background: var(--bg); border: 1px solid var(--surface2); cursor: pointer; }
.icon-option:hover { background: var(--surface2); }
.icon-option.selected { border-color: var(--accent); background: var(--surface2); }
```

Add this JS, near the other module-level constants:

```js
const ICON_OPTIONS = ['⚔', '🐉', '🔥', '💧', '🌿', '☀️', '💀', '🌈', '🃏', '🎲', '🦄', '🧙', '👑', '⭐'];

function renderIconPalette(selected) {
  const palette = document.getElementById('profile-icon-palette');
  palette.innerHTML = '';
  for (const icon of ICON_OPTIONS) {
    const btn = document.createElement('div');
    btn.className = 'icon-option' + (icon === selected ? ' selected' : '');
    btn.textContent = icon;
    btn.onclick = () => selectIcon(icon);
    palette.appendChild(btn);
  }
}

function selectIcon(icon) {
  document.getElementById('profile-icon').value = icon;
  renderIconPalette(icon);
}
```

In `loadConfig()`, replace:

```js
document.getElementById('profile-icon').value = data.icon || '';
```

with:

```js
document.getElementById('profile-icon').value = data.icon || '';
renderIconPalette(data.icon || '');
```

`saveProfile()` already reads `document.getElementById('profile-icon').value` — no change needed there, since the hidden input still holds the selected value.

- [ ] **Step 2: Sanity-check the file is still well-formed**

Run: `python -c "import html.parser; p = html.parser.HTMLParser(); p.feed(open('webapp/static/config.html', encoding='utf-8').read())"`
Expected: No exception.

- [ ] **Step 3: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All pass (this task touches only an HTML file — confirms no regression).

- [ ] **Step 4: Commit**

```bash
git add webapp/static/config.html
git commit -m "feat: replace free-text icon field with a fixed emoji palette"
```

---

## Task 2: `remove_whitelisted_user` registry function

**Files:**
- Modify: `api/users.py`
- Modify: `tests/test_users.py`

**Interfaces:**
- Produces: `remove_whitelisted_user(email: str) -> None` — deletes the `google:<email>` row from `users` (cascading `user_packages`), deletes the `email` row from `whitelisted_emails`, and deletes the per-user SQLite file if present. Raises `ValueError` if `email` matches `OWNER_GOOGLE_EMAIL` (case-insensitive).

- [ ] **Step 1: Write failing tests in `tests/test_users.py`**

```python
def test_remove_whitelisted_user_deletes_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(users_mod, "_USERS_DIR", tmp_path / "users")
    users_mod.ensure_user("google:alice@example.com")
    users_mod.add_package("google:alice@example.com", "Red", "abc123")
    users_mod.add_whitelisted_email("alice@example.com")

    # Force creation of alice's per-user db file on disk
    cfg = users_mod.get_user_config("google:alice@example.com")
    import sqlite3
    conn = sqlite3.connect(cfg.db_path)
    conn.close()
    assert cfg.db_path.exists()

    users_mod.remove_whitelisted_user("alice@example.com")

    assert not users_mod.is_registered("google:alice@example.com")
    assert users_mod.list_packages("google:alice@example.com") == []
    assert not users_mod.is_whitelisted("alice@example.com")
    assert not cfg.db_path.exists()


def test_remove_whitelisted_user_refuses_to_remove_owner(monkeypatch):
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "owner@example.com")
    users_mod.ensure_user("google:owner@example.com")
    users_mod.add_whitelisted_email("owner@example.com", is_admin=True)

    with pytest.raises(ValueError):
        users_mod.remove_whitelisted_user("owner@example.com")

    assert users_mod.is_whitelisted("owner@example.com")


def test_remove_whitelisted_user_case_insensitive_owner_guard(monkeypatch):
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "Owner@Example.com")
    with pytest.raises(ValueError):
        users_mod.remove_whitelisted_user("owner@example.com")


def test_remove_whitelisted_user_is_safe_for_unregistered_email():
    """Removing an email that was never whitelisted/registered should not raise."""
    users_mod.remove_whitelisted_user("nobody@example.com")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_users.py -v -k remove_whitelisted_user`
Expected: FAIL with `AttributeError: module 'api.users' has no attribute 'remove_whitelisted_user'`.

- [ ] **Step 3: Implement `remove_whitelisted_user` in `api/users.py`**

```python
def remove_whitelisted_user(email: str) -> None:
    """Fully delete a whitelisted user's account: whitelist entry, registry
    row (packages/formats/sort cascade via FK), and their per-user DB file.
    Irreversible. Refuses to remove the owner's own email."""
    email = email.strip().lower()
    owner_email = os.environ.get("OWNER_GOOGLE_EMAIL")
    if owner_email is not None and email == owner_email.lower():
        raise ValueError("Cannot remove the owner's account.")

    user_id = f"google:{email}"

    with _registry_conn() as conn:
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM whitelisted_emails WHERE email = ?", (email,))

    db_path = _USERS_DIR / f"{_safe_filename(user_id)}.sqlite"
    if db_path.exists():
        db_path.unlink()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_users.py -v`
Expected: All tests pass.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add api/users.py tests/test_users.py
git commit -m "feat: add remove_whitelisted_user for full account deletion"
```

---

## Task 3: `DELETE /api/admin/whitelist/{email}` route + frontend

**Files:**
- Modify: `webapp/config.py`
- Modify: `tests/test_webapp_config.py`
- Modify: `webapp/static/config.html`

**Interfaces:**
- Consumes: `remove_whitelisted_user` (Task 2).
- Produces: `DELETE /api/admin/whitelist/{email}`, gated by `require_admin`. Returns `{"ok": True}` on success, `400` if the target is the owner's own email.

- [ ] **Step 1: Write failing tests in `tests/test_webapp_config.py`**

```python
def test_admin_can_remove_whitelisted_user(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import add_whitelisted_email
    ensure_user("google:boss@example.com")
    add_whitelisted_email("boss@example.com", is_admin=True)
    ensure_user("google:friend@example.com")
    add_whitelisted_email("friend@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:boss@example.com"
        remove_response = c.delete("/api/admin/whitelist/friend@example.com")
        list_response = c.get("/api/admin/whitelist")

    assert remove_response.status_code == 200
    emails = [row["email"] for row in list_response.json()["whitelist"]]
    assert "friend@example.com" not in emails
    assert "boss@example.com" in emails


def test_admin_cannot_remove_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "boss@example.com")
    client = _client(tmp_path, monkeypatch)
    from api.users import add_whitelisted_email
    ensure_user("google:boss@example.com")
    add_whitelisted_email("boss@example.com", is_admin=True)

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:boss@example.com"
        response = c.delete("/api/admin/whitelist/boss@example.com")

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

Run: `pytest tests/test_webapp_config.py -v -k "remove_whitelisted or cannot_remove_owner"`
Expected: FAIL — the route doesn't exist yet (404).

- [ ] **Step 3: Implement the route in `webapp/config.py`**

Add `remove_whitelisted_user` to the existing `from api.users import (...)` block, then:

```python
@router.delete("/api/admin/whitelist/{email}")
async def remove_from_whitelist(email: str, cfg: Config = Depends(require_admin)):
    try:
        remove_whitelisted_user(email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_config.py -v`
Expected: All pass.

- [ ] **Step 5: Add the "Remove" button in `webapp/static/config.html`**

In `loadWhitelist()`'s row-building loop (currently just sets `el.innerHTML` with the email/admin label), add a remove button:

```js
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
    loadWhitelist();
  } else {
    const data = await res.json().catch(() => ({}));
    alert(data.detail || 'Failed to remove user.');
  }
}
```

(This replaces the existing `loadWhitelist()` function, which previously built each row via `innerHTML` with only the email/admin label — note the new version uses `textContent` for the email label, consistent with avoiding `innerHTML` for any value that could theoretically contain user-influenced content.)

- [ ] **Step 6: Sanity-check and run the full suite**

Run:
```bash
python -c "import html.parser; p = html.parser.HTMLParser(); p.feed(open('webapp/static/config.html', encoding='utf-8').read())"
pytest tests/ -v
```
Expected: No exceptions; all tests pass.

- [ ] **Step 7: Commit**

```bash
git add webapp/config.py tests/test_webapp_config.py webapp/static/config.html
git commit -m "feat: add admin whitelist user removal route and confirm-then-delete UI"
```

---

## Self-Review Notes

- **Spec coverage:** icon palette (Task 1), registry deletion with owner guard and cascade + file cleanup (Task 2), admin route + confirmation UI (Task 3) — every piece of the spec is covered.
- **Type consistency:** `remove_whitelisted_user(email: str) -> None` raises `ValueError` on owner-removal attempt, caught and converted to `HTTPException(400)` in the route — consistent with the existing `set_sort`/`ValueError`→400 pattern already used elsewhere in `webapp/config.py`.
- **No placeholders:** all code is complete in every step.
