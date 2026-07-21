# Multi-User Frontend — Design Spec
_2026-07-21_

## Context

This is sub-project C of the web multi-user rollout (see `docs/superpowers/specs/2026-07-21-web-auth-foundation-design.md`). Sub-project A (backend + Google auth) is built, reviewed, and deployed live at `erskymtg.co.uk`: a whitelisted Google account can log in, and `GET /api/collection`/`GET /api/decks` return that account's live data as JSON. There is no page yet — logging in redirects to `/app`, which currently 404s.

This sub-project gives `/app` an actual page: a per-user Collection/Decks/Missing browser, visually consistent with the existing public static site (`web/static/index.html`), but backed by the authenticated per-request API instead of static JSON files.

Sub-project B (self-service config page, on-demand sync, admin whitelist UI) is separate and not part of this spec.

## Scope

**In scope:** Collection tab (grid, search, colour-group stat tiles), Decks tab (grouped by box, expand/collapse, proxy badges), Missing tab (paste-a-decklist checker, unchanged logic from the static site). A nav bar showing the logged-in email and a logout control.

**Out of scope:** Meta tab (heaviest Scryfall/Moxfield consumer, stays owner/cron-only per the original design spec), For Sale/Wants tab, the lands-cycle breakdown sidebar, any self-service config UI, admin pages. All deferred to a later pass.

## Architecture

```
webapp/
  static/
    app.html         ← new: the authenticated multi-user page
  main.py            ← gains GET /app, GET /api/whoami, GET /
```

`app.html` is a static file (no server-side templating) adapted from `web/static/index.html`: same `:root` CSS palette, `.mtg-card`/badge/overlay styles, `makeCard()` component, and the Collection/Decks/Missing render logic — with the Meta tab, Sale tab, and lands sidebar removed, and the `<nav>` changed to show identity instead of "Last synced".

### Data loading

`app.html`'s `init()` calls `fetch('/api/collection')` and `fetch('/api/decks')` instead of the static site's `fetch('./collection.json')`/`fetch('./decks.json')`. Both are same-origin requests, so the session cookie is sent automatically — no extra auth wiring needed in the frontend. If either call redirects (302 to `/login`, meaning the session expired), the fetch's `response.redirected`/final URL will not be JSON; `init()` treats a non-OK or non-JSON response the same way the static site already treats a missing JSON file — show the existing `#load-error` panel, but re-purposed to say "Session expired — please log in again" with a link to `/login`.

### Card images

The static site downloads and caches Scryfall images locally (`web/export.py`'s background thread) and falls back to Scryfall's live API. This sub-project does not replicate that caching job — `app.html` calls Scryfall's image API directly for every card (`https://api.scryfall.com/cards/{set}/{cn}?format=image&version=normal`, falling back to the by-name endpoint for proxies with no owned printing), exactly like the "missing" cards already do on the static site today. This is simpler and avoids running a new background job per authenticated user; if load time becomes a problem later, caching can be revisited.

### New routes (`webapp/main.py`)

```python
GET /app          -> Depends(require_user); serves webapp/static/app.html
GET /api/whoami   -> Depends(require_user); returns {"email": "<identity>"}
GET /             -> redirect to /app if session valid, else /login
```

`/api/whoami`'s `email` field is derived from the session's `user_id` by splitting on the first `:` and returning the remainder (works uniformly for `google:<email>` and `discord:<id>` — the latter will show the numeric Discord ID, which is acceptable since Discord users aren't expected to reach this page in practice).

### Nav bar

Same tab-strip visual style as the existing site, now three tabs (Collection, Decks, Missing) plus, right-aligned, `{email} · Logout` (fetched from `/api/whoami` on page load). Logout is a small JS handler: `fetch('/logout', {method: 'POST'}).then(() => location.href = '/login')`.

### Error handling

- `/api/collection`/`/api/decks` returning anything other than 200 JSON (expired session, server error): show a re-purposed load-error panel with a "Log in again" link to `/login`, rather than the static site's "run /sync in Discord" message (not applicable here).
- `/api/whoami` failing: nav falls back to showing just "Logout" with no email (non-fatal — the page still functions).

### Testing

`GET /app`, `GET /api/whoami`, and `GET /` are thin route handlers — tested the same way the existing `/api/collection`/`/api/decks` tests work in `tests/test_webapp_main.py` (real signed session cookies via the existing test helper, asserting redirect-when-unauthenticated and correct response when authenticated). `app.html`'s client-side JS is not unit tested (consistent with the existing static site, which has no JS test coverage) — verified manually in a browser against the live VM, the same way sub-project A's login flow was verified.

### Out of scope details

- No changes to `web/export.py`, `webapp/data.py`, the Discord bot, or the static site at `web/static/index.html` (that file is untouched and continues to work for any future re-enablement of the public static mirror).
- No nginx/deployment changes — `erskymtg.co.uk` already proxies to `mtg-web.service` for all paths, so `/app` is reachable as soon as the route exists.
