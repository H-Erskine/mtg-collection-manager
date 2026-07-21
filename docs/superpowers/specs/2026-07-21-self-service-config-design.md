# Self-Service Config & Onboarding — Design Spec
_2026-07-21_

## Context

This is sub-project B of the web multi-user rollout (see `docs/superpowers/specs/2026-07-21-web-auth-foundation-design.md`). Sub-projects A (auth) and C (frontend) are built and deployed. Right now a whitelisted user can log in and *view* their collection/decks, but the only way to get data into that collection is still the Discord bot's `/setup`, `/addpackage`, `/sync` commands. This sub-project makes that self-service on the web: a config page for Moxfield packages/formats/sort, an on-demand sync button, and an admin-only whitelist page — all without the owner's manual intervention, matching the original goal from the web-auth-foundation spec.

## Scope

**In scope:** `/config` page — Moxfield package management (add/remove color-group → public_id pairs), tracked-formats input, pick-list sort selector, an on-demand "Sync now" button with the existing 60-minute throttle. An admin-only whitelist section on the same page (add a new whitelisted email, optionally as admin) for the owner account.

**Out of scope:** removing a whitelisted email (v1 is add-only; can be done directly against the registry DB if ever needed), any UI for the meta-comparison feature (permanently descoped from self-service per the original design spec), account deletion/data export.

## Architecture

```
webapp/
  config.py         ← new: router with /config, /api/config, /api/config/packages,
                        /api/config/sort, /api/config/formats, /api/config/sync,
                        /api/admin/whitelist
  static/
    config.html      ← new: config page, same visual style as app.html
  deps.py            ← gains require_admin (builds on require_user + is_whitelist_admin)
  main.py            ← mounts config.py's router
```

`webapp/config.py` is almost entirely a thin wrapper: every piece of registry logic it needs (`add_package`, `remove_package`, `list_packages`, `set_sort`, `set_formats`, `minutes_since_last_sync`, `mark_synced`, `is_whitelist_admin`, `add_whitelisted_email`) already exists in `api/users.py`, built for the Discord bot. The sync action reuses `api/handlers.py`'s existing `handle_sync(cfg, is_owner, color_group)` unchanged.

### `require_admin` dependency (`webapp/deps.py`)

```python
def require_admin(request: Request) -> Config:
    cfg = require_user(request)
    user_id = request.session["user_id"]
    if not user_id.startswith("google:") or not is_whitelist_admin(user_id.split(":", 1)[1]):
        raise HTTPException(status_code=403, detail="Admin access required")
    return cfg
```

Layers on top of `require_user` (reuses its session validation), then additionally checks the whitelist's `is_admin` flag for the session's email.

### Routes

All routes below require `require_user` (an authenticated session); the whitelist routes additionally require `require_admin`.

- `GET /config` — serves `config.html`.
- `GET /api/config` — returns:
  ```json
  {
    "packages": [{"color_group": "Red", "public_id": "abc123"}],
    "formats": ["modern", "legacy"],
    "pick_list_sort": "colour",
    "minutes_since_last_sync": 12.4,
    "is_admin": false
  }
  ```
- `POST /api/config/packages` — body `{"color_group": str, "public_id": str}` → `add_package(user_id, ...)`.
- `DELETE /api/config/packages/{color_group}` → `remove_package(user_id, color_group)`.
- `POST /api/config/sort` — body `{"sort_mode": str}` → `set_sort(user_id, sort_mode)` (400 on `ValueError` from an invalid mode, matching the existing validation).
- `POST /api/config/formats` — body `{"formats": [str]}` → `set_formats(user_id, formats)`.
- `POST /api/config/sync` — checks `minutes_since_last_sync(user_id)` against the same 60-minute threshold `bot.py` uses (`SYNC_THROTTLE_MINUTES`); if throttled, returns `429` with the remaining-minutes message; otherwise calls `handle_sync(cfg, is_owner=False)`, then `mark_synced(user_id)`, and returns the result string. **Defined as a plain `def` route (not `async def`)** — FastAPI runs synchronous path operation functions in its thread pool automatically, so the blocking Moxfield HTTP calls inside `handle_sync` don't block the event loop the way an `async def` route calling sync code directly would.
- `GET /api/admin/whitelist` (admin only) — returns the whitelist table's rows (email, is_admin, added_at).
- `POST /api/admin/whitelist` (admin only) — body `{"email": str, "is_admin": bool}` → `add_whitelisted_email(...)`.

### Frontend (`webapp/static/config.html`)

Same visual style as `app.html` (shared `:root` CSS palette). Sections:
- **Packages** — list of color_group/public_id rows with a remove button each, plus an add form.
- **Formats** — a text input (comma-separated), saved on blur/submit.
- **Sort order** — a `<select>` matching `VALID_SORT_OPTIONS` (`colour`, `alphabetical`, `set`, `cmc`).
- **Sync** — a "Sync now" button; on `429` shows "Already synced N min ago, try again in M min"; on success shows the returned message text (same string the Discord bot would show).
- **Whitelist** (only rendered if `/api/config`'s `is_admin` is `true`) — a list of whitelisted emails with their admin flag, and an add-email form (email + admin checkbox).

`app.html`'s nav gains a "Config" link to `/config`; `config.html`'s nav gets a link back to `/app`.

### Error handling

- Invalid sort mode: `400` with the existing `ValueError` message surfaced to the user.
- Sync throttled: `429` with minutes remaining (same wording style as the Discord bot's throttle message).
- Non-admin hitting an `/api/admin/*` route: `403`.

### Testing

Route tests follow the same pattern already established in `tests/test_webapp_main.py` (real signed session cookies, real registry side effects via `tmp_path`-isolated `api.users`). `require_admin` gets direct unit tests (admin session passes, non-admin session 403s, unauthenticated session redirects) similar to `require_user`'s existing tests in `tests/test_webapp_deps.py`.

## Out of Scope

- Removing/editing a whitelist entry's `is_admin` flag or deleting it entirely (add-only for v1).
- Any UI surfacing the meta-comparison feature.
- Rate-limiting `/api/admin/whitelist` beyond the existing per-route auth check (only the owner is expected to use it).
