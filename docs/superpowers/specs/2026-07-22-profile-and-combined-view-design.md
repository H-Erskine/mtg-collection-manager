# Profile Identity & Combined Collection View — Design Spec
_2026-07-22_

## Context

Every whitelisted user currently only ever sees their own collection on `/app`. This adds a per-account **display name + icon** (set on `/config`), and extends the Collection tab with a **person filter side panel** so any whitelisted user can view a combined pool of everyone's cards, filterable by person — matching a trusted-friend-group model where everyone whitelisted can already interact with each other's data via the shared Discord bot.

## Scope

**In scope:**
- `users` registry table gains `display_name` and `icon` columns, editable via a new "Profile" section on `/config`.
- A new combined-collection endpoint returning every registered user's cards, each tagged with its owner's identity/display name/icon.
- A side panel on the Collection tab: "Just Me" (default, current behavior, unchanged), "All" (everyone pooled together), and one tile per registered person — selecting a person filters the grid to just their cards.

**Out of scope:** combined Decks/Missing views (this only touches Collection), any change to per-user config privacy (packages/formats/sort stay private to each account — only the card list + display name/icon are shared), removing/deactivating a profile, real-time updates when someone else syncs (the combined view is fetched fresh on each load, no live-push).

## Data model

```sql
ALTER TABLE users ADD COLUMN display_name TEXT;
ALTER TABLE users ADD COLUMN icon TEXT;
```

Added via the same guarded-migration pattern already used for the `discord_id` → `user_id` rename (`api/users.py`'s `_migrate_legacy_discord_id`): check `PRAGMA table_info(users)` for the column's existence before adding it, so this is safe to run repeatedly and safe against an already-migrated database.

Both columns are nullable; unset defaults to falling back to the identity string (e.g. the email for a `google:` user) wherever a display name is needed, and a generic 🂠 icon wherever an icon is needed.

### New `api/users.py` functions

```python
def set_profile(user_id: str, display_name: str, icon: str) -> None: ...
def list_profiles() -> list[dict]:
    """Return [{"user_id": ..., "display_name": ..., "icon": ...}, ...] for every registered user."""
```

## New endpoint: `GET /api/collection/all`

Gated by `require_user` (any whitelisted, logged-in user — not admin-only, per the trusted-friend-group decision). For every row in `list_profiles()`:
1. Resolve that user's `Config` via `get_user_config(user_id)`.
2. Query their `owned_cards` via the existing `get_collection_data(conn)` (from `web/export.py`, already reused by `webapp/data.py`).
3. Tag each card with `owner_user_id`, `owner_display_name`, `owner_icon`.

Response:
```json
{
  "updated_at": "...",
  "people": [
    {"user_id": "google:alice@example.com", "display_name": "Alice", "icon": "🐉"},
    {"user_id": "google:harry.erskine1@gmail.com", "display_name": "Harry", "icon": "⚔"}
  ],
  "cards": [
    {"name": "...", "set_code": "...", "collector_number": "...", "foil": false, "quantity": 4,
     "color_group": "Red", "owner_user_id": "google:alice@example.com",
     "owner_display_name": "Alice", "owner_icon": "🐉"}
  ]
}
```

This is a separate endpoint from `GET /api/collection` (unchanged, still returns only the caller's own cards) — the default "Just Me" view on page load costs nothing extra; `/api/collection/all` is only fetched lazily when the user switches the side panel to "All" or a specific other person.

**Performance:** opens one SQLite connection per registered user sequentially. Fine for a small trusted-friend-group scale; not optimized further for this version.

## Frontend (`webapp/static/app.html`, `webapp/static/config.html`)

### `/config` — new "Profile" section
Two fields: display name (text input) and icon (short text input, same free-form emoji-as-text convention already used for the colour tiles, e.g. type `🐉`), with a Save button, calling a new `POST /api/config/profile` route (`{"display_name": str, "icon": str}` → `set_profile`).

### `/app` Collection tab — person filter side panel
A new sidebar (visually consistent with the existing stats-banner tile style), with:
- **Just Me** (default, active on load) — current behavior, uses `/api/collection` as today.
- **All** — fetches `/api/collection/all`, shows everyone's cards pooled, each card gets a small owner badge (icon) overlaid.
- One tile per person from `/api/collection/all`'s `people` list (icon + display name, falling back to the raw identity string if unset) — selecting one filters the pooled data down to just that person's cards.

Switching away from "Just Me" and back re-uses the already-fetched `/api/collection/all` payload client-side (no re-fetch) until the page reloads.

### Error handling
- A person in `list_profiles()` whose `get_user_config` returns `None` (e.g. a registry row exists but something is inconsistent) is skipped silently in the combined endpoint rather than erroring the whole request.
- `/api/collection/all` failing (e.g. one user's SQLite file is locked) — best-effort: skip that user's cards, still return everyone else's, and note the skip in a lightweight `warnings` array in the response (not surfaced prominently in the UI for v1, just available for debugging).

## Testing

- `api/users.py`: new tests for `set_profile`/`list_profiles`, including the column-migration safety (fresh DB with no `display_name`/`icon` columns yet).
- `webapp/config.py`: new route test for `POST /api/config/profile`.
- New `GET /api/collection/all` route test with 2+ registered users, confirming the combined payload's `people` and `cards` both include the right entries and owner tagging.
- Frontend HTML sanity-parses; side panel logic not unit tested (consistent with the rest of `app.html`, verified manually).
