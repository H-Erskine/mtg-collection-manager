# Design: ManaBox import, personal groups, and Meta tab

Date: 2026-07-23
Branch: feature/web-multiuser-auth

## Summary

Four related additions to the multi-user webapp:

1. ManaBox CSV import as an alternative collection source to Moxfield packages.
2. Per-user, one-directional "groups" (friends/teams), with a directory to find other users.
3. Restrict the combined "view all collections" surface to admins; non-admins get a group-scoped equivalent instead.
4. Reintroduce the Meta tab (deck-vs-collection comparison, previously only in the old single-user static site) with a "can my group complete this" annotation, and add the same annotation to the existing Missing tab.

Plus two supporting pieces: an auto-sync trigger (throttled) fired on collection-source edits, and a new shared card-printing cache to avoid repeat Scryfall calls.

## 1. ManaBox CSV import

**Source toggle.** `/config` gains a collection-source selector, mutually exclusive per user: **Moxfield packages** (existing behavior, unchanged) or **ManaBox CSV** (new). A user is on one or the other, not both.

**Upload endpoint.** `POST /api/config/manabox` accepts a CSV upload. ManaBox's export header is:

```
Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID,Purchase price,Misprint,Altered,Condition,Language,Purchase price currency,Added
```

Only these columns are used: `Name`, `Set code`, `Collector number`, `Foil`, `Quantity`, `Scryfall ID`. Every other column (`Set name`, `Rarity`, `ManaBox ID`, `Purchase price`, `Misprint`, `Altered`, `Condition`, `Language`, `Purchase price currency`, `Added`) is read and discarded — none of it is written to `owned_cards` or anywhere else.

**Validation (reject the whole upload with a clear error, not partial import):**
- Required columns must be present in the header.
- File size and row count caps (exact limits TBD at implementation time, generous enough for a full collection export, e.g. tens of thousands of rows).
- `Quantity` must parse as a positive integer.
- `Name` must be non-empty.

**Mapping into `owned_cards`:**
- `Foil`: `normal` → `foil=0`; any other value (`foil`, `etched`, etc.) → `foil=1`.
- `color_group` is fixed to the literal string `'manabox'` for all rows from this source.
- `cmc`: resolved via the shared card-printing cache (see §5), keyed on `Scryfall ID`.
- Same upsert/replace semantics as an existing Moxfield sync: re-uploading a fresh CSV first clears all `owned_cards` rows where `color_group = 'manabox'`, then inserts the new rows (mirrors `clear_color_group` + `upsert_owned_cards` already used by Moxfield sync).

**Switching sources.** Switching the toggle from ManaBox to Moxfield (or back) does not delete the inactive source's rows automatically — it only changes which is presented/active going forward and which one auto-sync (§5) applies to. (If a user actually wants to discard the old source's cards, that's a manual "remove" action, not an implicit side effect of switching.)

## 2. Personal groups

**Data model.** New table in `registry.sqlite`:

```sql
CREATE TABLE IF NOT EXISTS user_groups (
    owner_user_id  TEXT NOT NULL,
    member_user_id TEXT NOT NULL,
    added_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (owner_user_id, member_user_id)
);
```

This is **one-directional and purely local** to the owner: user A adding user B to A's group does not require B's consent, does not notify B, and does not change anything about what B can see or who is in B's group. It is just A's personal list of "who counts as my team" for the features in §3 and §4.

**Directory.** Since non-admins will no longer see a full list of every user's collection (§3), they need some way to find other users to add. New endpoint `GET /api/users/directory` (any authenticated user) returns `user_id`, `display_name`, `icon` for every registered user — no collection/card data. `/config` gets a group-management UI: search this directory, add/remove members from the caller's group.

**Management endpoints:** `POST /api/config/group` (add member), `DELETE /api/config/group/{member_user_id}` (remove member), included in the existing `GET /api/config` response as a `group: [...]` list.

## 3. Restrict the combined collection view

