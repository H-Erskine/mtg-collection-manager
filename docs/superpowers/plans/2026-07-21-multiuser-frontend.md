# Multi-User Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the authenticated web app a real page — `/app` shows a per-user Collection/Decks/Missing browser backed by the live `/api/collection`/`/api/decks` endpoints, so logging in no longer dead-ends on a 404.

**Architecture:** A new static file `webapp/static/app.html` (adapted from the existing public site's `web/static/index.html`, trimmed to 3 tabs) is served by a new `GET /app` route gated by the existing `require_user` dependency. A small `GET /api/whoami` route exposes the session's identity for the nav bar, and `GET /` redirects to `/app` or `/login` depending on session state.

**Tech Stack:** FastAPI (existing `webapp` package), vanilla HTML/CSS/JS (no build step, matching the existing static site's approach), pytest + FastAPI `TestClient`.

## Global Constraints

- No changes to `web/export.py`, `webapp/data.py`, the Discord bot, or `web/static/index.html` — this plan only adds to `webapp/`.
- No local image caching — card images load directly from Scryfall's API (`https://api.scryfall.com/cards/{set}/{cn}?format=image&version=normal`, falling back to `https://api.scryfall.com/cards/named?exact={name}&format=image&version=normal`), same as the existing site's fallback path for proxy cards.
- Meta tab, For Sale/Wants tab, and the lands-cycle sidebar are explicitly out of scope — do not port them.
- `/app`, `/api/whoami` must both be gated by the existing `require_user` dependency (redirect to `/login` when unauthenticated), same behavior as `/api/collection`/`/api/decks`.
- No nginx/deployment changes — `erskymtg.co.uk` already proxies every path to `mtg-web.service`.

---

## Task 1: `/api/whoami` and `/` routes

**Files:**
- Modify: `webapp/main.py`
- Modify: `tests/test_webapp_main.py`

**Interfaces:**
- Consumes: `require_user` (existing, `webapp/deps.py`), `Config` (existing).
- Produces: `GET /api/whoami` → `{"email": "<identity>"}` for an authenticated session; `GET /` → 302 redirect to `/app` (authenticated) or `/login` (not).

- [ ] **Step 1: Write failing tests in `tests/test_webapp_main.py`**

Add these tests to the existing file (it already has a `_client(tmp_path, monkeypatch)` helper and uses `client.session_transaction()`-style session signing — follow the exact same pattern already used by `test_api_collection_returns_data_when_logged_in` in that file for constructing an authenticated session):

```python
def test_whoami_redirects_when_not_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/whoami", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_whoami_returns_email_when_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/whoami")

    assert response.status_code == 200
    assert response.json() == {"email": "alice@example.com"}


def test_root_redirects_to_login_when_not_authenticated(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_root_redirects_to_app_when_authenticated(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/app"
```

Note: `tests/test_webapp_main.py` already defines a `_client(tmp_path, monkeypatch)` fixture-style helper function used by the existing collection/decks tests — read the existing file first and reuse that exact helper rather than redefining it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_main.py -v -k "whoami or root_redirects"`
Expected: FAIL — `/api/whoami` and `/` don't exist yet (404), or the redirect assertions fail.

- [ ] **Step 3: Implement the routes in `webapp/main.py`**

Add to `webapp/main.py` (alongside the existing `/api/collection`/`/api/decks` routes):

```python
@app.get("/api/whoami")
async def api_whoami(request: Request, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    return {"email": user_id.split(":", 1)[1]}


@app.get("/")
async def root(request: Request):
    user_id = request.session.get("user_id")
    if user_id and get_user_config(user_id) is not None:
        return RedirectResponse(url="/app", status_code=302)
    return RedirectResponse(url="/login", status_code=302)
```

This requires importing `get_user_config` from `api.users` at the top of `webapp/main.py` (it's already imported transitively via `webapp.deps`, but import it directly here too since `root()` calls it explicitly):

```python
from api.users import get_user_config, seed_owner_whitelist
```

(adjust the existing `from api.users import seed_owner_whitelist` import line to include `get_user_config` alongside it, rather than adding a second import line for the same module).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_main.py -v`
Expected: All tests pass, including the 4 new ones.

- [ ] **Step 5: Commit**

```bash
git add webapp/main.py tests/test_webapp_main.py
git commit -m "feat: add /api/whoami and / routes"
```

---

## Task 2: `app.html` static page

**Files:**
- Create: `webapp/static/app.html`

**Interfaces:**
- Produces: a static HTML file at `webapp/static/app.html`, fetched by the `GET /app` route added in Task 3. Fetches `/api/collection`, `/api/decks`, `/api/whoami` client-side (same-origin, cookies sent automatically). No server-side templating — this is a plain static file.

- [ ] **Step 1: Create `webapp/static/app.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MTG Collection</title>
<style>
  :root {
    --bg: #1a1a2e;
    --surface: #16213e;
    --surface2: #0f3460;
    --accent: #e94560;
    --text: #eaeaea;
    --muted: #888;
    --green: #4caf50;
    --red: #e94560;
    --gold: #f0c040;
    --radius: 8px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; min-height: 100vh; }

  nav { background: var(--surface); border-bottom: 2px solid var(--surface2); padding: 0 24px; display: flex; align-items: center; gap: 0; }
  .nav-brand { font-weight: 700; font-size: 1.1rem; color: var(--gold); padding: 16px 24px 16px 0; border-right: 1px solid var(--surface2); margin-right: 8px; }
  .tab { padding: 16px 20px; cursor: pointer; border-bottom: 3px solid transparent; color: var(--muted); font-size: 0.95rem; transition: color 0.15s, border-color 0.15s; user-select: none; }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--text); border-bottom-color: var(--accent); }
  .nav-meta { margin-left: auto; font-size: 0.8rem; color: var(--muted); }
  .nav-logout { color: var(--text); text-decoration: underline; cursor: pointer; margin-left: 8px; }
  .nav-logout:hover { color: var(--accent); }

  .page { display: none; padding: 24px; max-width: 1400px; margin: 0 auto; }
  .page.active { display: block; }

  .load-error { background: #3a1a1a; border: 1px solid var(--red); border-radius: var(--radius); padding: 20px; margin: 40px auto; max-width: 500px; text-align: center; }
  .load-error h3 { color: var(--red); margin-bottom: 8px; }
  .load-error p { font-size: 0.9rem; color: var(--muted); }
  .load-error a { color: #6ab0f5; }
  .loading { text-align: center; padding: 60px; color: var(--muted); }

  /* COLLECTION */
  .collection-header { display: flex; gap: 8px; align-items: center; margin-bottom: 20px; flex-wrap: wrap; }
  .search { background: var(--surface); border: 1px solid var(--surface2); color: var(--text); padding: 8px 14px; border-radius: var(--radius); font-size: 0.9rem; width: 180px; flex-shrink: 0; }
  .search:focus { outline: none; border-color: var(--accent); }
  .stat { margin-left: auto; font-size: 0.85rem; color: var(--muted); }

  .card-grid { display: flex; flex-wrap: wrap; gap: 10px; }
  .mtg-card { position: relative; width: 130px; flex-shrink: 0; }
  .mtg-card img { width: 130px; height: 181px; border-radius: 6px; display: block; object-fit: cover; }
  .card-fallback { width: 130px; height: 181px; border-radius: 6px; background: var(--surface); border: 1px solid var(--surface2); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; }
  .card-fallback span { font-size: 0.65rem; color: var(--muted); text-align: center; padding: 0 6px; }
  .badge { position: absolute; bottom: 6px; right: 6px; background: rgba(0,0,0,0.85); color: var(--gold); font-size: 0.7rem; font-weight: 700; padding: 2px 6px; border-radius: 4px; }
  .badge.foil { background: linear-gradient(135deg, #b8860b, #daa520); color: #1a1a1a; }
  .badge.green { color: var(--green); }
  .overlay { position: absolute; inset: 0; border-radius: 6px; background: rgba(233,69,96,0.55); display: flex; align-items: center; justify-content: center; }
  .overlay span { background: var(--red); color: white; font-size: 0.65rem; font-weight: 700; padding: 3px 8px; border-radius: 4px; letter-spacing: 0.5px; }
  .mtg-card.in-deck img,
  .mtg-card.in-deck .card-fallback { box-shadow: var(--deck-glow); }

  /* COLLECTION BODY */
  #collection-body { display: flex; gap: 16px; align-items: flex-start; }
  #collection-grid-wrap { flex: 1; min-width: 0; }

  /* STATS BANNER */
  .stats-banner { display: flex; gap: 6px; flex-wrap: wrap; }
  .stat-tile { border-radius: var(--radius); padding: 10px 14px; text-align: center; min-width: 68px; border: 1px solid; cursor: pointer; transition: filter 0.15s; }
  .stat-tile:hover { filter: brightness(1.25); }
  .stat-tile.active { outline: 2px solid var(--accent); outline-offset: 1px; }
  .stat-tile-icon { font-size: 1.2rem; line-height: 1; margin-bottom: 2px; }
  .stat-tile-count { font-size: 1.1rem; font-weight: 700; line-height: 1.2; }
  .stat-tile-label { font-size: 0.7rem; color: var(--muted); margin-top: 2px; }

  /* DECKS */
  .box-section { margin-bottom: 32px; }
  .box-title { font-size: 1rem; font-weight: 700; color: var(--gold); margin-bottom: 14px; padding-bottom: 8px; border-bottom: 1px solid var(--surface2); }
  .deck-item { background: var(--surface); border: 1px solid var(--surface2); border-radius: var(--radius); overflow: hidden; margin-bottom: 10px; }
  .deck-header { padding: 12px 16px; display: flex; align-items: center; gap: 12px; cursor: pointer; user-select: none; }
  .deck-header:hover { background: var(--surface2); }
  .deck-name { font-weight: 600; font-size: 0.95rem; }
  .deck-meta { font-size: 0.78rem; color: var(--muted); margin-top: 2px; }
  .deck-url { font-size: 0.78rem; color: #6ab0f5; text-decoration: none; margin-left: auto; flex-shrink: 0; }
  .deck-url:hover { text-decoration: underline; }
  .chevron { color: var(--muted); font-size: 0.75rem; transition: transform 0.2s; flex-shrink: 0; }
  .deck-item.open .chevron { transform: rotate(90deg); }
  .deck-cards { display: none; padding: 12px 16px 16px; border-top: 1px solid var(--surface2); flex-wrap: wrap; gap: 8px; }
  .deck-item.open .deck-cards { display: flex; }

  /* MISSING */
  .missing-layout { display: grid; grid-template-columns: 320px 1fr; gap: 24px; align-items: start; }
  @media (max-width: 800px) { .missing-layout { grid-template-columns: 1fr; } }
  .input-panel { background: var(--surface); border: 1px solid var(--surface2); border-radius: var(--radius); padding: 20px; position: sticky; top: 20px; }
  .input-panel h3 { margin-bottom: 4px; }
  .hint { font-size: 0.8rem; color: var(--muted); margin-bottom: 12px; }
  textarea { width: 100%; height: 260px; background: var(--bg); border: 1px solid var(--surface2); color: var(--text); padding: 10px 12px; border-radius: var(--radius); font-family: 'Courier New', monospace; font-size: 0.82rem; resize: vertical; }
  textarea:focus { outline: none; border-color: var(--accent); }
  .btn { display: block; width: 100%; padding: 10px; background: var(--accent); color: white; border: none; border-radius: var(--radius); font-size: 0.95rem; font-weight: 600; cursor: pointer; margin-top: 12px; }
  .btn:hover { background: #c73652; }
  .btn-secondary { display: inline-block; padding: 6px 14px; background: var(--surface); border: 1px solid var(--surface2); color: var(--text); border-radius: var(--radius); font-size: 0.82rem; cursor: pointer; }
  .btn-secondary:hover { background: var(--surface2); }
  .results-summary { font-size: 0.85rem; color: var(--muted); margin-bottom: 16px; }
  .results-summary .ok { color: var(--green); font-weight: 600; }
  .results-summary .miss { color: var(--red); font-weight: 600; }
  .section-label { font-size: 0.75rem; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: var(--muted); margin: 16px 0 8px; }
  .placeholder-text { display: flex; align-items: center; justify-content: center; color: var(--muted); font-size: 0.9rem; min-height: 200px; }
</style>
</head>
<body>

<nav>
  <div class="nav-brand">⚔ MTG Collection</div>
  <div class="tab active" data-tab="collection" onclick="showTab('collection', this)">Collection</div>
  <div class="tab" data-tab="decks" onclick="showTab('decks', this)">Decks</div>
  <div class="tab" data-tab="missing" onclick="showTab('missing', this)">Missing</div>
  <div class="nav-meta" id="nav-identity">Loading…</div>
</nav>

<div id="load-error" class="load-error" style="display:none">
  <h3>Session expired</h3>
  <p>Please <a href="/login">log in again</a>.</p>
</div>

<!-- COLLECTION -->
<div id="page-collection" class="page active">
  <div class="loading" id="collection-loading">Loading collection…</div>
  <div id="collection-content" style="display:none">
    <div class="collection-header">
      <input class="search" id="col-search" placeholder="Search cards…" oninput="filterCollection()">
      <div id="collection-stats" class="stats-banner"></div>
      <div class="stat" id="col-stat"></div>
    </div>
    <div id="collection-body">
      <div id="collection-grid-wrap">
        <div class="card-grid" id="collection-grid"></div>
      </div>
    </div>
  </div>
</div>

<!-- DECKS -->
<div id="page-decks" class="page">
  <div class="loading" id="decks-loading">Loading decks…</div>
  <div id="decks-content"></div>
</div>

<!-- MISSING -->
<div id="page-missing" class="page">
  <div class="missing-layout">
    <div class="input-panel">
      <h3>Check a decklist</h3>
      <p class="hint">Paste cards in MTG Arena / MTGO format:<br><code>4 Lightning Bolt</code></p>
      <textarea id="decklist-input" placeholder="4 Lightning Bolt&#10;4 Goblin Guide&#10;20 Mountain"></textarea>
      <button class="btn" onclick="checkMissing()">Check Collection</button>
    </div>
    <div id="missing-results">
      <div class="placeholder-text" id="missing-placeholder">Paste a decklist and hit Check Collection</div>
      <div id="missing-output" style="display:none">
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:4px">
          <div class="results-summary" id="missing-summary" style="margin-bottom:0"></div>
          <button class="btn-secondary" onclick="exportBuyList()" id="export-btn" style="display:none">⬇ Export buy list</button>
        </div>
        <div class="section-label">Missing / Insufficient</div>
        <div class="card-grid" id="missing-grid"></div>
        <div class="section-label" style="margin-top:24px">Owned</div>
        <div class="card-grid" id="owned-grid"></div>
      </div>
    </div>
  </div>
</div>

<script>
let collectionCards = [];
let collectionMap = {};   // lower_name -> { quantity, set_code, collector_number }
let allocatedCards = {};  // lower_name -> [{deckName, color, quantity}]
let activeGroup = 'all';

const COLOUR_TILES = [
  { group: 'White',       icon: '☀️', iconColor: null,      numColor: '#e8e8d0', bg: '#2a2a1e', border: '#4a4a2e', label: 'White' },
  { group: 'Blue',        icon: '💧', iconColor: null,      numColor: '#6ab0f5', bg: '#1a1e2e', border: '#2a3a5e', label: 'Blue' },
  { group: 'Black',       icon: '💀', iconColor: null,      numColor: '#aaaaaa', bg: '#1a1a1a', border: '#2e2e2e', label: 'Black' },
  { group: 'Red',         icon: '🔥', iconColor: null,      numColor: '#e94560', bg: '#2e1a1a', border: '#4e2a2a', label: 'Red' },
  { group: 'Green',       icon: '🌿', iconColor: null,      numColor: '#4caf50', bg: '#1a2e1a', border: '#2a4e2a', label: 'Green' },
  { group: 'Multicolour', icon: '🌈', iconColor: null,      numColor: '#f0c040', bg: '#2e2a10', border: '#5e4a20', label: 'Multi' },
  { group: 'Lands',       icon: '🗺️', iconColor: null,      numColor: '#c8894a', bg: '#1e1208', border: '#3e2418', label: 'Lands' },
  { group: 'Colourless',  icon: '⬡',  iconColor: '#ffffff', numColor: '#b0b0b0', bg: '#1e1e1e', border: '#2e2e2e', label: 'Colorless' },
];

// ─── Tabs ────────────────────────────────────────────────────────────────────

function showTab(name, tabEl) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  tabEl.classList.add('active');
}

// ─── Stats banner ────────────────────────────────────────────────────────────

function buildStatsBanner() {
  const counts = {};
  for (const c of collectionCards) {
    counts[c.color_group] = (counts[c.color_group] || 0) + c.quantity;
  }
  const totalQty = Object.values(counts).reduce((s, v) => s + v, 0);
  const inDeckQty = collectionCards.filter(c => !!allocatedCards[c.name.toLowerCase()]).reduce((s, c) => s + c.quantity, 0);

  const banner = document.getElementById('collection-stats');
  banner.innerHTML = '';

  function makeTile(dataGroup, icon, iconColor, count, numColor, bg, border, label, isActive) {
    const tile = document.createElement('div');
    tile.className = 'stat-tile' + (isActive ? ' active' : '');
    tile.dataset.group = dataGroup;
    tile.style.cssText = `background:${bg};border-color:${border}`;
    const iconStyle = iconColor ? ` style="color:${iconColor}"` : '';
    tile.innerHTML = `
      <div class="stat-tile-icon"${iconStyle}>${icon}</div>
      <div class="stat-tile-count" style="color:${numColor}">${count}</div>
      <div class="stat-tile-label">${label}</div>`;
    tile.onclick = () => setGroup(tile);
    return tile;
  }

  banner.appendChild(makeTile('all', '✦', '#aaaaaa', totalQty, 'var(--text)', '#141420', '#28283e', 'All', activeGroup === 'all'));

  for (const t of COLOUR_TILES) {
    const qty = counts[t.group] || 0;
    banner.appendChild(makeTile(t.group.toLowerCase(), t.icon, t.iconColor, qty, t.numColor, t.bg, t.border, t.label, activeGroup === t.group.toLowerCase()));
  }

  banner.appendChild(makeTile('in-deck', '🃏', null, inDeckQty, '#a88be0', '#1e1428', '#3e2a48', 'In Deck', activeGroup === 'in-deck'));
}

// ─── Data loading ───────────────────────────────────────────────────────────

async function init() {
  try {
    const [colRes, deckRes] = await Promise.all([
      fetch('/api/collection'),
      fetch('/api/decks'),
    ]);
    if (!colRes.ok || !deckRes.ok) throw new Error('data missing');
    const colData = await colRes.json();
    const deckData = await deckRes.json();

    collectionCards = colData.cards;

    const deckColorMap = {};
    for (const deck of deckData.decks) {
      if (!deckColorMap[deck.deck_name]) {
        deckColorMap[deck.deck_name] = Math.floor(Math.random() * 360);
      }
    }

    for (const deck of deckData.decks) {
      const color = deckColorMap[deck.deck_name];
      for (const card of deck.cards) {
        const key = card.name.toLowerCase();
        if (!allocatedCards[key]) allocatedCards[key] = [];
        allocatedCards[key].push({ deckName: deck.deck_name, color, quantity: card.quantity });
      }
    }

    for (const card of collectionCards) {
      const key = card.name.toLowerCase();
      if (!collectionMap[key]) {
        collectionMap[key] = { quantity: 0, set_code: card.set_code, collector_number: card.collector_number };
      }
      collectionMap[key].quantity += card.quantity;
      if (key.includes(' // ')) {
        const frontKey = key.split(' // ')[0].trim();
        if (!collectionMap[frontKey]) collectionMap[frontKey] = collectionMap[key];
      }
    }

    buildStatsBanner();
    renderCollection(collectionCards);
    renderDecks(deckData.decks);
  } catch {
    document.getElementById('load-error').style.display = 'block';
    document.getElementById('collection-loading').style.display = 'none';
    document.getElementById('decks-loading').style.display = 'none';
  }

  loadWhoami();
}

async function loadWhoami() {
  try {
    const res = await fetch('/api/whoami');
    if (!res.ok) throw new Error('whoami failed');
    const data = await res.json();
    const el = document.getElementById('nav-identity');
    el.innerHTML = '';
    el.append(data.email + ' · ');
    const logout = document.createElement('span');
    logout.className = 'nav-logout';
    logout.textContent = 'Logout';
    logout.onclick = doLogout;
    el.appendChild(logout);
  } catch {
    const el = document.getElementById('nav-identity');
    el.innerHTML = '';
    const logout = document.createElement('span');
    logout.className = 'nav-logout';
    logout.textContent = 'Logout';
    logout.onclick = doLogout;
    el.appendChild(logout);
  }
}

function doLogout() {
  fetch('/logout', { method: 'POST' }).then(() => { location.href = '/login'; });
}

// ─── Image helpers ───────────────────────────────────────────────────────────

function scryfallImgUrl(set_code, collector_number) {
  return `https://api.scryfall.com/cards/${set_code}/${collector_number}?format=image&version=normal`;
}

function namedImgUrl(name) {
  return `https://api.scryfall.com/cards/named?exact=${encodeURIComponent(name)}&format=image&version=normal`;
}

function makeCard(name, set_code, collector_number, badgeText, badgeCls, overlayText) {
  const wrap = document.createElement('div');
  wrap.className = 'mtg-card';

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
      fb.className = 'card-fallback';
      fb.innerHTML = `<span style="font-size:1.5rem">🃏</span><span>${name}</span>`;
      wrap.prepend(fb);
    }
  };
  wrap.appendChild(img);

  if (overlayText) {
    const ov = document.createElement('div');
    ov.className = 'overlay';
    ov.innerHTML = `<span>${overlayText}</span>`;
    wrap.appendChild(ov);
  }
  if (badgeText) {
    const b = document.createElement('div');
    b.className = `badge ${badgeCls || ''}`;
    b.textContent = badgeText;
    wrap.appendChild(b);
  }
  return wrap;
}

