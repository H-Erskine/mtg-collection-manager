# Admin console design

## Motivation

We're actively testing the multi-user web app with real friend accounts.
There's currently no visibility into who is logging in (or failing to),
who is registered, or what actions people are taking. A dedicated admin
page gives the owner/admins that visibility during the testing period.

## Scope

A new `/admin` page, visible only to whitelist admins (`is_whitelist_admin`),
showing three read-only-ish views:

1. **User roster** — everyone in the registry: email, admin flag, last
   seen, last synced.
2. **Failed login attempts** — rejected OAuth logins (not on the
   whitelist, or an OAuth error), so admins can see who's trying to get
   in.
3. **Activity log** — every authenticated (and unauthenticated) HTTP
   request, with a toggle between "Activity" (all requests) and
   "Actions" (state-changing requests only).

Whitelist management (add/remove/promote a user), currently a section on
`/config`, moves to `/admin` as well, so all admin-only functionality
lives on one page. `/config` remains purely self-service (packages,
formats, sort, profile) plus a link to `/admin` when `is_admin` is true.

Out of scope for this pass: pagination beyond a fixed "last 200 rows"
limit, log export, real-time/streaming updates, per-category log
filtering beyond the activity/actions split.

## Architecture

`/admin` is served the same way `/config` is today: a FastAPI route in
`webapp/main.py` returning `webapp/static/admin.html`, gated by the
existing `require_admin` dependency (`webapp/deps.py`).

A new `webapp/admin.py` router holds all admin-only API routes:
- `GET /api/admin/users` — roster query
- `GET /api/admin/failed-logins?limit=200`
- `GET /api/admin/activity?limit=200`
- `GET /api/admin/whitelist`, `POST /api/admin/whitelist`,
  `DELETE /api/admin/whitelist/{email}` — moved from `webapp/config.py`
  (functionally unchanged, just relocated for cohesion)

All routes depend on `require_admin`.

## Data model

Two new tables in the existing `registry.sqlite`, added to
`REGISTRY_SCHEMA` in `api/users.py` (auto-migrated on connect, same
pattern as existing tables):

```sql
CREATE TABLE IF NOT EXISTS failed_logins (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT NOT NULL,
    reason     TEXT NOT NULL,      -- 'not_whitelisted' | 'oauth_error'
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS request_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT,               -- NULL for unauthenticated requests
    method     TEXT NOT NULL,
    path       TEXT NOT NULL,
    status     INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_request_log_created_at ON request_log(created_at);
```

New `api/users.py` functions:
- `log_failed_login(email: str, reason: str) -> None`
- `log_request(user_id: str | None, method: str, path: str, status: int) -> None`
- `list_users() -> list[dict]` — roster (users joined with whitelisted_emails for the admin flag)
- `list_failed_logins(limit: int = 200) -> list[dict]`
- `list_request_log(limit: int = 200) -> list[dict]`
- `prune_logs(days: int = 30) -> None` — deletes rows older than `days` from both new tables

The `users` roster already has `last_seen_at`/`last_synced_at`; no schema
change needed there, just a query that also pulls `is_admin` from
`whitelisted_emails`.

## Capture points

**Failed logins** — `webapp/auth.py`'s `auth_callback`: call
`log_failed_login(email, "oauth_error")` in the `OAuthError` branch (email
may be empty there) and `log_failed_login(email, "not_whitelisted")` in
the whitelist-rejection branch.

**Activity log** — a Starlette HTTP middleware registered in
`webapp/main.py`:

```python
@app.middleware("http")
async def _log_requests(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/static"):
        user_id = request.session.get("user_id") if hasattr(request, "session") else None
        try:
            log_request(user_id, request.method, request.url.path, response.status_code)
        except Exception:
            pass  # never let logging break a real request
    return response
```

Runs for every request except static assets. Logging failures are
swallowed so a logging bug can never take down the app.

**Pruning** — `scripts/evict_cache.py` gains one more step alongside its
existing per-user eviction: call `prune_logs(days=30)` once per run.

## Admin page UI

`webapp/static/admin.html`, following the existing look of
`config.html`/`app.html`. Three sections, newest-first tables:

- **Users** — email, admin badge, last seen, last synced. Read-only
  display; actual roster changes happen through the whitelist controls
  below.
- **Whitelist management** — the existing add/remove/promote form
  (moved verbatim from `config.html`'s `admin-section`).
- **Failed logins** — timestamp, email, reason.
- **Activity log** — timestamp, user (or "anonymous"), friendly action
  label, status code. An Activity/Actions toggle filters client-side
  (Actions = method in POST/PUT/DELETE) over the already-fetched rows —
  no extra API call for the toggle.

Friendly labels are produced by a small path-pattern lookup in
`admin.html`'s JS (e.g. `POST /api/config/packages` → "Added Moxfield
package"). Unmapped paths fall back to showing the raw `METHOD /path`.
This mapping lives client-side only, so it can't drift out of sync with
what the middleware actually records — worst case an unmapped new route
just shows its raw form until someone adds a label for it.

`config.html` changes: drop the `admin-section` whitelist UI entirely;
keep the `is_admin` field from `/api/config` only to conditionally show
a nav link to `/admin`.

## Testing

Following the existing `tests/test_webapp_*.py` / `tests/test_users.py`
pattern:
- `api/users.py`: unit tests for `log_failed_login`, `log_request`,
  `list_users`, `list_failed_logins`, `list_request_log`, `prune_logs`
  (including the 30-day cutoff boundary).
- Middleware: a test hitting a couple of routes through the test client
  and asserting `request_log` rows appear with correct method/path/status,
  and that `/static/*` is excluded.
- `webapp/admin.py`: `require_admin` gating (403 for non-admins) plus
  happy-path response shape for each endpoint.
