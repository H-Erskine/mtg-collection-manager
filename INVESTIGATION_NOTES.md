# Moxfield Global Tags — Investigation Notes

## Problem

After `mtg build` + `mtg unbox` + rebuild (with basic lands excluded), Mountain x25 and Plains x25 were still appearing in the `in-box-titan-kani` group in the Moxfield Lands binder.

## Root Cause

The first build (before basic land exclusion was added) tagged Mountain and Plains with `in-box-titan-kani`. All subsequent clearing attempts failed silently because the `MOXFIELD_TOKEN` had expired, causing `PUT` requests to return 401 with an empty body. The script was crashing on the empty response rather than reporting the failure clearly — making it look like the clears had run but leaving the tags unchanged.

Once a valid token was used:
- `PUT /v2/cards/kqAb3/tags {"tags": []}` → `200 []` (Mountain cleared)
- `PUT /v2/cards/EbPg6/tags {"tags": []}` → `200 []` (Plains cleared)

## Key Findings

### API behaviour
- `PUT /v2/cards/{uniqueCardId}/tags` replaces ALL tags on a card and returns the new list
- `GET /v1/cards/tags` returns aggregate list of ALL tag names used across any card — requires auth (`Authorization: Bearer <token>`)
- Both PUT and GET on tag endpoints require a valid Bearer token; 401 returns empty body (not JSON)
- `uniqueCardId` is consistent: the same ucid appears in both the deck API response and the binder API response for the same card identity

### Basic land ucids (all binders)
| Card | ucid |
|------|------|
| Mountain | kqAb3 |
| Plains | EbPg6 |
| Forest | kywJr |
| Island | kaN49 |
| Swamp | kOzVw |

### Token expiry
- `MOXFIELD_TOKEN` JWT expires in ~1 hour
- Must be manually refreshed from browser DevTools Network tab
- When expired, write operations return 401 with empty body — scripts must handle this explicitly

## Functions Added

### `mtg_manager/moxfield_write.py`
- `deck_name_to_tag(deck_name)` — converts deck name to `in-box-{slug}` tag string
- `set_card_tags(unique_card_id, tags, token, delay)` — PUT full tag list for a card; returns True on 200

### `mtg_manager/moxfield.py`
- `fetch_moxfield_card_ids(url)` — fetches a Moxfield deck URL and returns `{card_name_lower: uniqueCardId}` mapping; also indexes DFC front faces

### `mtg_manager/db.py` — new functions
- `add_moxfield_tag(conn, unique_card_id, tag)` — insert into `moxfield_tags` (ignore duplicate)
- `remove_moxfield_tag(conn, unique_card_id, tag)` — delete from `moxfield_tags` (case-insensitive)
- `get_card_moxfield_tags(conn, unique_card_id)` — get all tags we've set for a ucid
- `get_ucids_by_moxfield_tag(conn, tag)` — get all ucids that have a given tag recorded

### `mtg_manager/db.py` — new table
```sql
CREATE TABLE IF NOT EXISTS moxfield_tags (
    unique_card_id  TEXT NOT NULL,
    tag             TEXT NOT NULL,
    PRIMARY KEY (unique_card_id, tag)
);
```

### `mtg_manager/cli.py` — changes
- `_load_env()` — loads `.env` file into `os.environ` at module level
- `build` command: after allocating cards, fetches card ucids from Moxfield deck URL and applies `in-box-{deck-slug}` tag to each non-basic-land card; skips Forest/Island/Mountain/Plains/Swamp/Wastes
- `unbox` command: before deleting the built deck, removes the `in-box-{deck-slug}` tag from all ucids recorded in `moxfield_tags`

## Scripts

### `scripts/fix_basic_land_tags.py`
One-shot cleanup script. Fetches all binder packages, collects ucids for all basic lands, clears all tags on them, and reports whether the target tag is still present on non-basic cards.

### `scripts/test_moxfield_global_tags.py` / `test_moxfield_global_tags2.py`
Probe scripts used during initial API exploration. Confirmed correct payload format (`{"tags": [...]}` not a raw array) and that `x-moxfield-version` header is required for write operations.

## Pending

- Automatic token refresh (avoid manual DevTools step) — token expires ~hourly, currently must be grabbed manually