// ─── Collection tab ──────────────────────────────────────────────────────────

function renderCollection(cards) {
  document.getElementById('collection-loading').style.display = 'none';
  document.getElementById('collection-content').style.display = 'block';
  const grid = document.getElementById('collection-grid');
  grid.innerHTML = '';
  cards.forEach(c => {
    const badge = c.foil ? `✦ ×${c.quantity}` : `×${c.quantity}`;
    const el = makeCard(c.name, c.set_code, c.collector_number, badge, c.foil ? 'foil' : '', null);
    const entries = allocatedCards[c.name.toLowerCase()];
    if (entries) {
      el.classList.add('in-deck');
      const shadows = [];
      let spread = 2;
      for (const e of entries) {
        for (let i = 0; i < e.quantity; i++) {
          shadows.push(`0 0 0 ${spread}px hsl(${e.color}, 75%, 65%)`);
          spread += 2;
          shadows.push(`0 0 0 ${spread}px #1a1a2e`);
          spread += 2;
        }
      }
      shadows.pop();
      shadows.push(`0 0 12px hsla(${entries[entries.length - 1].color}, 75%, 65%, 0.35)`);
      el.style.setProperty('--deck-glow', shadows.join(', '));
      el.title = 'In deck: ' + entries.map(e => `${e.deckName} (×${e.quantity})`).join(', ');
    }
    grid.appendChild(el);
  });
  document.getElementById('col-stat').textContent =
    `${cards.reduce((s, c) => s + c.quantity, 0)} cards · ${cards.length} printings`;
}

