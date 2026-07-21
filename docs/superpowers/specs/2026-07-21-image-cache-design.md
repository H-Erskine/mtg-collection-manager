# Server-Side Image Cache — Design Spec
_2026-07-21_

## Context

`webapp/static/app.html` (sub-project C) currently loads every card image directly from Scryfall's live API on every page load, for every user. Since a card's image is keyed only by `(set_code, collector_number)` — not by which user owns it — this is wasteful and risks Scryfall rate limits (~10 req/s) for a collection with hundreds of cards. This adds a lazy, shared, server-side cache.

## Scope

**In scope:** a new `GET /images/{set_code}/{collector_number}` route in the `webapp` FastAPI app that serves a cached file if present, otherwise fetches it from Scryfall, saves it, and serves it. `app.html` is updated to request images from this route instead of Scryfall directly.

**Out of scope:** the `namedImgUrl()` fallback (used for proxy cards with no owned printing — no stable `set`/`cn` cache key exists for a name-only lookup) stays pointed directly at Scryfall. No bulk pre-sync job, no cache eviction/expiry (Scryfall printings are immutable once printed — a cached image never goes stale). No change to the Discord bot's `web/export.py` image-download thread (separate, pre-existing feature for the old static site).

## Design

### Cache location

`~/mtg_data/image_cache/{set_code}/{safe_cn}.jpg`, following the existing `~/mtg_data/` convention used by the registry and per-user DBs. `safe_cn` is `collector_number` with `/` replaced by `_` (collector numbers essentially never contain `/` in practice, but split-card entries historically have; this avoids an unintended subdirectory or path traversal). The directory is created on first write if missing.

### Route

```python
GET /images/{set_code}/{collector_number}
```

No authentication required — this serves publicly-available Scryfall card art, identical to how the old static site's `web/static/images/` directory was served unauthenticated by nginx.

Behavior:
1. Sanitize `collector_number` for the filename (`/` → `_`); reject/400 if `set_code` or the sanitized `collector_number` contain any other filesystem-unsafe characters (only allow `[A-Za-z0-9_-]+` for `set_code`, and the same plus already-handled `_` for the sanitized collector number) — this guards against path traversal (`..`) since both become path components.
2. If `~/mtg_data/image_cache/{set_code}/{safe_cn}.jpg` exists, serve it directly (`FileResponse`) with `Cache-Control: public, max-age=31536000, immutable` (a given printing's art never changes).
3. If not, fetch `https://api.scryfall.com/cards/{quote(set_code)}/{quote(collector_number)}?format=image&version=normal` (the *unsanitized* `collector_number`, URL-quoted, so the real Scryfall lookup is unaffected by filename sanitization), save the response bytes to the cache path, then serve it the same way.
4. If Scryfall returns a non-200 (unknown printing), return 404 — the frontend's existing `onerror` fallback chain (try named lookup, then the `card-fallback` placeholder) already handles this.

### Frontend change (`webapp/static/app.html`)

```js
function scryfallImgUrl(set_code, collector_number) {
  return `/images/${encodeURIComponent(set_code)}/${encodeURIComponent(collector_number)}`;
}
```

(`namedImgUrl()` is unchanged.) No other JS changes — `makeCard()`'s existing `onerror` fallback chain (try `namedImgUrl` next, then the fallback placeholder) is unaffected, since a 404 from `/images/...` triggers the same `onerror` path a failed Scryfall request already did.

### Testing

- Route test with a temp cache dir (monkeypatched path) and a mocked `httpx`/`requests` call standing in for Scryfall: first request (cache miss) saves the file and returns 200 with the right bytes; second request (cache hit) returns 200 without a second outbound call (assert the mock was called once across both requests).
- A request for an unknown printing (mocked Scryfall 404) returns 404 without creating a cache file.
- A `set_code`/`collector_number` containing `..` or other unsafe characters returns 400, not a filesystem access outside the cache directory.

## Out of Scope

- Cache eviction, size limits, or a management UI — printings are immutable and card image files are small; this isn't expected to grow unreasonably for a personal-scale deployment.
- Any change to the existing static site's own image-caching thread in `web/export.py`.
