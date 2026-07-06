# URL Path Routing for Web Site Tabs — Design Spec
_2026-07-06_

## Overview

The site at `web/static/index.html` is a single-page app with 5 tabs (Collection, Decks, Missing, Meta, For Sale) switched purely by JS state — the URL never changes. This adds real paths so specific tabs can be linked directly, e.g. `erskymtg.co.uk/sale`.

## Path mapping

| Path       | Tab          |
|------------|--------------|
| `/`        | collection   |
| `/decks`   | decks        |
| `/missing` | missing      |
| `/meta`    | meta         |
| `/sale`    | sale         |

Unrecognized paths fall back to `collection` (same as `/`).

## Frontend changes (`web/static/index.html`)

- Add a `PATH_TO_TAB` map and its inverse `TAB_TO_PATH`.
- `init()`: derive the initial tab from `location.pathname` (stripping trailing slash) instead of hardcoding `collection`; call `showTab()` for it and mark the matching `.tab` element active.
- New `navigateTab(name, tabEl)`: calls `history.pushState(null, '', TAB_TO_PATH[name])`, then `showTab(name, tabEl)`. Tab `onclick` handlers call this instead of `showTab` directly.
- `window.addEventListener('popstate', ...)`: re-derive the tab from `location.pathname` and call `showTab()` directly (no pushState, to avoid history loops), updating the active `.tab` class.
- `showTab()` itself is unchanged (still just toggles page/tab visibility and lazy-loads Meta data).

## Server changes

The static site is served by nginx on the Oracle VM. Direct navigation to `/sale` etc. must serve `index.html` rather than 404. The original site design spec already called for:

```nginx
location / {
    root /var/www/mtg;
    index index.html;
    try_files $uri $uri/ /index.html;
}
```

This work verifies that config is actually live on the VM (via SSH) and corrects it if not — no other server-side changes.

## Out of scope

- Deep-linking into Meta's per-format sub-tabs or Missing's submitted-list state — only the 5 top-level tabs get paths.
- Any change to `export.py`, JSON schemas, or Discord bot code.