function setGroup(el) {
  document.querySelectorAll('#collection-stats .stat-tile').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  activeGroup = el.dataset.group;
  filterCollection();
}

function filterCollection() {
  const q = document.getElementById('col-search').value.toLowerCase();
  const filtered = collectionCards.filter(c => {
    const nameOk = c.name.toLowerCase().includes(q);
    let groupOk;
    if (activeGroup === 'all') groupOk = true;
    else if (activeGroup === 'in-deck') groupOk = !!allocatedCards[c.name.toLowerCase()];
    else groupOk = c.color_group.toLowerCase() === activeGroup;
    return nameOk && groupOk;
  });
  renderCollection(filtered);
}

// ─── Decks tab ───────────────────────────────────────────────────────────────

function renderDecks(decks) {
  document.getElementById('decks-loading').style.display = 'none';
  const container = document.getElementById('decks-content');
  container.innerHTML = '';

  if (!decks.length) {
    container.innerHTML = '<p style="color:var(--muted);padding:40px 0">No decks built yet.</p>';
    return;
  }

  const byBox = {};
  for (const deck of decks) {
    (byBox[deck.box_name] = byBox[deck.box_name] || []).push(deck);
  }

  for (const [boxName, boxDecks] of Object.entries(byBox)) {
    const section = document.createElement('div');
    section.className = 'box-section';
    section.innerHTML = `<div class="box-title">📦 ${boxName}</div>`;

    for (const deck of boxDecks) {
      const total = deck.cards.reduce((s, c) => s + c.quantity, 0);
      const proxies = deck.cards.filter(c => c.is_proxy).reduce((s, c) => s + c.quantity, 0);
      const proxyNote = proxies ? ` · ${proxies} prox${proxies === 1 ? 'y' : 'ies'}` : '';
      const builtDate = deck.built_at ? deck.built_at.slice(0, 10) : '';

      const item = document.createElement('div');
      item.className = 'deck-item';
      item.innerHTML = `
        <div class="deck-header" onclick="toggleDeck(this)">
          <span class="chevron">▶</span>
          <div>
            <div class="deck-name">${deck.deck_name}</div>
            <div class="deck-meta">Built ${builtDate} · ${total} cards${proxyNote}</div>
          </div>
          <a class="deck-url" href="${deck.deck_url}" target="_blank" rel="noopener" onclick="event.stopPropagation()">View list ↗</a>
        </div>
        <div class="deck-cards"></div>`;

      const cardsDiv = item.querySelector('.deck-cards');
      for (const c of deck.cards) {
        const overlay = c.is_proxy ? 'PROXY' : null;
        cardsDiv.appendChild(makeCard(c.name, c.set_code, c.collector_number, `×${c.quantity}`, '', overlay));
      }
      section.appendChild(item);
    }
    container.appendChild(section);
  }
}