- `/api/collection/all` moves behind `require_admin` (same gate as `/admin`), unchanged in behavior for admins.
- New `GET /api/collection/group` (any authenticated user, `require_user`): same aggregation/owner-tagging logic as today's `get_all_collections`, but filtered to `{caller} ∪ {caller's group members}` instead of every registered user.
- `/api/sale/all` (For Sale "all" view) is **unchanged** — stays visible to every user regardless of admin/group status, since it's intentionally a market-wide listing.
- `webapp/static/app.html`: the "all collections" view calls `/api/collection/group` for non-admins and `/api/collection/all` for admins, keyed off the same `is_admin` flag already returned by `GET /api/config`.

## 4. Meta tab + group-completion

**Reintroducing Meta.** The old `web/export_meta.py` (single-user, static JSON export) compared saved meta decklists (`meta_decks`/`meta_deck_cards`, refreshed nightly by the existing `scripts/refresh_meta.py` — no changes needed there, it's already global/shared, not per-user) against a collection, per format, producing per-deck owned/total slots and per-card owned/missing + EUR price for missing cards.

New `GET /api/meta` (per-request, live, multi-user — not a static export):
- Runs against the logged-in user's own `owned_cards`.
- Iterates the formats in the user's saved `/config` **formats** preference (reused as-is; no separate format picker on this tab).
- Same comparison shape as the old static export: per deck, `total_slots`/`owned_slots`, and per-card `owned`/`quantity`/`eur_price` for shortfalls.
- New `webapp/static/app.html` tab renders this, matching the visual style of existing tabs (Missing/For Sale).

**Group-completion checkbox.** Both the new Meta tab and the existing Missing tab (currently a client-side paste-a-decklist diff against `/api/collection`) get an optional checkbox: "Can my group complete this?" When checked, for each missing/short card, the backend checks `owned_cards` across every member of the caller's group (via `user_groups`) and annotates the card with which teammate(s) have it and how many — e.g. `"Dave x2, Sam x1"` — not just a boolean. This requires the Missing tab's diff to move server-side (or at least the group-check portion of it) since it needs access to other users' per-user DBs, which the client cannot do directly.

## 5. Supporting pieces

**Auto-sync on config change.** Adding/removing a Moxfield package, or uploading a ManaBox CSV, automatically triggers a sync for that user — distinct from the existing manual "Sync now" action (which keeps its current 60-minute throttle for non-owners). The auto-trigger has its own throttle: at most once per 60 seconds per user, tracked via a new `last_auto_synced_at` column on `users` in `registry.sqlite`, checked the same way `minutes_since_last_sync` is today but at second granularity. If an edit arrives inside the 60s window, the sync is not silently dropped — the UI shows a brief "auto-syncing shortly" indicator, and the existing manual Sync button remains available as a fallback.

**Shared card-printing cache.** New file `~/mtg_data/cards_cache.sqlite` (separate from `registry.sqlite`, since this is bulk card data rather than account/user metadata), one table:

```sql
CREATE TABLE IF NOT EXISTS card_printings (
    scryfall_id      TEXT NOT NULL PRIMARY KEY,
    name             TEXT NOT NULL,
    set_code         TEXT NOT NULL,
    collector_number TEXT NOT NULL,
    cmc              REAL NOT NULL DEFAULT 0,
    cached_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
```

ManaBox import (§1) looks up each row's `Scryfall ID` here first. On a cache hit, `cmc` (and set/collector number if useful for the printing-image lookup) comes from the cache with no external call. On a miss, a single batched Scryfall `/cards/collection` request (by ID, same batching pattern as `web/export_meta.py`'s `_fetch_scryfall_prices`) resolves the misses and populates the cache for every user going forward. This table is designed to be reusable by other lookups later (e.g. Moxfield sync could also consult it), but no other caller is being changed as part of this work.

## Out of scope

- Mutual/two-way group membership or invite/accept flows — groups are one-directional and unilateral, per explicit decision.
- Admin-managed groups — groups are entirely self-service per user.
- Enriching the card cache with art or other Scryfall fields beyond what's needed for CMC right now (the schema leaves room, but only `cmc` is populated by this work).
- Any change to `/api/sale/all` or the For Sale tab's visibility.
- Any change to how Moxfield packages/sync behave for users who stay on that source.
