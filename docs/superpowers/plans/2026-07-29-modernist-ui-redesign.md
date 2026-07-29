# Modernist UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the web app's dark, rounded, colorful theme with the Modernist design system (flat, Archivo type, one red accent, zero radius, 2px rules) across all pages, add the in-deck proportion bar, the Meta art bar, and the CardMarket profile link.

**Architecture:** A new shared stylesheet (`webapp/static/modernist.css`) carries the design tokens and reusable components (nav, card grid/thumb, in-deck bar, link-rail, `.seg`, `.table`, buttons, inputs). Each existing page (`app.html`, `config.html`, `admin.html`, `admin-user.html`, `onboarding.html`) links it and keeps its own page-specific layout CSS inline, same as today. Two small backend additions: a `cardmarket_url` per-user config field, and a Meta "art-pick" endpoint that queries Scryfall to choose one card for the deck art bar. No frontend framework, no build step — plain HTML/CSS/vanilla JS throughout, matching the existing architecture.

**Tech Stack:** FastAPI (Python), plain HTML/CSS/JS (`webapp/static/*.html`), SQLite (`api/users.py` registry), httpx for Scryfall calls, pytest for backend tests.

## Global Constraints

- Design tokens (colors, spacing, radius) come verbatim from the handoff's `styles.css` — see spec `docs/superpowers/specs/2026-07-29-modernist-ui-redesign-design.md` for the full token block. Never hand-roll a different hex/px value.
- `--radius-*` is `0px` everywhere — no rounded corners anywhere in this redesign.
- No new JS framework, no build step, no bundler. Vanilla JS only, matching the existing pages.
- Archivo font loaded via Google Fonts `@import` (weights 400/600/800), no other font.
- No icons/illustrations/emoji — text-only labels (the old `⚔`, `📦`, `✦`, `👤` etc. are removed).
- `mtg_manager/`, `api/bot.py`, and the CLI are out of scope — this touches only `webapp/` and `api/users.py` (for the new config field).
- Every task that changes rendering must be manually verified in a running browser (via the `run` skill) against `screenshots/1b-full-direction.png`, not just "should work."

---

### Task 1: Static file serving + shared `modernist.css`

**Files:**
- Modify: `webapp/main.py` (add a static mount)
- Create: `webapp/static/modernist.css`
- Test: manual (browser), no automated test — this is pure CSS/config, covered by Task 14's full pass

**Interfaces:**
- Produces: `/static/modernist.css`, linkable from any page via `<link rel="stylesheet" href="/static/modernist.css">`. Every later task's `<style>` block assumes this is already linked and only writes page-specific overrides/layout on top of it.
- Produces these CSS classes for later tasks to consume: `.nav`, `.nav-brand`, `.nav-tab`, `.nav-meta`, `.nav-logout`, `.nav-second-row`, `.btn`/`.btn-primary`/`.btn-secondary`/`.btn-block`, `.input`, `.seg`/`.seg-opt`, `.section-h`, `.flush-section`, `.table`, `.link-rail`/`.link-rail-item`, `.card-grid`, `.mtg-card`/`.mtg-card-frame`/`.mtg-card-fallback`/`.mtg-card-badge`/`.mtg-card-overlay`/`.mtg-card-name`/`.mtg-card-owner`, `.mtg-card-deckbar-track`/`.mtg-card-deckbar-fill`/`.mtg-card-deck-label`.

- [ ] **Step 1: Add the static mount**

In `webapp/main.py`, near the top where `_STATIC_DIR` is defined (line 57), add the import and mount:

```python
from fastapi.staticfiles import StaticFiles
```

Then directly after the `app = FastAPI(...)` construction (find it near the top of the file, before the route definitions), add:

```python
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
```

Note: `_STATIC_DIR` is defined at line 57 — if the `FastAPI()` app instance is constructed earlier in the file, move this mount call to just after the `_STATIC_DIR` assignment instead, so it doesn't reference a name before it's assigned.

- [ ] **Step 2: Create `webapp/static/modernist.css`**

```css
/* Modernist design system tokens + shared components.
   Values copied verbatim from the design handoff's styles.css. */
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;800&display=swap');

:root {
  --color-bg: #f3f2f2;
  --color-surface: #eae9e9;
  --color-text: #201e1d;
  --color-accent: #ec3013;
  --color-divider: color-mix(in srgb, #201e1d 40%, transparent);

  --color-neutral-100: #f8f4f4;
  --color-neutral-200: #eae7e7;
  --color-neutral-300: #d7d3d3;
  --color-neutral-400: #bab6b6;
  --color-neutral-500: #9b9797;
  --color-neutral-600: #7d7979;
  --color-neutral-700: #605d5d;
  --color-neutral-800: #444141;
  --color-neutral-900: #2d2b2b;

  --color-accent-100: #fff2ef;
  --color-accent-200: #ffe0d9;
  --color-accent-300: #ffc4b8;
  --color-accent-400: #ff9783;
  --color-accent-500: #ff563c;
  --color-accent-600: #dd2b0f;
  --color-accent-700: #ae1800;
  --color-accent-800: #7c1405;
  --color-accent-900: #4d170e;

  --font-heading: "Archivo", system-ui, sans-serif;
  --font-body: "Archivo", system-ui, sans-serif;

  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px; --space-6: 24px; --space-8: 32px;
  --radius-sm: 0px; --radius-md: 0px; --radius-lg: 0px;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--color-bg); color: var(--color-text); font-family: var(--font-body); min-height: 100vh; }
h1, h2, h3, h4, h5, h6 { font-family: var(--font-heading); font-weight: 800; }
a { color: var(--color-accent); }
:focus { outline: none; }
:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 2px; }

.hr { height: 2px; border: 0; background: var(--color-divider); margin: var(--space-4) 0; }

/* — nav — */
.nav { display: flex; align-items: center; height: 56px; padding: 0 var(--space-6); border-bottom: 2px solid var(--color-divider); background: var(--color-bg); }
.nav-brand { font-size: 16px; margin-right: var(--space-6); }
.nav-tab { font-size: 13px; color: color-mix(in srgb, var(--color-text) 55%, transparent); text-decoration: none; margin-right: 28px; height: 100%; display: flex; align-items: center; border-bottom: 2px solid transparent; cursor: pointer; user-select: none; }
.nav-tab:hover { color: var(--color-text); }
.nav-tab.active { color: var(--color-text); border-bottom-color: var(--color-accent); }
.nav-meta { margin-left: auto; font-size: 12px; color: color-mix(in srgb, var(--color-text) 55%, transparent); white-space: nowrap; }
.nav-logout { color: var(--color-text); text-decoration: underline; cursor: pointer; margin-left: var(--space-2); }
.nav-logout:hover { color: var(--color-accent); }
.nav-second-row { border-top: 2px solid var(--color-divider); padding: var(--space-2) var(--space-6); }

/* — buttons / inputs — */
.btn { display: inline-flex; align-items: center; justify-content: flex-start; gap: 6px; cursor: pointer; font-family: var(--font-heading); font-weight: 800; font-size: 14px; border: 1px solid transparent; padding: var(--space-2) var(--space-3); background: transparent; color: var(--color-text); border-radius: 0; }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn-primary { background: var(--color-accent); color: #fff; }
.btn-primary:hover { background: var(--color-accent-600); }
.btn-secondary { border-color: var(--color-divider); }
.btn-secondary:hover { background: color-mix(in srgb, var(--color-text) 7%, transparent); }
.btn-block { width: 100%; margin-top: var(--space-2); }

.input, select.input { width: 100%; min-height: 36px; padding: 6px 10px; font: inherit; font-size: 14px; color: var(--color-text); background: var(--color-surface); border: 1px solid var(--color-divider); border-radius: 0; }
.input:hover { border-color: color-mix(in srgb, var(--color-text) 45%, transparent); }
.input:focus-visible { border-color: var(--color-accent); outline-offset: 0; }
textarea.input { min-height: 90px; resize: vertical; font-family: 'Courier New', monospace; font-size: 12px; }

.seg { display: inline-flex; overflow: hidden; border: 1px solid var(--color-divider); }
.seg-opt { padding: 7px 14px; font-size: 13px; cursor: pointer; color: color-mix(in srgb, var(--color-text) 55%, transparent); background: transparent; border: 0; border-left: 1px solid var(--color-divider); font-family: var(--font-body); }
.seg-opt:first-child { border-left: 0; }
.seg-opt.active { background: var(--color-accent); color: #fff; }
.seg-opt:not(.active):hover { background: color-mix(in srgb, var(--color-text) 7%, transparent); }

/* — flush sections (Config/Admin) — */
.section-h { font-size: 11px; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; color: var(--color-accent-700); padding-bottom: var(--space-2); border-bottom: 1px solid var(--color-divider); margin-bottom: var(--space-3); }
.flush-section { padding: var(--space-4) 0; border-bottom: 1px solid var(--color-divider); }
.flush-section:last-child { border-bottom: none; }

/* — tables — */
.table { width: 100%; border-collapse: collapse; font-size: 14px; }
.table th { text-align: left; font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: color-mix(in srgb, var(--color-text) 60%, transparent); padding: var(--space-2); border-bottom: 2px solid var(--color-divider); }
.table td { padding: var(--space-2); border-bottom: 1px solid var(--color-divider); }
.table tbody tr:hover { background: color-mix(in srgb, var(--color-text) 4%, transparent); }

/* — flush link rail (people/group sidebar) — */
.link-rail { width: 130px; flex-shrink: 0; }
.link-rail-item { display: block; font-size: 13px; padding: 6px 0; color: color-mix(in srgb, var(--color-text) 65%, transparent); cursor: pointer; user-select: none; }
.link-rail-item:hover { color: var(--color-text); }
.link-rail-item.active { color: var(--color-accent-700); font-weight: 600; }

/* — card grid + thumb — */
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(76px, 1fr)); gap: 6px; }
.mtg-card { position: relative; }
.mtg-card-frame { position: relative; aspect-ratio: 5 / 7; border: 1px solid var(--color-divider); overflow: hidden; background: var(--color-neutral-200); }
.mtg-card-frame img { width: 100%; height: 100%; object-fit: cover; display: block; }
.mtg-card-fallback { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; font-size: 9px; text-align: center; padding: 0 4px; color: color-mix(in srgb, var(--color-text) 55%, transparent); }
.mtg-card-badge { position: absolute; top: 4px; right: 4px; background: var(--color-text); color: #fff; font-size: 9px; font-weight: 700; padding: 1px 4px; }
.mtg-card-badge.foil { background: var(--color-accent); }
.mtg-card-overlay { position: absolute; inset: 0; background: color-mix(in srgb, var(--color-accent) 55%, transparent); display: flex; align-items: center; justify-content: center; }
.mtg-card-overlay span { background: var(--color-accent); color: #fff; font-size: 9px; font-weight: 700; padding: 2px 6px; }
.mtg-card-name { font-size: 10px; padding: 3px 4px; background: color-mix(in srgb, var(--color-text) 6%, transparent); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mtg-card-owner { position: absolute; top: 4px; left: 4px; background: var(--color-text); color: #fff; font-size: 9px; padding: 1px 3px; }

/* — in-deck proportion bar (replaces the old glow ring) — */
.mtg-card-deckbar-track { height: 3px; background: var(--color-neutral-300); }
.mtg-card-deckbar-fill { height: 3px; background: var(--color-accent); }
.mtg-card-deck-label { font-size: 9px; color: var(--color-accent-700); padding: 2px 4px 0; }
```