function toggleDeck(header) {
  header.closest('.deck-item').classList.toggle('open');
}

// ─── Missing tab ─────────────────────────────────────────────────────────────

function parseDeckList(text) {
  const cards = [];
  for (const raw of text.trim().split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('//') || line.startsWith('#')) continue;
    const m = line.match(/^(\d+)x?\s+(.+?)(?:\s+\(\w+\)\s*\S+\s*)?$/i);
    if (m) cards.push({ qty: parseInt(m[1], 10), name: m[2].trim() });
  }
  return cards;
}

let lastMissingCards = [];

function checkMissing() {
  const text = document.getElementById('decklist-input').value;
  const parsed = parseDeckList(text);
  if (!parsed.length) return;

  const missing = [], owned = [];

  for (const { qty, name } of parsed) {
    const key = name.toLowerCase();
    const entry = collectionMap[key];
    const have = entry ? entry.quantity : 0;
    const set_code = entry ? entry.set_code : null;
    const collector_number = entry ? entry.collector_number : null;

    if (have >= qty) {
      owned.push({ name, qty, have, set_code, collector_number });
    } else {
      missing.push({ name, qty, have, set_code, collector_number });
    }
  }

  lastMissingCards = missing;
  document.getElementById('export-btn').style.display = missing.length ? 'inline-block' : 'none';
  document.getElementById('missing-placeholder').style.display = 'none';
  document.getElementById('missing-output').style.display = 'block';
  document.getElementById('missing-summary').innerHTML =
    `<span class="ok">${owned.length} cards owned</span> · <span class="miss">${missing.length} missing or insufficient</span>`;

  const mg = document.getElementById('missing-grid');
  mg.innerHTML = '';
  for (const c of missing) {
    const label = c.have > 0 ? `${c.have}/${c.qty}` : 'MISSING';
    mg.appendChild(makeCard(c.name, c.set_code, c.collector_number, null, '', label));
  }

  const og = document.getElementById('owned-grid');
  og.innerHTML = '';
  for (const c of owned) {
    og.appendChild(makeCard(c.name, c.set_code, c.collector_number, `×${c.qty}`, 'green', null));
  }
}

