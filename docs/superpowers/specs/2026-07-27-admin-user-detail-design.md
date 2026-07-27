# Admin user detail page design

## Motivation

The admin console (`/admin`) currently shows a roster, failed logins, and
an activity feed, but gives no way to drill into a single user's setup.
When a friend reports missing cards or a broken sync, an admin currently
has no visibility into what Moxfield packages that user has configured,
and no way to trigger a sync on their behalf without asking them to do
it themselves (which is throttled to once per hour).

## Scope

A new per-user detail page, linked from the existing Users table on
`/admin`, showing:

1. **Moxfield packages** — the user's `color_group` → `public_id` list.
2. **Sync now** — a button that triggers a sync for that user immediately,
   bypassing the self-service 60-minute throttle (this is an admin action,
   not a proxy for the user's own `/api/config/sync`).
3. **Activity log** — that user's rows from the existing request log,
   reusing the same friendly-label mapping already in `admin.html`.

Out of scope for this pass: editing packages/formats/sort/profile on
behalf of a user (view + sync only), and formats/sort/profile/groups
display (packages only, per the roster's existing focus).

## Architecture

**Route:** `GET /admin/users/{user_id}` in `webapp/main.py`, gated by
`require_admin` (same pattern as `/admin`), serving a new
`webapp/static/admin-user.html`.

**Users table → link:** in `admin.html`, each row's user_id cell becomes
a link to `/admin/users/{encodeURIComponent(user_id)}`.

**New API endpoints** in `webapp/admin.py` (all `require_admin`):

- `GET /api/admin/users/{user_id}` — 404 if the user isn't in the
  registry (`is_registered`). Otherwise returns:
  ```json
  {
    "user_id": "...",
    "packages": [{"color_group": "...", "public_id": "..."}],
    "activity": [{"created_at": "...", "method": "...", "path": "...", "status": 200}]
  }
  ```
- `POST /api/admin/users/{user_id}/sync` — 404 if the user isn't
  registered. Resolves the user's `Config` via `get_user_config`, calls
  `handle_sync(cfg, is_owner=is_owner(user_id))` directly (no throttle
  check), then `mark_synced(user_id)`. Returns `{"message": "..."}`
  matching the shape of `/api/config/sync`.

**`api/users.py` change:** `list_request_log` gains an optional
`user_id: str | None = None` filter:

```python
def list_request_log(limit: int = 200, user_id: str | None = None) -> list[dict]:
    ...
    query = "SELECT user_id, method, path, status, created_at FROM request_log"
    params: list = []
    if user_id is not None:
        query += " WHERE user_id = ?"
        params.append(user_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    ...
```

This reuses the existing `request_log` table and logging middleware — no
new table, no new capture point. The global admin activity view keeps
calling it with no `user_id` (unchanged behavior).

## UI

`webapp/static/admin-user.html`, matching `admin.html`'s existing look
(same CSS variables/classes, same nav bar):

- Heading: the `user_id`.
- **Packages** section: a table of color_group/public_id, or an "empty"
  message if none.
- **Sync now** button: calls the sync endpoint, shows the returned
  `message` (or error) in a `.msg` div below the button, then reloads
  the packages/activity data.
- **Activity** section: a table of this user's request log rows, reusing
  the same `ACTION_LABELS` friendly-label mapping as `admin.html`
  (duplicated inline — the file is small and this avoids introducing a
  shared JS module for two files).

If `GET /api/admin/users/{user_id}` 404s (bad/stale link), show a simple
"User not found" message instead of the sections.

## Testing

Following the existing `tests/test_webapp_admin.py` / `tests/test_users.py`
pattern:

- `api/users.py`: `list_request_log` filtered by `user_id` returns only
  that user's rows, in the same order/limit semantics as unfiltered.
- `webapp/admin.py`:
  - `GET /api/admin/users/{user_id}` — 404 for unregistered user_id;
    happy path returns packages + activity for a seeded user.
  - `POST /api/admin/users/{user_id}/sync` — 404 for unregistered
    user_id; happy path calls `handle_sync` (mocked) and updates
    `last_synced_at` regardless of how recently the user last synced
    themselves (proves the throttle bypass).
  - Both endpoints reject non-admins with 403 (existing `require_admin`
    gating, same as other admin routes).