- [ ] **Step 3: Verify the static file serves**

Run: `Skill: run` to start the dev server, then in a second terminal:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/static/modernist.css
```
Expected: `200`

- [ ] **Step 4: Commit**

```bash
git add webapp/main.py webapp/static/modernist.css
git commit -m "feat: add static file mount and Modernist design tokens/components"
```

---

### Task 2: Backend — `cardmarket_url` config field

**Files:**
- Modify: `api/users.py` (schema migration + getter/setter)
- Modify: `webapp/config.py` (GET `/api/config` field + new POST endpoint)
- Test: `tests/test_users.py`

**Interfaces:**
- Consumes: `_registry_conn()` context manager and the `_migrate_*` pattern already in `api/users.py` (see `_migrate_privacy_column` at line 149 for the exact idempotent-migration shape to copy).
- Produces: `get_cardmarket_url(user_id: str) -> str | None`, `set_cardmarket_url(user_id: str, url: str) -> None` in `api/users.py`; `GET /api/config` response gains a `"cardmarket_url"` key; new `POST /api/config/cardmarket` endpoint in `webapp/config.py` that Task 9 (For Sale) and Task 10 (Config page) will call.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_users.py` (follow the existing test file's fixture pattern for an isolated registry DB — check the top of that file for how `_REGISTRY_PATH` is patched per-test before adding these):

```python
def test_cardmarket_url_defaults_to_none():
    ensure_user("user1")
    assert get_cardmarket_url("user1") is None


def test_set_and_get_cardmarket_url():
    ensure_user("user1")
    set_cardmarket_url("user1", "https://www.cardmarket.com/en/Magic/Users/example")
    assert get_cardmarket_url("user1") == "https://www.cardmarket.com/en/Magic/Users/example"
```

Add the corresponding import at the top of `tests/test_users.py`:
```python
from api.users import get_cardmarket_url, set_cardmarket_url
```

(If the test file doesn't already have an `ensure_user` helper, use whatever existing helper the file uses to create a registered user before other `test_set_*` tests — mirror the setup of the nearest existing `set_privacy`/`set_profile` test instead of inventing a new helper.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_users.py -k cardmarket -v`
Expected: FAIL with `ImportError: cannot import name 'get_cardmarket_url'`

- [ ] **Step 3: Add the migration and getter/setter to `api/users.py`**

Add directly after `_migrate_privacy_column` (after line 158):

```python
def _migrate_cardmarket_column(conn: sqlite3.Connection) -> None:
    """One-time addition of the cardmarket_url column for pre-existing registries."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "users" not in tables:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "cardmarket_url" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN cardmarket_url TEXT")
```

In `_registry_conn()` (around line 206), add the call alongside the other migrations:
```python
        _migrate_privacy_column(conn)
        _migrate_cardmarket_column(conn)
```

Add the getter/setter near `set_privacy`/`is_private` (after line 682):
```python
def set_cardmarket_url(user_id: str, url: str) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "UPDATE users SET cardmarket_url = ? WHERE user_id = ?",
            (url.strip(), user_id),
        )


def get_cardmarket_url(user_id: str) -> str | None:
    with _registry_conn() as conn:
        row = conn.execute(
            "SELECT cardmarket_url FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["cardmarket_url"] if row and row["cardmarket_url"] else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_users.py -k cardmarket -v`
Expected: PASS

- [ ] **Step 5: Wire the field into `webapp/config.py`**

Add to the imports (alongside `set_privacy`, `is_private` in the `from api.users import (...)` block at the top):
```python
    get_cardmarket_url,
    set_cardmarket_url,
```

Add `"cardmarket_url": get_cardmarket_url(user_id),` to the `get_config` return dict (line ~101-111), alongside `"is_private": is_private(user_id),`.

Add a new request model near the other `*In` models (find `class PrivacyIn` or similar and place this next to it) and a new endpoint right after `/api/config/privacy` (after line 177's block):

```python
class CardmarketIn(BaseModel):
    cardmarket_url: str


@router.post("/api/config/cardmarket")
async def set_config_cardmarket(request: Request, body: CardmarketIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    set_cardmarket_url(user_id, body.cardmarket_url)
    return {"ok": True}
```

- [ ] **Step 6: Run the full config test suite to confirm nothing broke**

Run: `pytest tests/ -v`
Expected: PASS (all tests, including the two new ones)

- [ ] **Step 7: Commit**

```bash
git add api/users.py webapp/config.py tests/test_users.py
git commit -m "feat: add cardmarket_url config field and save endpoint"
```

---

### Task 3: Backend — Meta art-bar card-pick endpoint

**Files:**
- Modify: `webapp/data.py` (new function) or `webapp/main.py` (new route) — add the route in `main.py` alongside `/api/meta`, and the Scryfall-calling logic in `webapp/data.py` next to `get_meta`
- Test: `tests/test_meta_art_pick.py` (new file)

**Interfaces:**
- Produces: `POST /api/meta/art-pick` — body `{"names": ["Card A", "Card B", ...]}` (the full decklist's card names), returns `{"name": str, "set_code": str|None, "collector_number": str|None}` for the chosen card, or `{"name": None}` if no non-land creature/planeswalker was found. Task 8 (Meta tab) calls this once per deck selection.
- Consumes: `httpx.AsyncClient`, same pattern as `webapp/images.py`'s Scryfall call.

- [ ] **Step 1: Write the failing test**

Create `tests/test_meta_art_pick.py`:

```python
import httpx
import pytest
from fastapi.testclient import TestClient

from webapp.data import pick_art_card


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json


@pytest.mark.asyncio
async def test_pick_art_card_prefers_highest_cmc_creature(monkeypatch):
    fake_collection_response = {
        "data": [
            {"name": "Mountain", "type_line": "Basic Land — Mountain", "cmc": 0},
            {"name": "Goblin Guide", "type_line": "Creature — Goblin", "cmc": 1, "set": "zen", "collector_number": "143"},
            {"name": "Wrenn and Six", "type_line": "Legendary Planeswalker — Wrenn", "cmc": 2, "set": "mh1", "collector_number": "212"},
        ],
        "not_found": [],
    }

    async def fake_post(self, url, json):
        return _FakeResponse(fake_collection_response)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = await pick_art_card(["Mountain", "Goblin Guide", "Wrenn and Six"])
    assert result == {"name": "Wrenn and Six", "set_code": "mh1", "collector_number": "212"}


@pytest.mark.asyncio
async def test_pick_art_card_returns_none_when_no_candidates(monkeypatch):
    fake_collection_response = {
        "data": [{"name": "Mountain", "type_line": "Basic Land — Mountain", "cmc": 0}],
        "not_found": [],
    }

    async def fake_post(self, url, json):
        return _FakeResponse(fake_collection_response)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = await pick_art_card(["Mountain"])
    assert result == {"name": None, "set_code": None, "collector_number": None}
```

Check whether `pytest-asyncio` is already a dependency (grep `requirements-api.txt` / `pyproject.toml` for `asyncio`); if not present, add `pytest-asyncio` to `requirements-api.txt` and add `asyncio_mode = auto` under `[tool.pytest.ini_options]` in `pyproject.toml` (or the project's pytest config file) before running.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_meta_art_pick.py -v`
Expected: FAIL with `ImportError: cannot import name 'pick_art_card' from 'webapp.data'`

- [ ] **Step 3: Implement `pick_art_card` in `webapp/data.py`**

Add `import httpx` to the top of `webapp/data.py` alongside its existing imports. Then add the function near `get_meta` (after its closing line, ~180):

```python
async def pick_art_card(names: list[str]) -> dict:
    """Given a decklist's card names, ask Scryfall's batch collection endpoint
    for type_line/cmc, and pick the highest-CMC non-land creature or
    planeswalker to represent the deck in the Meta art bar. Returns
    {"name": None, ...} if no such card is found (e.g. a land-only deck)."""
    if not names:
        return {"name": None, "set_code": None, "collector_number": None}

    identifiers = [{"name": name} for name in names[:75]]
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.scryfall.com/cards/collection",
            json={"identifiers": identifiers},
        )
    if resp.status_code != 200:
        return {"name": None, "set_code": None, "collector_number": None}

    data = resp.json().get("data", [])
    candidates = [
        c for c in data
        if "Land" not in c.get("type_line", "")
        and ("Creature" in c.get("type_line", "") or "Planeswalker" in c.get("type_line", ""))
    ]
    if not candidates:
        return {"name": None, "set_code": None, "collector_number": None}

    best = max(candidates, key=lambda c: c.get("cmc", 0))
    return {
        "name": best["name"],
        "set_code": best.get("set"),
        "collector_number": best.get("collector_number"),
    }
```

Add the route in `webapp/main.py` next to `/api/meta` (after line 82):

```python
class ArtPickIn(BaseModel):
    names: list[str]


@app.post("/api/meta/art-pick")
async def api_meta_art_pick(body: ArtPickIn, cfg: Config = Depends(require_user)):
    return await pick_art_card(body.names)
```

Add `from webapp.data import pick_art_card` and `from pydantic import BaseModel` to `main.py`'s imports if not already present (check the existing import block first — `get_meta` is likely already imported from `webapp.data`, so add `pick_art_card` to that same import line).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_meta_art_pick.py -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add webapp/data.py webapp/main.py tests/test_meta_art_pick.py requirements-api.txt pyproject.toml
git commit -m "feat: add Scryfall-backed art-card picker for Meta deck view"
```

---

### Task 4: `app.html` — Nav rewrite + auth-gated tab placeholders

**Files:**
- Modify: `webapp/static/app.html` (style block, nav markup, `applyAuthState`, `init`)

**Interfaces:**
- Consumes: `.nav`, `.nav-brand`, `.nav-tab`, `.nav-meta`, `.nav-logout`, `.nav-second-row` from `modernist.css` (Task 1).
- Produces: nav markup that never hides tabs; each gated page (Decks, Missing, Meta, Config) shows a "log in to view this" placeholder instead of an infinite spinner when `isAuthenticated` is false. Later tasks (5-9) render inside these same page containers and must preserve the placeholder's sibling `<div>` ids.

- [ ] **Step 1: Link the stylesheet and strip the old inline tokens**

In `webapp/static/app.html`, add right after the `<title>` tag (line 6):
```html
<link rel="stylesheet" href="/static/modernist.css">
```

Delete the old `:root { ... }` block (lines 8-19) and the `* { box-sizing... }` / `body { ... }` rules (lines 20-21) — both now come from `modernist.css`.

- [ ] **Step 2: Replace the nav CSS rules (lines 23-30) with**

```css
.load-error { border: 1px solid var(--color-accent); padding: var(--space-6); margin: 40px auto; max-width: 500px; text-align: center; }
.load-error h3 { color: var(--color-accent-700); margin-bottom: var(--space-2); }
.load-error p { font-size: 13px; color: color-mix(in srgb, var(--color-text) 55%, transparent); }
.loading { text-align: center; padding: 60px; color: color-mix(in srgb, var(--color-text) 55%, transparent); }
.auth-gate { text-align: center; padding: 60px; color: color-mix(in srgb, var(--color-text) 55%, transparent); }
.auth-gate a { color: var(--color-accent-700); }

.page { display: none; padding: var(--space-6); max-width: 1400px; margin: 0 auto; }
.page.active { display: block; }
```

(Keep the rest of the `<style>` block below this untouched for now — later tasks rewrite each tab's section-specific rules.)

- [ ] **Step 3: Replace the nav markup (lines 160-169)**

```html
<nav class="nav">
  <div class="nav-brand">MTG Collection</div>
  <div class="nav-tab active" data-tab="collection" onclick="showTab('collection', this)">Collection</div>
  <div class="nav-tab" data-tab="decks" onclick="showTab('decks', this)">Decks</div>
  <div class="nav-tab" data-tab="missing" onclick="showTab('missing', this)">Missing</div>
  <div class="nav-tab" data-tab="meta" onclick="showTab('meta', this)">Meta</div>
  <div class="nav-tab" data-tab="sale" onclick="showTab('sale', this)">For Sale</div>
  <a class="nav-tab" href="/config">Config</a>
  <div class="nav-meta" id="nav-identity">Loading…</div>
</nav>
```

- [ ] **Step 4: Add an auth-gate placeholder to each gated page**

Immediately inside `#page-decks` (after line 198's opening tag), `#page-missing` (after line 204), `#page-meta` (after line 232), add a sibling div (shown/hidden by `applyAuthState`), e.g. for Decks:
```html
<div id="page-decks" class="page">
  <div class="auth-gate" id="decks-auth-gate" style="display:none">Log in to view your decks.</div>
  <div class="loading" id="decks-loading">Loading decks…</div>
  <div id="decks-content"></div>
</div>
```
Do the same for `#page-missing` (add `<div class="auth-gate" id="missing-auth-gate" style="display:none">Log in to check your collection against a decklist.</div>` as the first child) and `#page-meta` (add `<div class="auth-gate" id="meta-auth-gate" style="display:none">Log in to view meta decks.</div>` as the first child, before the existing `<label>`).

- [ ] **Step 5: Rewrite `applyAuthState` (lines 399-431)**

Replace the whole function body — it no longer hides tabs; instead it shows the auth-gate placeholders and hides the corresponding loading spinners:

```javascript
function applyAuthState(whoamiData) {
  isAuthenticated = !!whoamiData.authenticated;
  const el = document.getElementById('nav-identity');
  el.innerHTML = '';

  if (isAuthenticated) {
    el.append(whoamiData.email + ' · ');
    const logout = document.createElement('span');
    logout.className = 'nav-logout';
    logout.textContent = 'Logout';
    logout.onclick = doLogout;
    el.appendChild(logout);
    return;
  }

  const login = document.createElement('a');
  login.className = 'nav-logout';
  login.href = '/login';
  login.textContent = 'Log in';
  el.appendChild(login);

  document.getElementById('decks-loading').style.display = 'none';
  document.getElementById('decks-auth-gate').style.display = 'block';
  document.getElementById('missing-auth-gate').style.display = 'block';
  document.getElementById('meta-loading').style.display = 'none';
  document.getElementById('meta-auth-gate').style.display = 'block';

  const peopleSidebar = document.getElementById('people-sidebar');
  if (peopleSidebar) peopleSidebar.style.display = 'none';
  const salePeopleSidebar = document.getElementById('sale-people-sidebar');
  if (salePeopleSidebar) salePeopleSidebar.style.display = 'none';
}
```

- [ ] **Step 6: Guard `showTab` against loading Meta for anonymous users**

In `showTab` (line 341-350), the `if (name === 'meta' && !metaLoaded)` branch would still try to `loadMeta()` for an anonymous visitor clicking the Meta tab. Guard it:
```javascript
function showTab(name, tabEl) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  tabEl.classList.add('active');
  if (name === 'meta' && !metaLoaded && isAuthenticated) {
    metaLoaded = true;
    loadMeta();
  }
}
```
(Note the `.tab` → `.nav-tab` class rename in the `querySelectorAll` call, matching Step 3's markup change. Also update `selectTabFromHash` at line 354, which queries `.tab[data-tab="${name}"]` — change to `.nav-tab[data-tab="${name}"]`.)

- [ ] **Step 7: Manual verification**

Run: `Skill: run` to start the dev server.
- Visit `/app` while logged out: confirm all 6 tabs are visible and clickable, and clicking Decks/Missing/Meta shows the "Log in to…" message instead of a stuck spinner.
- Log in, confirm the placeholders never appear and each tab loads its normal content.

- [ ] **Step 8: Commit**

```bash
git add webapp/static/app.html
git commit -m "feat: persistent Modernist nav with all tabs always visible"
```

---

### Task 5: `app.html` — Collection tab

**Files:**
- Modify: `webapp/static/app.html` (style block, Collection markup, `buildStatsBanner`→counts row, `renderCollection`'s in-deck rendering, `renderPeopleSidebar`)

**Interfaces:**
- Consumes: `.card-grid`/`.mtg-card*` and `.link-rail`/`.link-rail-item` from `modernist.css`.
- Produces: no new interfaces for other tasks — Decks/Missing/Meta/Sale (Tasks 6-9) each build their own grids using the same `.mtg-card-*` classes but call their own render functions.

- [ ] **Step 1: Replace the Collection CSS block** (the `/* COLLECTION */` through `/* COLLECTION BODY */` sections, lines 41-63)

```css
.collection-header { display: flex; flex-direction: column; }
.search-row { border-top: 2px solid var(--color-divider); padding: var(--space-2) 0; }
.counts-row { display: flex; border-bottom: 1px solid var(--color-divider); padding-bottom: var(--space-3); margin-bottom: var(--space-4); }
.count-cell { flex: 1; text-align: center; padding: 0 var(--space-2); border-left: 1px solid var(--color-divider); cursor: pointer; }
.count-cell:first-child { border-left: none; }
.count-num { font-family: var(--font-heading); font-weight: 800; font-size: 28px; line-height: 1; color: var(--color-text); }
.count-num.accent { color: var(--color-accent); }
.count-label { font-size: 11px; text-transform: uppercase; color: color-mix(in srgb, var(--color-text) 55%, transparent); margin-top: var(--space-1); }
.count-cell.active .count-num { color: var(--color-accent); }

#collection-body { display: flex; gap: var(--space-4); align-items: flex-start; }
#collection-grid-wrap { flex: 1; min-width: 0; }
```

- [ ] **Step 2: Replace the Collection HTML** (lines 176-195)

```html
<!-- COLLECTION -->
<div id="page-collection" class="page active">
  <div class="loading" id="collection-loading">Loading collection…</div>
  <div id="collection-content" style="display:none">
    <div class="search-row">
      <input class="input" id="col-search" placeholder="Search cards…" oninput="filterCollection()">
    </div>
    <div class="counts-row" id="collection-stats"></div>
    <div id="collection-body">
      <div class="link-rail" id="people-sidebar">
        <div id="people-list"></div>
      </div>
      <div id="collection-grid-wrap">
        <div class="card-grid" id="collection-grid"></div>
      </div>
    </div>
  </div>
</div>
```

Note: the old `.stat` element (`#col-stat`, the "N cards · M printings" text) had no home in the new counts-row design — drop the `<div class="stat" id="col-stat">` element and its one write site in `renderCollection` (see Step 5).

- [ ] **Step 3: Rewrite `buildStatsBanner` (lines 360-393) into the counts row**

```javascript
function buildStatsBanner() {
  const counts = {};
  for (const c of collectionCards) {
    counts[c.color_group] = (counts[c.color_group] || 0) + c.quantity;
  }
  const totalQty = Object.values(counts).reduce((s, v) => s + v, 0);
  const inDeckQty = collectionCards.filter(c => !!allocatedCards[c.name.toLowerCase()]).reduce((s, c) => s + c.quantity, 0);

  const banner = document.getElementById('collection-stats');
  banner.innerHTML = '';

  function makeCell(dataGroup, count, label, accent, isActive) {
    const cell = document.createElement('div');
    cell.className = 'count-cell' + (isActive ? ' active' : '');
    cell.dataset.group = dataGroup;
    cell.innerHTML = `
      <div class="count-num${accent ? ' accent' : ''}">${count}</div>
      <div class="count-label">${label}</div>`;
    cell.onclick = () => setGroup(cell);
    return cell;
  }

  banner.appendChild(makeCell('all', totalQty, 'All', true, activeGroup === 'all'));
  for (const t of COLOUR_TILES) {
    const qty = counts[t.group] || 0;
    banner.appendChild(makeCell(t.group.toLowerCase(), qty, t.label, false, activeGroup === t.group.toLowerCase()));
  }
  banner.appendChild(makeCell('in-deck', inDeckQty, 'In Deck', true, activeGroup === 'in-deck'));
}
```

`setGroup` (line 597) already reads `el.dataset.group` and calls `filterCollection()` — no change needed there.

- [ ] **Step 4: Rewrite `makeCard` (lines 518-555) to use the new thumb markup**

```javascript
function makeCard(name, set_code, collector_number, badgeText, badgeCls, overlayText) {
  const wrap = document.createElement('div');
  wrap.className = 'mtg-card';

  const frame = document.createElement('div');
  frame.className = 'mtg-card-frame';

  const hasSetCn = set_code && collector_number;
  const src = hasSetCn ? scryfallImgUrl(set_code, collector_number) : namedImgUrl(name);
  const img = document.createElement('img');
  img.src = src;
  img.alt = name;
  img.loading = 'lazy';
  img.onerror = () => {
    if (hasSetCn && !img.dataset.triedNamed) {
      img.dataset.triedNamed = '1';
      img.src = namedImgUrl(name);
    } else {
      img.remove();
      const fb = document.createElement('div');
      fb.className = 'mtg-card-fallback';
      fb.textContent = name;
      frame.appendChild(fb);
    }
  };
  frame.appendChild(img);

  if (overlayText) {
    const ov = document.createElement('div');
    ov.className = 'mtg-card-overlay';
    ov.innerHTML = `<span>${overlayText}</span>`;
    frame.appendChild(ov);
  }
  if (badgeText) {
    const b = document.createElement('div');
    b.className = `mtg-card-badge ${badgeCls || ''}`;
    b.textContent = badgeText;
    frame.appendChild(b);
  }
  wrap.appendChild(frame);

  const nameStrip = document.createElement('div');
  nameStrip.className = 'mtg-card-name';
  nameStrip.textContent = name;
  wrap.appendChild(nameStrip);

  return wrap;
}
```

Note: `badgeCls` is still passed the string `'foil'` or `'green'` by callers (`renderCollection`, `makeSaleCard`, `renderMetaDeckView`). `'green'` no longer has a corresponding CSS rule (the old dark theme used it for the meta "owned" badge) — that's fine, it renders as the default ink badge in the new design; no caller changes needed for this task.

- [ ] **Step 5: Rewrite the in-deck rendering in `renderCollection` (lines 559-595)**

Replace the whole function:

```javascript
function renderCollection(cards) {
  document.getElementById('collection-loading').style.display = 'none';
  document.getElementById('collection-content').style.display = 'block';
  const grid = document.getElementById('collection-grid');
  grid.innerHTML = '';
  cards.forEach(c => {
    const badge = c.foil ? `✦ ×${c.quantity}` : `×${c.quantity}`;
    const el = makeCard(c.name, c.set_code, c.collector_number, badge, c.foil ? 'foil' : '', null);
    if (c.owner_icon) {
      const ownerBadge = document.createElement('div');
      ownerBadge.className = 'mtg-card-owner';
      ownerBadge.textContent = c.owner_icon;
      el.querySelector('.mtg-card-frame').appendChild(ownerBadge);
    }
    const entries = allocatedCards[c.name.toLowerCase()];
    if (entries) {
      const inDeckQty = entries.reduce((s, e) => s + e.quantity, 0);
      const pct = Math.min(100, Math.round((inDeckQty / c.quantity) * 100));

      const track = document.createElement('div');
      track.className = 'mtg-card-deckbar-track';
      const fill = document.createElement('div');
      fill.className = 'mtg-card-deckbar-fill';
      fill.style.width = `${pct}%`;
      track.appendChild(fill);
      el.insertBefore(track, el.querySelector('.mtg-card-name'));

      const label = document.createElement('div');
      label.className = 'mtg-card-deck-label';
      label.textContent = `${inDeckQty}/${c.quantity} in deck`;
      el.appendChild(label);

      el.title = 'In deck: ' + entries.map(e => `${e.deckName} (×${e.quantity})`).join(', ');
    }
    grid.appendChild(el);
  });
}
```

Note the removed `document.getElementById('col-stat').textContent = ...` line from Step 2 — that element no longer exists.

- [ ] **Step 6: Rewrite `renderPeopleSidebar` (lines 626-654) to use `.link-rail-item`**

```javascript
function renderPeopleSidebar() {
  const list = document.getElementById('people-list');
  list.innerHTML = '';

  function makeItem(scope, label) {
    const item = document.createElement('div');
    item.className = 'link-rail-item' + (viewScope === scope ? ' active' : '');
    item.textContent = label;
    item.onclick = () => setViewScope(scope);
    return item;
  }

  list.appendChild(makeItem('me', 'Just Me'));
  list.appendChild(makeItem('all', 'All'));

  if (combinedData) {
    for (const p of combinedData.people) {
      list.appendChild(makeItem(p.user_id, p.display_name));
    }
  }
}
```

(This assumes the original function's body beyond the tile-building loop — the `combinedData.people` iteration — matched this shape; check the current lines 626-654 for any group-scoping logic beyond what's shown here and preserve it, only swapping `person-tile`/`person-icon` markup for the plain `link-rail-item` text label.)

- [ ] **Step 7: Manual verification**

Run: `Skill: run`, log in, open Collection tab.
- Confirm the counts row shows one cell per color group with the right numbers, clicking a cell filters the grid and highlights the cell.
- Confirm the link-rail on the left shows "Just Me" / "All" / group members, switching between them changes the grid.
- Find a card allocated to a deck at less than full quantity (e.g. 1 of 2 copies) and one at full quantity (e.g. 4 of 4) — confirm the proportion bar visibly differs (partial fill vs. full fill) and the "{in_deck}/{owned} in deck" label is correct for both.

- [ ] **Step 8: Commit**

```bash
git add webapp/static/app.html
git commit -m "feat: restyle Collection tab to Modernist, add in-deck proportion bar"
```

---

### Task 6: `app.html` — Decks tab

**Files:**
- Modify: `webapp/static/app.html` (style block, `renderDecks`)

**Interfaces:**
- Consumes: `.card-grid`/`.mtg-card*` (Task 5's `makeCard`), `.hr`.

- [ ] **Step 1: Replace the Decks CSS block** (`/* DECKS */`, lines 93-106)

```css
.box-section { margin-bottom: var(--space-8); }
.box-title { font-family: var(--font-heading); font-weight: 800; font-size: 15px; margin-bottom: var(--space-3); padding-bottom: var(--space-2); border-bottom: 1px solid var(--color-divider); }
.deck-item { border-bottom: 1px solid var(--color-divider); }
.deck-header { padding: var(--space-3) 0; display: flex; align-items: center; gap: var(--space-3); cursor: pointer; user-select: none; }
.deck-name { font-weight: 600; font-size: 14px; }
.deck-meta { font-size: 12px; color: color-mix(in srgb, var(--color-text) 55%, transparent); margin-top: 2px; }
.deck-url { font-size: 12px; color: var(--color-accent-700); text-decoration: none; margin-left: auto; flex-shrink: 0; }
.deck-url:hover { text-decoration: underline; }
.chevron { color: color-mix(in srgb, var(--color-text) 55%, transparent); font-size: 11px; transition: transform 0.15s; flex-shrink: 0; }
.deck-item.open .chevron { transform: rotate(90deg); }
.deck-cards { display: none; padding: 0 0 var(--space-4) 24px; }
.deck-item.open .deck-cards { display: block; }
```

- [ ] **Step 2: Update `renderDecks` (lines 676-724) markup**

Replace the box heading and deck-item template strings — remove the `📦` emoji, and swap `.deck-cards` from a flex wrapper to hold a `.card-grid`:

```javascript
function renderDecks(decks) {
  document.getElementById('decks-loading').style.display = 'none';
  const container = document.getElementById('decks-content');
  container.innerHTML = '';

  if (!decks.length) {
    container.innerHTML = '<p style="color:var(--color-text);opacity:0.55;padding:40px 0">No decks built yet.</p>';
    return;
  }

  const byBox = {};
  for (const deck of decks) {
    (byBox[deck.box_name] = byBox[deck.box_name] || []).push(deck);
  }

  for (const [boxName, boxDecks] of Object.entries(byBox)) {
    const section = document.createElement('div');
    section.className = 'box-section';
    section.innerHTML = `<div class="box-title">${boxName}</div>`;

    for (const deck of boxDecks) {
      const total = deck.cards.reduce((s, c) => s + c.quantity, 0);
      const proxies = deck.cards.filter(c => c.is_proxy).reduce((s, c) => s + c.quantity, 0);
      const proxyNote = proxies ? ` · ${proxies} prox${proxies === 1 ? 'y' : 'ies'}` : '';
      const builtDate = deck.built_at ? deck.built_at.slice(0, 10) : '';

      const item = document.createElement('div');
      item.className = 'deck-item';
      item.innerHTML = `
        <div class="deck-header" onclick="toggleDeck(this)">
          <span class="chevron">&#9656;</span>
          <div>
            <div class="deck-name">${deck.deck_name}</div>
            <div class="deck-meta">Built ${builtDate} · ${total} cards${proxyNote}</div>
          </div>
          <a class="deck-url" href="${deck.deck_url}" target="_blank" rel="noopener" onclick="event.stopPropagation()">View list ↗</a>
        </div>
        <div class="deck-cards"><div class="card-grid"></div></div>`;

      const cardsDiv = item.querySelector('.card-grid');
      for (const c of deck.cards) {
        const overlay = c.is_proxy ? 'PROXY' : null;
        cardsDiv.appendChild(makeCard(c.name, c.set_code, c.collector_number, `×${c.quantity}`, '', overlay));
      }
      section.appendChild(item);
    }
    container.appendChild(section);
  }
}
```

`toggleDeck` (line 726-728) is unchanged.

- [ ] **Step 3: Manual verification**

Run: `Skill: run`, open Decks tab. Confirm boxes group correctly, clicking a deck row expands/collapses with the chevron rotating, and the expanded card grid uses the same thumb style as Collection.

- [ ] **Step 4: Commit**

```bash
git add webapp/static/app.html
git commit -m "feat: restyle Decks tab to Modernist"
```

---

### Task 7: `app.html` — Missing tab

**Files:**
- Modify: `webapp/static/app.html` (style block, Missing HTML)

**Interfaces:**
- Consumes: `.input`, `.btn`/`.btn-primary`/`.btn-secondary`, `.card-grid`.

- [ ] **Step 1: Replace the Missing CSS block** (`/* MISSING */`, lines 108-124)

```css
.missing-layout { display: grid; grid-template-columns: 320px 1fr; gap: var(--space-6); align-items: start; }
@media (max-width: 800px) { .missing-layout { grid-template-columns: 1fr; } }
.input-panel h3 { margin-bottom: var(--space-1); }
.hint { font-size: 12px; color: color-mix(in srgb, var(--color-text) 55%, transparent); margin-bottom: var(--space-3); }
.results-summary { font-size: 13px; color: color-mix(in srgb, var(--color-text) 55%, transparent); margin-bottom: var(--space-4); }
.results-summary .ok { color: var(--color-text); font-weight: 600; }
.results-summary .miss { color: var(--color-accent-700); font-weight: 600; }
.section-label { font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: color-mix(in srgb, var(--color-text) 55%, transparent); margin: var(--space-4) 0 var(--space-2); }
.placeholder-text { display: flex; align-items: center; justify-content: center; color: color-mix(in srgb, var(--color-text) 55%, transparent); font-size: 14px; min-height: 200px; }
```

- [ ] **Step 2: Replace the Missing HTML** (lines 203-229)

```html
<!-- MISSING -->
<div id="page-missing" class="page">
  <div class="auth-gate" id="missing-auth-gate" style="display:none">Log in to check your collection against a decklist.</div>
  <div class="missing-layout">
    <div class="input-panel">
      <h3>Check a decklist</h3>
      <p class="hint">Paste cards in MTG Arena / MTGO format:<br><code>4 Lightning Bolt</code></p>
      <textarea class="input" id="decklist-input" placeholder="4 Lightning Bolt&#10;4 Goblin Guide&#10;20 Mountain"></textarea>
      <label style="display:block;margin-top:var(--space-2);font-size:13px">Can this group complete this?
        <select class="input" id="missing-group-check" onchange="checkMissing()"><option value="">— off —</option></select>
      </label>
      <button class="btn btn-primary btn-block" onclick="checkMissing()">Check Collection</button>
    </div>
    <div id="missing-results">
      <div class="placeholder-text" id="missing-placeholder">Paste a decklist and hit Check Collection</div>
      <div id="missing-output" style="display:none">
        <div style="display:flex;align-items:center;gap:var(--space-3);margin-bottom:4px">
          <div class="results-summary" id="missing-summary" style="margin-bottom:0"></div>
          <button class="btn btn-secondary" onclick="exportBuyList()" id="export-btn" style="display:none">Export buy list</button>
        </div>
        <div class="section-label">Missing / Insufficient</div>
        <div class="card-grid" id="missing-grid"></div>
        <div class="section-label" style="margin-top:var(--space-6)">Owned</div>
        <div class="card-grid" id="owned-grid"></div>
      </div>
    </div>
  </div>
</div>
```

No JS changes needed — `checkMissing`, `parseDeckList`, `exportBuyList` are markup-agnostic.

- [ ] **Step 3: Manual verification**

Run: `Skill: run`, open Missing tab, paste a decklist, click "Check Collection". Confirm the input panel and results grid render in the flat/Modernist style and behave identically to before.

- [ ] **Step 4: Commit**

```bash
git add webapp/static/app.html
git commit -m "feat: restyle Missing tab to Modernist"
```

---

### Task 8: `app.html` — Meta tab + art bar

**Files:**
- Modify: `webapp/static/app.html` (style block, Meta HTML, `renderMetaDeckView`)

**Interfaces:**
- Consumes: `POST /api/meta/art-pick` from Task 3; `.card-grid`/`.mtg-card*`.

- [ ] **Step 1: Replace the Meta CSS block** (`/* META */`, lines 126-155)

```css
#page-meta > label { display: block; margin-bottom: var(--space-4); font-size: 14px; }
.meta-format-tabs { display: flex; gap: var(--space-4); border-bottom: 1px solid var(--color-divider); margin-bottom: var(--space-4); }
.meta-fmt-tab { padding: var(--space-2) 0; font-size: 13px; cursor: pointer; color: color-mix(in srgb, var(--color-text) 55%, transparent); border-bottom: 2px solid transparent; }
.meta-fmt-tab:hover { color: var(--color-text); }
.meta-fmt-tab.active { color: var(--color-accent); border-bottom-color: var(--color-accent); }

.meta-layout { display: grid; grid-template-columns: 230px 1fr; gap: var(--space-6); align-items: start; }
@media (max-width: 800px) { .meta-layout { grid-template-columns: 1fr; } }
.meta-sidebar { max-height: calc(100vh - 160px); overflow-y: auto; }
.meta-section-header { font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; padding: var(--space-2) 0 var(--space-1); border-bottom: 1px solid var(--color-divider); color: color-mix(in srgb, var(--color-text) 55%, transparent); }
.meta-deck-item { padding: var(--space-2) 0; cursor: pointer; border-bottom: 1px solid var(--color-divider); user-select: none; border-left: 3px solid transparent; padding-left: var(--space-2); }
.meta-deck-item:hover { background: color-mix(in srgb, var(--color-text) 4%, transparent); }
.meta-deck-item.active { border-left-color: var(--color-accent); }
.meta-deck-name { font-size: 13px; font-weight: 600; margin-bottom: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.meta-deck-frac { font-size: 11px; color: color-mix(in srgb, var(--color-text) 55%, transparent); margin-bottom: 4px; }
.meta-bar-bg { height: 3px; background: var(--color-neutral-300); }
.meta-bar-fill { height: 3px; background: var(--color-accent); }
.meta-panel { min-height: 300px; }
.meta-deck-header { display: flex; align-items: stretch; gap: var(--space-3); margin-bottom: var(--space-4); }
.meta-deck-info { flex-shrink: 0; }
.meta-deck-title { font-size: 17px; font-weight: 800; margin-bottom: 4px; }
.meta-deck-subtitle { font-size: 12px; color: color-mix(in srgb, var(--color-text) 55%, transparent); display: flex; align-items: center; gap: var(--space-3); }
.meta-deck-link { color: var(--color-accent-700); text-decoration: none; }
.meta-deck-link:hover { text-decoration: underline; }
.meta-art-bar { flex: 1; border: 1px solid var(--color-divider); background: var(--color-neutral-100); overflow: hidden; }
.meta-art-bar img { width: 100%; height: 100%; object-fit: cover; display: block; }
.group-owners { font-size: 10px; color: color-mix(in srgb, var(--color-text) 55%, transparent); }
```

- [ ] **Step 2: Update the Meta HTML** (lines 231-259) — add the auth-gate and the art-bar container

```html
<!-- META -->
<div id="page-meta" class="page">
  <div class="auth-gate" id="meta-auth-gate" style="display:none">Log in to view meta decks.</div>
  <label>Can this group complete this?
    <select class="input" id="meta-group-check" onchange="onMetaGroupCheckToggled()" style="width:auto;display:inline-block"><option value="">— off —</option></select>
  </label>
  <div class="meta-format-tabs" id="meta-format-tabs"></div>
  <div class="loading" id="meta-loading">Loading meta…</div>
  <div class="meta-layout" id="meta-layout" style="display:none">
    <div class="meta-sidebar" id="meta-sidebar"></div>
    <div class="meta-panel" id="meta-panel">
      <div class="placeholder-text" id="meta-placeholder">Select a deck to see its cards</div>
      <div id="meta-deck-view" style="display:none">
        <div class="meta-deck-header">
          <div class="meta-deck-info">
            <div class="meta-deck-title" id="meta-deck-title"></div>
            <div class="meta-deck-subtitle">
              <span id="meta-deck-stats"></span>
              <a class="meta-deck-link" id="meta-deck-link" href="#" target="_blank" rel="noopener">View decklist ↗</a>
            </div>
          </div>
          <div class="meta-art-bar" id="meta-art-bar"></div>
        </div>
        <div class="section-label">Missing / Insufficient</div>
        <div class="card-grid" id="meta-missing-grid"></div>
        <p id="meta-missing-empty" style="display:none;color:var(--color-text);padding:8px 0;font-size:13px">You own all cards in this deck</p>
        <div class="section-label" style="margin-top:var(--space-6)">Owned</div>
        <div class="card-grid" id="meta-owned-grid"></div>
      </div>
    </div>
  </div>
  <div id="meta-empty" style="display:none" class="placeholder-text">No meta decks tracked yet.</div>
</div>
```

- [ ] **Step 3: Wire the art bar into `renderMetaDeckView` (lines 1063-1098)**

Add the art-bar fetch/render at the top of the function, right after the placeholder/view toggling:

```javascript
async function renderMetaDeckView(fmt, deck) {
  document.getElementById('meta-placeholder').style.display = 'none';
  document.getElementById('meta-deck-view').style.display = 'block';

  const artBar = document.getElementById('meta-art-bar');
  artBar.innerHTML = '';
  const artRes = await fetch('/api/meta/art-pick', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ names: deck.cards.map(c => c.name) }),
  });
  if (artRes.ok) {
    const art = await artRes.json();
    if (art.name) {
      const img = document.createElement('img');
      img.src = art.set_code && art.collector_number
        ? scryfallImgUrl(art.set_code, art.collector_number)
        : namedImgUrl(art.name);
      img.alt = art.name;
      artBar.appendChild(img);
    }
  }

  const pct = deck.total_slots ? Math.round(deck.owned_slots / deck.total_slots * 100) : 0;
  const missing = deck.cards.filter(c => c.owned < c.quantity);
  const owned = deck.cards.filter(c => c.owned >= c.quantity);

  document.getElementById('meta-deck-title').textContent = deck.name;
  document.getElementById('meta-deck-stats').textContent =
    `${fmt.charAt(0).toUpperCase() + fmt.slice(1)} · ${deck.owned_slots}/${deck.total_slots} owned (${pct}%)`;
  document.getElementById('meta-deck-link').href = deck.url;

  let ownership = {};
  if (metaGroupCheckEnabled() && missing.length) {
    const needs = missing.map(c => ({ name: c.name, quantity: c.quantity - c.owned }));
    ownership = await annotateGroupOwnership(needs, metaGroupCheckId());
  }

  const mg = document.getElementById('meta-missing-grid');
  mg.innerHTML = '';
  document.getElementById('meta-missing-empty').style.display = missing.length ? 'none' : 'block';
  for (const c of missing) {
    const label = c.owned > 0 ? `${c.owned}/${c.quantity}` : 'MISSING';
    const cardEl = makeCard(c.name, c.set_code, c.collector_number, null, '', label);
    const badge = buildOwnershipBadge(c.name, ownership);
    if (badge) cardEl.appendChild(badge);
    mg.appendChild(cardEl);
  }

  const og = document.getElementById('meta-owned-grid');
  og.innerHTML = '';
  for (const c of owned) {
    og.appendChild(makeCard(c.name, c.set_code, c.collector_number, `×${c.quantity}`, '', null));
  }
}
```

(This drops the `'green'` badge class argument on the owned-grid cards, since that class no longer has a distinct color in the new theme — matches Task 5 Step 4's note.)

- [ ] **Step 4: Manual verification**

Run: `Skill: run`, open Meta tab, select a format, click a deck with at least one non-land creature/planeswalker. Confirm the art bar shows that card's cropped art next to the name/stats block at matching height. Select a deck with only lands/instants/sorceries (if one exists in your tracked meta data) and confirm the art bar area stays blank (no broken image icon).

- [ ] **Step 5: Commit**

```bash
git add webapp/static/app.html
git commit -m "feat: restyle Meta tab to Modernist, add auto-picked deck art bar"
```

---

### Task 9: `app.html` — For Sale tab + CardMarket link

**Files:**
- Modify: `webapp/static/app.html` (style block, For Sale HTML, `init`, `setSaleView`, `buildSaleFilterBanner`, `renderSalePeopleSidebar`)

**Interfaces:**
- Consumes: `GET /api/config`'s `cardmarket_url` field from Task 2; `.seg`/`.seg-opt`, `.link-rail`, `.card-grid`.

- [ ] **Step 1: Replace the Sale CSS block** (`/* SALE */` and `/* PEOPLE SIDEBAR */`, lines 73-91)

```css
.sale-section { margin-bottom: var(--space-8); }
.sale-section-title { font-family: var(--font-heading); font-weight: 800; font-size: 15px; color: var(--color-text); margin-bottom: var(--space-3); padding-bottom: var(--space-2); border-bottom: 1px solid var(--color-divider); }
.sale-header { display: flex; gap: var(--space-3); align-items: center; margin-bottom: var(--space-4); flex-wrap: wrap; }
.sale-disclaimer { font-size: 12px; color: color-mix(in srgb, var(--color-text) 55%, transparent); margin-bottom: var(--space-4); font-style: italic; }
.cardmarket-link { margin-left: auto; font-size: 13px; color: var(--color-accent-700); text-decoration: none; }
.cardmarket-link:hover { text-decoration: underline; }
#sale-body { display: flex; gap: var(--space-4); align-items: flex-start; }
#sale-grid-wrap { flex: 1; min-width: 0; }
```

- [ ] **Step 2: Replace the For Sale HTML** (lines 261-307)

```html
<!-- FOR SALE -->
<div id="page-sale" class="page">
  <div class="loading" id="sale-loading">Loading sale data…</div>
  <div id="sale-content" style="display:none">
    <div class="sale-header">
      <div class="seg" id="sale-toggle">
        <button class="seg-opt active" onclick="setSaleView('for-sale', this)">For Sale</button>
        <button class="seg-opt" onclick="setSaleView('wants', this)">Wants</button>
      </div>
      <input class="input" style="width:auto;flex:1" id="sale-search" placeholder="Search cards…" oninput="filterSale()">
      <a class="cardmarket-link" id="cardmarket-link" href="#" target="_blank" rel="noopener" style="display:none">CardMarket profile ↗</a>
    </div>
    <div class="counts-row" id="sale-color-filter"></div>
    <div id="sale-body">
      <div class="link-rail" id="sale-people-sidebar">
        <div id="sale-people-list"></div>
      </div>
      <div id="sale-grid-wrap">
        <div id="sale-view-forsale">
          <p class="sale-disclaimer">Versions shown are owned copies — the specific printing for sale may vary.</p>
          <div class="sale-section">
            <div class="sale-section-title">For Sale</div>
            <div class="card-grid" id="sale-forsale-grid"></div>
            <p id="sale-forsale-empty" style="display:none;color:color-mix(in srgb, var(--color-text) 55%, transparent);padding:20px 0">No cards listed for sale.</p>
          </div>
          <div class="sale-section">
            <div class="sale-section-title">Extras (own &gt;4 copies)</div>
            <div class="card-grid" id="sale-extras-grid"></div>
            <p id="sale-extras-empty" style="display:none;color:color-mix(in srgb, var(--color-text) 55%, transparent);padding:20px 0">No extras.</p>
          </div>
        </div>
        <div id="sale-view-wants" style="display:none">
          <div class="sale-section">
            <div class="sale-section-title">Wants — Specific Version</div>
            <div class="card-grid" id="wants-specific-grid"></div>
            <p id="wants-specific-empty" style="display:none;color:color-mix(in srgb, var(--color-text) 55%, transparent);padding:20px 0">No specific-version wants.</p>
          </div>
          <div class="sale-section">
            <div class="sale-section-title">Wants — Any Version</div>
            <div class="card-grid" id="wants-any-grid"></div>
            <p id="wants-any-empty" style="display:none;color:color-mix(in srgb, var(--color-text) 55%, transparent);padding:20px 0">No any-version wants.</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
```

Note `.seg-opt` uses `.active` (not the old `.sale-toggle-btn.active`) — update `setSaleView` in Step 4.

- [ ] **Step 3: Wire `cardmarket_url` into `init` (lines 433-502)**

`init` already fetches `/api/config` into `configRes` and reads `.groups` from it (line 448: `myGroups = configRes && configRes.ok ? (await configRes.json()).groups : [];`). Calling `.json()` twice on the same Response throws — capture it once:

```javascript
    const configData = configRes && configRes.ok ? await configRes.json() : null;
    myGroups = configData ? configData.groups : [];
    populateGroupSelects();

    const cardmarketLink = document.getElementById('cardmarket-link');
    if (configData && configData.cardmarket_url) {
      cardmarketLink.href = configData.cardmarket_url;
      cardmarketLink.style.display = '';
    }
```

Replace the original line 448 (`myGroups = configRes && configRes.ok ? (await configRes.json()).groups : [];`) and the `populateGroupSelects();` call on the next line with the block above.

- [ ] **Step 4: Update `setSaleView`, `buildSaleFilterBanner`, `renderSalePeopleSidebar`**

In `setSaleView` (line 793), swap the selector and class name:
```javascript
function setSaleView(view, btnEl) {
  saleView = view;
  document.querySelectorAll('#sale-toggle .seg-opt').forEach(b => b.classList.remove('active'));
  btnEl.classList.add('active');
  document.getElementById('sale-view-forsale').style.display = view === 'for-sale' ? 'block' : 'none';
  document.getElementById('sale-view-wants').style.display = view === 'wants' ? 'block' : 'none';
  saleActiveGroup = 'all';
  buildSaleFilterBanner();
  filterSale();
}
```

In `buildSaleFilterBanner` (line 753-784), swap `makeTile`'s `stat-tile`/`stat-tile-icon` markup for the same `count-cell`/`count-num`/`count-label` shape used in Task 5's `buildStatsBanner` (this reuses the `.counts-row` CSS from Task 5, already linked via `modernist.css`/Task 5's inline rules):
```javascript
function buildSaleFilterBanner() {
  const container = document.getElementById('sale-color-filter');
  container.innerHTML = '';

  const cards = saleView === 'wants'
    ? currentSaleWants()
    : [...currentSaleForSale(), ...currentSaleExtras()];

  const counts = {};
  for (const c of cards) counts[c.color_group] = (counts[c.color_group] || 0) + c.quantity;
  const totalQty = Object.values(counts).reduce((s, v) => s + v, 0);

  function makeCell(dataGroup, count, label, accent, isActive) {
    const cell = document.createElement('div');
    cell.className = 'count-cell' + (isActive ? ' active' : '');
    cell.dataset.group = dataGroup;
    cell.innerHTML = `
      <div class="count-num${accent ? ' accent' : ''}">${count}</div>
      <div class="count-label">${label}</div>`;
    cell.onclick = () => setSaleGroup(cell);
    return cell;
  }

  container.appendChild(makeCell('all', totalQty, 'All', true, saleActiveGroup === 'all'));
  for (const t of COLOUR_TILES) {
    const qty = counts[t.group] || 0;
    container.appendChild(makeCell(t.group.toLowerCase(), qty, t.label, false, saleActiveGroup === t.group.toLowerCase()));
  }
}
```

In `renderSalePeopleSidebar` (line 804), mirror Task 5 Step 6's `link-rail-item` swap:
```javascript
function renderSalePeopleSidebar() {
  const list = document.getElementById('sale-people-list');
  list.innerHTML = '';

  function makeItem(scope, label) {
    const item = document.createElement('div');
    item.className = 'link-rail-item' + (saleViewScope === scope ? ' active' : '');
    item.textContent = label;
    item.onclick = () => setSaleViewScope(scope);
    return item;
  }

  list.appendChild(makeItem('me', 'Just Me'));
  list.appendChild(makeItem('all', 'All'));

  if (combinedSaleData) {
    for (const p of combinedSaleData.people) {
      list.appendChild(makeItem(p.user_id, p.display_name));
    }
  }
}
```

In `makeSaleCard` (line 847-858), swap the owner badge class the same way Task 5 Step 5 did (`owner-badge` → `mtg-card-owner`, appended to `.mtg-card-frame` instead of the wrapper).

- [ ] **Step 5: Manual verification**

Run: `Skill: run`. In Config, leave CardMarket URL empty — confirm no link appears on For Sale. Set a CardMarket URL, reload For Sale — confirm "CardMarket profile ↗" appears and opens the right URL in a new tab. Toggle For Sale/Wants and confirm the `.seg` control and grids still work.

- [ ] **Step 6: Commit**

```bash
git add webapp/static/app.html
git commit -m "feat: restyle For Sale tab to Modernist, add CardMarket profile link"
```

---

### Task 10: `config.html`

**Files:**
- Modify: `webapp/static/config.html` (style block, nav, all sections, `renderPackages`, new CardMarket section JS)

**Interfaces:**
- Consumes: `POST /api/config/cardmarket` from Task 2; `.flush-section`/`.section-h`, `.input`, `.btn`, `.seg`, `.nav`.

- [ ] **Step 1: Link the stylesheet, delete the old tokens**

Add `<link rel="stylesheet" href="/static/modernist.css">` after `<title>`. Delete the `:root`/`*`/`body` rules (lines 8-21), same as Task 4 Step 1.

- [ ] **Step 2: Replace the rest of the `<style>` block (lines 23-53)**

```css
.nav-link { padding: 0 var(--space-3); color: color-mix(in srgb, var(--color-text) 55%, transparent); text-decoration: none; font-size: 13px; height: 100%; display: flex; align-items: center; }
.nav-link:hover { color: var(--color-text); }
.nav-meta { margin-left: auto; font-size: 12px; color: color-mix(in srgb, var(--color-text) 55%, transparent); }
.nav-logout { color: var(--color-text); text-decoration: underline; cursor: pointer; margin-left: var(--space-2); }
.nav-logout:hover { color: var(--color-accent); }

.page { padding: var(--space-6); max-width: 800px; margin: 0 auto; }
h2 { font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--color-accent-700); margin-bottom: var(--space-3); padding-bottom: var(--space-2); border-bottom: 1px solid var(--color-divider); }
label { display: block; font-size: 13px; color: color-mix(in srgb, var(--color-text) 55%, transparent); margin-bottom: 4px; }
input, select { margin-bottom: var(--space-3); }
.row { display: flex; gap: var(--space-2); align-items: center; justify-content: space-between; padding: var(--space-2) 0; border-bottom: 1px solid var(--color-divider); }
.row:last-child { border-bottom: none; }
.msg { font-size: 13px; margin-top: var(--space-2); }
.msg.ok { color: var(--color-text); }
.msg.err { color: var(--color-accent-700); }
.inline-form { display: flex; gap: var(--space-2); align-items: flex-end; }
.inline-form input { margin-bottom: 0; }
.checkbox-row { display: flex; align-items: center; gap: var(--space-2); }
.checkbox-row input { width: auto; margin-bottom: 0; }
.icon-palette { display: flex; flex-wrap: wrap; gap: var(--space-2); margin-bottom: var(--space-2); }
.icon-option { font-size: 20px; padding: 6px 10px; border: 1px solid var(--color-divider); cursor: pointer; }
.icon-option:hover { background: color-mix(in srgb, var(--color-text) 7%, transparent); }
.icon-option.selected { border-color: var(--color-accent); background: var(--color-accent-100); }
.muted { color: color-mix(in srgb, var(--color-text) 55%, transparent); font-size: 13px; margin-bottom: var(--space-2); }
.moxfield-link { color: var(--color-accent-700); text-decoration: none; margin-right: var(--space-3); }
.moxfield-link:hover { text-decoration: underline; }
```

Replace every raw `<input ...>` / `<select ...>` and `<button class="btn" ...>` element in the body with `class="input"` / `class="btn btn-primary"` respectively (the existing `class="btn"` buttons become `class="btn btn-primary"`; there are no secondary buttons on this page).

- [ ] **Step 3: Replace the nav (lines 58-66) and wrap each `<section>` as `.flush-section`**

```html
<nav class="nav">
  <div class="nav-brand">MTG Collection</div>
  <a class="nav-link" href="/app#collection">Collection</a>
  <a class="nav-link" href="/app#decks">Decks</a>
  <a class="nav-link" href="/app#missing">Missing</a>
  <a class="nav-link" href="/config">Config</a>
  <a class="nav-link" id="admin-nav-link" href="/admin" style="display:none">Admin</a>
  <div class="nav-meta" id="nav-identity">Loading…</div>
</nav>
```

Replace every `<section>...</section>` with `<div class="flush-section">...</div>`, and every `<h2>X</h2>` stays as-is (the new `h2` CSS from Step 2 already gives it the flush 11px-uppercase-accent look).

- [ ] **Step 4: Update the Moxfield Packages row (`renderPackages`, lines 188-206) to drop the raw `public_id` code and add a Moxfield link**

```javascript
function renderPackages(packages) {
  const list = document.getElementById('packages-list');
  list.innerHTML = '';
  if (!packages.length) {
    list.innerHTML = '<p class="muted">No packages yet.</p>';
    return;
  }
  for (const p of packages) {
    const row = document.createElement('div');
    row.className = 'row';
    row.innerHTML = `
      <span><strong>${p.color_group}</strong></span>
      <span>
        <a class="moxfield-link" href="https://www.moxfield.com/decks/${encodeURIComponent(p.public_id)}" target="_blank" rel="noopener">View on Moxfield ↗</a>
      </span>`;
    const btn = document.createElement('button');
    btn.className = 'btn btn-secondary';
    btn.textContent = 'Remove';
    btn.onclick = () => removePackage(p.color_group);
    row.children[1].appendChild(btn);
    list.appendChild(row);
  }
}
```

(As decided in the design spec: since the backend only stores `color_group` + `public_id` — no separate "package display name" field exists or is being added — the bold name shown is `color_group`. This satisfies the actual behavioral requirement from the handoff, "don't show the raw public_id as a code string; show a link out instead," without inventing new backend data.)

- [ ] **Step 5: Add the CardMarket section**

Add a new `<div class="flush-section">` right after the Moxfield Packages section (after the closing of that section, before "My Groups"):

```html
<div class="flush-section">
  <h2>CardMarket</h2>
  <p class="muted">Link your CardMarket shop — shown as a profile link on your For Sale page.</p>
  <label>CardMarket profile URL</label>
  <input class="input" id="cardmarket-input" placeholder="https://www.cardmarket.com/en/Magic/Users/...">
  <button class="btn btn-primary" onclick="saveCardmarket()">Save</button>
  <div id="cardmarket-msg" class="msg"></div>
</div>
```

Add to `loadConfig()` (after line 180's `profile-private` line): `document.getElementById('cardmarket-input').value = data.cardmarket_url || '';`

Add a new function near `saveFormats` (after line 459):
```javascript
async function saveCardmarket() {
  const cardmarket_url = document.getElementById('cardmarket-input').value.trim();
  const res = await fetch('/api/config/cardmarket', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cardmarket_url }),
  });
  const msg = document.getElementById('cardmarket-msg');
  msg.textContent = res.ok ? 'Saved.' : 'Failed to save.';
  msg.className = 'msg ' + (res.ok ? 'ok' : 'err');
}
```

- [ ] **Step 6: Replace the Pick List Sort `<select>` with a `.seg` control**

Replace lines 127-137:
```html
<div class="flush-section">
  <h2>Pick List Sort</h2>
  <div class="seg" id="sort-seg">
    <button class="seg-opt active" data-value="colour" onclick="setSort(this)">Colour</button>
    <button class="seg-opt" data-value="alphabetical" onclick="setSort(this)">Alphabetical</button>
    <button class="seg-opt" data-value="set" onclick="setSort(this)">Set</button>
    <button class="seg-opt" data-value="cmc" onclick="setSort(this)">CMC</button>
  </div>
  <div id="sort-msg" class="msg"></div>
</div>
```

Replace `saveSort` (line 474-484) and the `loadConfig` line that sets `sort-select`'s value (line 177) with:
```javascript
function setSort(btnEl) {
  document.querySelectorAll('#sort-seg .seg-opt').forEach(b => b.classList.remove('active'));
  btnEl.classList.add('active');
  saveSort(btnEl.dataset.value);
}

async function saveSort(sort_mode) {
  const res = await fetch('/api/config/sort', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sort_mode }),
  });
  const msg = document.getElementById('sort-msg');
  msg.textContent = res.ok ? 'Saved.' : 'Failed to save.';
  msg.className = 'msg ' + (res.ok ? 'ok' : 'err');
}
```
And in `loadConfig`, replace `document.getElementById('sort-select').value = data.pick_list_sort;` with:
```javascript
  document.querySelectorAll('#sort-seg .seg-opt').forEach(b => {
    b.classList.toggle('active', b.dataset.value === data.pick_list_sort);
  });
```

- [ ] **Step 7: Manual verification**

Run: `Skill: run`, open `/config` while logged in. Confirm every section reads as a flush list with a divider between sections (not boxed cards). Confirm a Moxfield package row shows the color group in bold + a working "View on Moxfield ↗" link (no raw slug/code visible). Set a CardMarket URL and Save, reload the page, confirm it persisted. Click each Pick List Sort option and confirm it saves (check Network tab or reload to see the selection persisted).

- [ ] **Step 8: Commit**

```bash
git add webapp/static/config.html
git commit -m "feat: restyle Config page to Modernist, add CardMarket section"
```

---

### Task 11: `admin.html`

**Files:**
- Modify: `webapp/static/admin.html` (style block, nav, section markup)

**Interfaces:**
- Consumes: `.flush-section`/`.section-h`, `.table`, `.seg`, `.btn`, `.input`, `.nav`. No JS logic changes — `loadAdmin`, `renderUsers`, `renderFailedLogins`, `renderActivity`, `loadWhitelist`, `addWhitelist`, `removeWhitelistedUser` are markup-agnostic and untouched.

- [ ] **Step 1: Link the stylesheet, delete old tokens (mirrors Task 10 Step 1)**

- [ ] **Step 2: Replace the `<style>` block (lines 23-53)**

Same rules as Task 10 Step 2, plus (this page has no icon palette, so omit those rules):
```css
.nav-link { padding: 0 var(--space-3); color: color-mix(in srgb, var(--color-text) 55%, transparent); text-decoration: none; font-size: 13px; height: 100%; display: flex; align-items: center; }
.nav-link:hover { color: var(--color-text); }
.nav-meta { margin-left: auto; font-size: 12px; color: color-mix(in srgb, var(--color-text) 55%, transparent); }
.nav-logout { color: var(--color-text); text-decoration: underline; cursor: pointer; margin-left: var(--space-2); }
.nav-logout:hover { color: var(--color-accent); }

.page { padding: var(--space-6); max-width: 1000px; margin: 0 auto; }
h2 { font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--color-accent-700); margin-bottom: var(--space-3); padding-bottom: var(--space-2); border-bottom: 1px solid var(--color-divider); }
label { display: block; font-size: 13px; color: color-mix(in srgb, var(--color-text) 55%, transparent); margin-bottom: 4px; }
.row { display: flex; gap: var(--space-2); align-items: center; justify-content: space-between; padding: var(--space-2) 0; border-bottom: 1px solid var(--color-divider); }
.row:last-child { border-bottom: none; }
.msg { font-size: 13px; margin-top: var(--space-2); }
.msg.ok { color: var(--color-text); }
.msg.err { color: var(--color-accent-700); }
.inline-form { display: flex; gap: var(--space-2); align-items: flex-end; }
.inline-form input { margin-bottom: 0; }
.checkbox-row { display: flex; align-items: center; gap: var(--space-2); }
.checkbox-row input { width: auto; margin-bottom: 0; }
.empty { color: color-mix(in srgb, var(--color-text) 55%, transparent); font-size: 13px; }
```

- [ ] **Step 3: Replace the nav (lines 58-66) — same pattern as Task 10 Step 3**, and replace each `<section>` with `<div class="flush-section">`, and each `<table id="...">` with `<table class="table" id="...">`.

- [ ] **Step 4: Replace the Activity toggle buttons (lines 98-101) with `.seg`**

```html
<div class="seg" style="margin-bottom:var(--space-3)">
  <button class="seg-opt active" id="activity-toggle" onclick="setActivityFilter('activity')">Activity</button>
  <button class="seg-opt" id="actions-toggle" onclick="setActivityFilter('actions')">Actions</button>
</div>
```

Update `setActivityFilter` (line 188-193) — the `classList.toggle('active', ...)` calls are unchanged since both old and new use an `active` class name; only the CSS class powering the look changes (from `.btn-toggle` to `.seg-opt`), so no JS edit is required here beyond confirming the button elements now carry `class="seg-opt"` instead of `class="btn-toggle active"` / `class="btn-toggle"` in the initial HTML (done in this step).

Replace every remaining `class="btn"` with `class="btn btn-primary"` and `class="btn-secondary"` with `class="btn btn-secondary"`.

- [ ] **Step 5: Manual verification**

Run: `Skill: run`, log in as an admin, open `/admin`. Confirm Users/Whitelist/Failed Logins/Activity render as flush sections with `.table`-styled tables, the Activity/Actions toggle uses the `.seg` look, and every existing action (add/remove whitelist entry, toggle activity filter, click through to a user) still works.

- [ ] **Step 6: Commit**

```bash
git add webapp/static/admin.html
git commit -m "feat: restyle Admin page to Modernist"
```

---

### Task 12: `admin-user.html` (extrapolated — no mockup in the handoff)

**Files:**
- Modify: `webapp/static/admin-user.html` (style block, nav, section markup)

**Interfaces:**
- Consumes: same shared classes as Task 11 (`.flush-section`, `.table`, `.btn`, `.nav`). No JS changes — `loadUserDetail`, `renderPackages`, `renderActivity`, `syncUser` are untouched.

- [ ] **Step 1: Link the stylesheet, delete old tokens.**

- [ ] **Step 2: Replace the `<style>` block (lines 8-47)** with the same rules as Task 11 Step 2, plus the back-link style:

```css
.back-link { display: inline-block; margin-bottom: var(--space-4); color: color-mix(in srgb, var(--color-text) 55%, transparent); text-decoration: none; font-size: 13px; }
.back-link:hover { color: var(--color-text); }
h1 { font-size: 20px; margin-bottom: var(--space-4); }
```

- [ ] **Step 3: Replace the nav (same pattern as Task 11 Step 3), replace `<section>` with `<div class="flush-section">`, `<table>` with `<table class="table">`, and `class="btn"` with `class="btn btn-primary"`.**

- [ ] **Step 4: Manual verification**

Run: `Skill: run`, log in as admin, click through from `/admin`'s Users table to a user's detail page. Confirm it now matches the Modernist look (flush sections, `.table` styling), the back-link works, and "Sync now" still triggers a sync and refreshes the activity table.

- [ ] **Step 5: Commit**

```bash
git add webapp/static/admin-user.html
git commit -m "feat: restyle admin user-detail page to Modernist"
```

---

### Task 13: `onboarding.html` (extrapolated — no mockup in the handoff)

**Files:**
- Modify: `webapp/static/onboarding.html` (style block, section markup)

**Interfaces:**
- Consumes: `.flush-section`, `.input`, `.btn`. No JS changes — `renderIconPalette`, `selectIcon`, `updateFinishState`, `renderPackages`, `addPackage`, `loadPackages`, `finishOnboarding` are untouched.

- [ ] **Step 1: Link the stylesheet, delete old tokens.**

- [ ] **Step 2: Replace the `<style>` block (lines 23-49)**

```css
.page { padding: 40px var(--space-6); max-width: 640px; margin: 0 auto; }
.welcome { margin-bottom: var(--space-6); }
.welcome h1 { font-size: 20px; margin-bottom: var(--space-1); }
.welcome p { color: color-mix(in srgb, var(--color-text) 55%, transparent); font-size: 13px; }
h2 { font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--color-accent-700); margin-bottom: var(--space-3); padding-bottom: var(--space-2); border-bottom: 1px solid var(--color-divider); }
label { display: block; font-size: 13px; color: color-mix(in srgb, var(--color-text) 55%, transparent); margin-bottom: 4px; }
.row { display: flex; gap: var(--space-2); align-items: center; justify-content: space-between; padding: var(--space-2) 0; border-bottom: 1px solid var(--color-divider); }
.row:last-child { border-bottom: none; }
.msg { font-size: 13px; margin-top: var(--space-2); }
.msg.ok { color: var(--color-text); }
.msg.err { color: var(--color-accent-700); }
.inline-form { display: flex; gap: var(--space-2); align-items: flex-end; }
.inline-form input { margin-bottom: 0; }
.icon-palette { display: flex; flex-wrap: wrap; gap: var(--space-2); margin-bottom: var(--space-2); }
.icon-option { font-size: 20px; padding: 6px 10px; border: 1px solid var(--color-divider); cursor: pointer; }
.icon-option:hover { background: color-mix(in srgb, var(--color-text) 7%, transparent); }
.icon-option.selected { border-color: var(--color-accent); background: var(--color-accent-100); }
.finish-row { display: flex; justify-content: flex-end; }
```

- [ ] **Step 3: Replace `<section>` with `<div class="flush-section">`, `class="btn"` (Finish) with `class="btn btn-primary"`, `class="btn-secondary"` (Add package) with `class="btn btn-secondary"`, and every raw `<input>` gets `class="input"`.**

- [ ] **Step 4: Manual verification**

Run: `Skill: run`. Since onboarding only shows for un-onboarded accounts, either use a fresh test account or temporarily flip `onboarded` to `0` for a test user in the registry DB, then visit `/onboarding`. Confirm the welcome header, profile section, and package section render flush with the new theme, and "Finish" completes onboarding and redirects to `/app` as before.

- [ ] **Step 5: Commit**

```bash
git add webapp/static/onboarding.html
git commit -m "feat: restyle onboarding page to Modernist"
```

---

### Task 14: Full verification pass

**Files:** none (verification only)

**Interfaces:** none — this task confirms Tasks 1-13's combined output.

- [ ] **Step 1: Run the full backend test suite**

Run: `pytest tests/ -v`
Expected: all tests PASS.

- [ ] **Step 2: Start the app and walk every page**

Run: `Skill: run`. Using the reference `screenshots/1b-full-direction.png` side-by-side, check for each of the 9 pages (Collection, Decks, Missing, Meta, For Sale, Config, Admin, admin-user detail, onboarding):
- Flat, light background, Archivo type, single red accent, zero rounded corners, 2px section dividers.
- No leftover emoji, no leftover `var(--gold)`/`var(--surface2)`-style dark-theme colors anywhere (grep the files for `#1a1a2e`, `#16213e`, `#0f3460`, `#f0c040` to confirm none remain: `grep -rn "1a1a2e\|16213e\|0f3460\|f0c040" webapp/static/` should return nothing).
- Nav shows all 6 tabs regardless of login state; anonymous visitors see the "log in to view" placeholders on Decks/Missing/Meta instead of a stuck spinner.
- Collection: in-deck proportion bar visibly differs between a partially-allocated card and a fully-allocated one.
- Meta: art bar shows card art for a representative deck and blanks out cleanly for a deck with no eligible candidate.
- For Sale: CardMarket link appears only when the Config field is set.
- Config: Moxfield package rows show bold color-group + "View on Moxfield ↗", no raw `public_id` string visible.
- Keyboard-tab through a form field and a nav tab on any page; confirm the focus ring is the 2px accent outline, not a browser-default blue ring.

- [ ] **Step 3: Fix any visual regressions found, re-run Steps 1-2**

- [ ] **Step 4: Final commit (only if fixes were needed in this task)**

```bash
git add -A
git commit -m "fix: address visual regressions found in Modernist redesign verification pass"
```