function exportBuyList() {
  const lines = lastMissingCards.map(c => `${c.qty - c.have} ${c.name}`);
  const blob = new Blob([lines.join('\n')], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'buy-list.txt';
  a.click();
  URL.revokeObjectURL(url);
}

init();
</script>
</body>
</html>
```

- [ ] **Step 2: Sanity-check the file is well-formed HTML**

Run: `python -c "import html.parser; p = html.parser.HTMLParser(); p.feed(open('webapp/static/app.html', encoding='utf-8').read())"`
Expected: No exception raised (this only checks the HTML parses — it does not execute the JS).

- [ ] **Step 3: Commit**

```bash
git add webapp/static/app.html
git commit -m "feat: add app.html — multi-user Collection/Decks/Missing page"
```

---

## Task 3: `GET /app` route

**Files:**
- Modify: `webapp/main.py`
- Modify: `tests/test_webapp_main.py`

**Interfaces:**
- Consumes: `webapp/static/app.html` (Task 2), `require_user` (existing).
- Produces: `GET /app` → 200 with the file's HTML content for an authenticated session; redirect to `/login` otherwise.

- [ ] **Step 1: Write failing tests in `tests/test_webapp_main.py`**

```python
def test_app_redirects_when_not_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/app", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_app_serves_page_when_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/app")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "MTG Collection" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_main.py -v -k test_app_`
Expected: FAIL — `/app` doesn't exist yet (404).

- [ ] **Step 3: Implement the route in `webapp/main.py`**

Add near the other routes, and import `FileResponse` and `Path` at the top of the file:

```python
from pathlib import Path

from fastapi.responses import FileResponse
```

```python
_STATIC_DIR = Path(__file__).parent / "static"


@app.get("/app")
async def app_page(cfg: Config = Depends(require_user)):
    return FileResponse(_STATIC_DIR / "app.html")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_main.py -v`
Expected: All tests pass.

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass (this plan's 3 tasks combined, plus everything from sub-project A untouched).

- [ ] **Step 6: Commit**

```bash
git add webapp/main.py tests/test_webapp_main.py
git commit -m "feat: add GET /app route serving the multi-user page"
```

---

## Self-Review Notes

- **Spec coverage:** `/api/whoami` (Task 1), `/` redirect (Task 1), `app.html` with Collection/Decks/Missing + nav identity/logout (Task 2), `GET /app` route (Task 3) all covered. Meta/Sale/lands-sidebar exclusion is satisfied by simply not porting that markup/JS from the source file. No local image caching — `app.html` calls Scryfall directly, matching the Global Constraints.
- **Type consistency:** `require_user` returns `Config` consistently across all three tasks' route signatures (`Depends(require_user)`), matching sub-project A's existing routes.
- **No placeholders:** `app.html`'s full content is written out in Task 2 — no "port the rest of the markup" placeholder steps.
