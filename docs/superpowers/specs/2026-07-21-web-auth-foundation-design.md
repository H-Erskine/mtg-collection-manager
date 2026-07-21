# Web Multi-User + Google Auth — Design Spec
_2026-07-21_

## Context

The current site (`docs/superpowers/specs/2026-06-10-web-collection-site-design.md`) is a single-owner, static, read-only export: `mtg sync` writes `collection.json`/`decks.json`, nginx serves them, no backend process, no auth.

The goal is to phase this out in favor of a self-service, multi-user website: any whitelisted Google account can log in and manage their own collection (Moxfield packages, sync, formats, sort prefs) without the owner's manual intervention — mirroring what the Discord bot's per-user registry (`api/users.py`, `~/mtg_data/registry.sqlite`) already does for Discord users.

**The Discord bot is not being retired.** It keeps running in parallel indefinitely; this work adds a second front end on the same underlying patterns, not a replacement.

**Identity is not linked across surfaces.** A person who uses both Discord and the website gets two independent accounts/collections. No migration or account-merging is in scope.

**Descoped from self-service:** the meta-decklist comparison feature (heaviest Scryfall/Moxfield consumer) stays owner/cron-only — no whitelisted user can trigger it.

## Project decomposition

This is being built as three sequential sub-projects, each with its own spec:

- **A. Backend + Auth foundation** (this spec) — FastAPI service, Google OAuth, generalized multi-user registry, live per-request data endpoints. Ends with: owner can log in via Google and see their own collection served live.
- **B. Self-service onboarding & config** (future spec) — first-login whitelist check + registry row creation, config page (packages/formats/sort), on-demand sync button, admin whitelist management page.
- **C. Multi-user frontend redesign** (future spec) — adapt collection/decks/missing/forsale views to be scoped per logged-in user, nav/login state.

All work happens on branch `feature/web-multiuser-auth`, isolated from `main` and the deployed VM until explicitly merged/deployed.

---

## A. Backend + Auth Foundation

### Architecture

```
webapp/
  main.py       ← FastAPI app, SessionMiddleware, route registration
  auth.py       ← /login, /auth/callback, /logout (Authlib Google OAuth)
  deps.py       ← get_current_user() dependency: session cookie → Config
  data.py       ← get_collection(cfg), get_decks(cfg) — live DB queries
                   (logic moved out of web/export.py, no JSON files written)
```

`webapp/` is a new sibling to `api/` (the Discord bot's handler layer) and `mtg_manager/` (core library). It imports the generalized registry from `api/users.py` and `mtg_manager.config`/`mtg_manager.db` directly. `api/handlers.py` and `bot.py` are untouched apart from the registry key change below.

### Registry generalization (`api/users.py`)

The `users`/`user_packages` tables are currently keyed on `discord_id`. This becomes a generic `user_id` TEXT column, storing prefixed identities:

- `discord:<discord_id>` — existing Discord users (via `bot.py`, updated to pass this prefix)
- `google:<email>` — web users

All existing functions (`ensure_user`, `add_package`, `remove_package`, `set_sort`, `set_formats`, `mark_seen`, `mark_synced`, `minutes_since_last_sync`, `list_users_for_eviction`) keep their behavior, just renamed parameter `discord_id` → `user_id`. `is_owner()` gains a matching `is_owner_google(email)` check against a new `OWNER_GOOGLE_EMAIL` env var, alongside the existing `OWNER_DISCORD_ID` check — both route to the same `~/.mtg_manager/config.toml`/owner DB.

**Migration:** SQLite `ALTER TABLE users RENAME COLUMN discord_id TO user_id` (and same on `user_packages`), run once against `registry.sqlite`. Existing Discord rows are back-filled with the `discord:` prefix in the same migration.

### New table: `whitelisted_emails`

```sql
CREATE TABLE IF NOT EXISTS whitelisted_emails (
    email      TEXT PRIMARY KEY,
    is_admin   INTEGER NOT NULL DEFAULT 0,
    added_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Login only succeeds for emails present here. The owner's email (`OWNER_GOOGLE_EMAIL`) is seeded with `is_admin=1` on first run. Managing this table beyond that seed is out of scope for sub-project A (see sub-project B: admin whitelist page).

### Live data instead of static export

`web/export.py`'s query logic (collection query, deck+allocated_cards join) is extracted into plain functions in `webapp/data.py`:

```python
def get_collection(cfg: Config) -> dict: ...
def get_decks(cfg: Config) -> dict: ...
```

These are called directly by authenticated routes per-request — no JSON files written to disk, no sync-triggered export step. The old public unauthenticated static site (`web/static/index.html` serving world-readable `collection.json`) is retired as part of this change; nginx stops serving that directory once the FastAPI service is live.

### Auth flow

- `GET /login` → redirect to Google's OAuth consent screen (Authlib `Google` client, `openid email` scope).
- `GET /auth/callback` → exchange code, verify ID token, extract email:
  - Not in `whitelisted_emails` → render a static "not authorized — contact the owner" page. No registry row is created.
  - Whitelisted → `ensure_user(f"google:{email}")`, set a signed session cookie (Starlette `SessionMiddleware` + `itsdangerous`, no server-side session store), redirect to `/app`.
- `POST /logout` → clears the session cookie.
- `get_current_user()` dependency: reads the session cookie, resolves `user_id`, builds a `Config` via `api.users.get_user_config()` (generalized). Routes requiring auth depend on it; if absent, redirect to `/login`.

### New secrets / config

`.env` additions: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `SESSION_SECRET_KEY`, `OWNER_GOOGLE_EMAIL`.

New `requirements-web.txt`: `fastapi`, `uvicorn`, `authlib`, `itsdangerous`.

### Deployment (later, not part of local dev)

- New systemd unit `mtg-web.service` running `uvicorn webapp.main:app`, alongside the existing `mtg-bot.service` — both processes, same VM, same shared `mtg_manager`/`api` code.
- nginx switches from serving a static directory to reverse-proxying to uvicorn.
- TLS via certbot on the existing domain (required for Google's production OAuth redirect URI — bare IP + HTTP won't work).
- Deployment itself is out of scope for this spec/branch; this work stays local until explicitly promoted.

### Local testing

Google permits `http://localhost:8000/auth/callback` as a redirect URI for a throwaway "Web application" OAuth client in Google Cloud Console (no TLS required for localhost). Local dev:

```bash
uvicorn webapp.main:app --reload
```

Uses a local/throwaway `registry.sqlite`, fully isolated from the VM and production registry. Nothing in this branch touches `main`, the deployed bot, or prod data.

### Error handling

- Login attempt with a non-whitelisted email: friendly rejection page, no side effects (no registry row).
- OAuth callback failure (bad state, expired code): generic error page, redirect to `/login`.
- Session cookie missing/invalid on a protected route: redirect to `/login`, no exception surfaced to the user.

### Testing

- Unit tests for the generalized `api/users.py` functions (parametrized over `discord:`/`google:` prefixes) — extends existing `tests/test_users.py`.
- Unit tests for `webapp/data.py` query functions against a fixture DB.
- Auth routes are integration-tested with Authlib's test utilities / a mocked OAuth provider (no real Google calls in CI).

### Out of scope for sub-project A

- Self-service config page, on-demand sync UI (sub-project B).
- Admin whitelist management UI beyond the initial owner seed row (sub-project B).
- Any frontend redesign of collection/decks/missing/forsale views (sub-project C).
- VM deployment, nginx/TLS changes (deferred until this branch is ready to ship).
- Meta-decklist comparison feature exposure to non-owner users (permanently descoped from self-service).
