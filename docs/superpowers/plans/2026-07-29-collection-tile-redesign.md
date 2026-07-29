# Collection Tile Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the Collection tab's card tiles to group by card name (not printing), replace the corner quantity badge + thin name-overlay strip with a larger inset name/status plate, and add a "N printings" picker that expands the individual printings inline.

**Architecture:** All markup lives in one static file (`webapp/static/app.html`) with styling in `webapp/static/assets/modernist.css` — there's no build step or component framework. `makeCard()` is a shared DOM-builder function used by every tab (Collection, Sale, Meta, Decks, Wants); it's extended with a new optional `statusText` parameter rather than forked, so the plate restyle applies everywhere consistently while the Collection-specific grouping/expansion logic stays local to `renderCollection()` and its new helpers.

**Tech Stack:** Vanilla JS (no framework), FastAPI static file serving (`webapp/main.py`), plain CSS custom properties (Modernist design tokens in `modernist.css`).

## Global Constraints

- No corner quantity badge on Collection's grouped tiles (spec: "Grouped tile (default, collapsed state)").
- Plate background `var(--color-neutral-100)`, `border-top: 3px solid var(--color-accent)`, status line color `var(--color-accent-700)`.
- Art for a grouped tile is chosen at random from that group's printings, re-picked on every render call (same pattern as the existing extras-tab picker).
- Multi-printing groups get a faint stacked-card edge + a "N printings" button in the old badge's top-right slot; single-printing groups get neither.
- Expanded printing tiles use the *same* new tile design (no corner badge), with status line `{set_code} #{collector_number}{ · ✦ Foil if foil} · ×{quantity}` in that field order.
- `makeCard()`'s existing callers (Sale, Meta, Decks, Wants) must keep working unchanged — extend its signature, don't fork it.
- There is no JS test harness for this static frontend (only Python tests under `tests/` cover backend routes). Verification is manual: run the dev server and check the rendered page in a browser against the approved mockup (https://claude.ai/code/artifact/fca11852-d2b6-453f-9c7a-3a1acdbd016e).

---

## File Structure

- Modify `webapp/static/assets/modernist.css` — replace the name-strip/deckbar rules (lines 108, 111–114) with the new plate, printings-button, and stacked-edge rules.
- Modify `webapp/static/app.html`:
  - `makeCard()` (currently lines 459–506) — swap the `mtg-card-name` strip for the new plate, add optional `statusText` param.
  - `renderCollection()` (currently lines 510–546) — replace with a grouping pass + calls to two new local helpers, `groupCollectionCards()` and `makeGroupedCardTile()`.
  - Add `togglePrintings()` — new function handling the inline expand/collapse.

No new files. No backend/API changes — this is presentation-only over data already returned by existing endpoints (`owned_cards`/`collection.json` equivalent already includes `name`, `set_code`, `collector_number`, `quantity`, `foil`, `color_group`, and (in combined view) `owner_user_id`/`owner_icon`).

---

### Task 1: Restyle shared card CSS — plate, printings button, stacked edge

**Files:**
- Modify: `webapp/static/assets/modernist.css:108,111-114`

**Interfaces:**
- Produces CSS classes consumed by Task 2/3: `.mtg-card-plate`, `.mtg-card-plate-name`, `.mtg-card-plate-status`, `.mtg-card-printings-btn`, `.mtg-card-printings-row`, `.mtg-card.stacked`.

- [ ] **Step 1: Replace the name-strip rule with the plate rules**

Delete line 108 (`.mtg-card-name { ... }`) and replace it with:

```css
.mtg-card-plate { position: absolute; left: 0; right: 0; bottom: 0; background: var(--color-neutral-100); border-top: 3px solid var(--color-accent); padding: 6px 8px 7px; }
.mtg-card-plate-name { font-size: 13px; font-weight: 700; line-height: 1.25; color: var(--color-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mtg-card-plate-status { font-size: 12px; font-weight: 700; color: var(--color-accent-700); margin-top: 2px; }
```

- [ ] **Step 2: Replace the deckbar rules with the printings-button and stacked-edge rules**

Delete lines 111–114 (the `/* — in-deck proportion bar ... */` comment plus the three `.mtg-card-deckbar-*`/`.mtg-card-deck-label` rules) and replace with:

```css
/* — multi-printing indicator (stacked edge + picker button) — */
.mtg-card.stacked { position: relative; }
.mtg-card.stacked::before,
.mtg-card.stacked::after { content: ''; position: absolute; inset: 0; border: 1px solid var(--color-divider); background: var(--color-neutral-300); z-index: -1; }
.mtg-card.stacked::before { top: -3px; left: 3px; right: -3px; bottom: 3px; opacity: 0.55; }
.mtg-card.stacked::after { top: -6px; left: 6px; right: -6px; bottom: 6px; opacity: 0.3; z-index: -2; }
.mtg-card-printings-btn { position: absolute; top: 6px; right: 6px; z-index: 2; background: rgba(32,30,29,0.85); color: #fff; border: 0; font-family: inherit; font-size: 11px; font-weight: 700; padding: 3px 7px; cursor: pointer; letter-spacing: 0.02em; }
.mtg-card-printings-btn:hover { background: var(--color-text); }
.mtg-card-printings-btn:focus-visible { outline: 2px solid #fff; outline-offset: 1px; }

/* — inline printings expansion row — */
.mtg-card-printings-row { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--color-divider); }
```

Note: the stacked-edge pseudo-elements go on `.mtg-card` (the wrapper `<div>` `makeCard()` returns), not on `.mtg-card-frame` — the frame has `overflow: hidden` (needed to clip the `object-fit: cover` image), which would clip the offset pseudo-elements. The wrapper has no `overflow` set, so the negative-`z-index` layers render behind the frame instead of being cut off.

- [ ] **Step 3: Visually sanity-check the CSS in isolation**

Run: `python -m http.server 8000 --directory webapp/static` from the worktree root, then open `http://localhost:8000/assets/modernist.css` in a browser — confirm it loads with no syntax errors (browser will render it as plain text; a parse error would show as garbled/truncated output, but the real check is Task 5's full-page render). This step just confirms the file is well-formed before wiring up the JS that depends on these classes.

Expected: file loads with the new rules visible near the end, no console/network errors.

- [ ] **Step 4: Commit**

```bash
git add webapp/static/assets/modernist.css
git commit -m "style: replace card name-strip/deckbar CSS with inset plate + printings button"
```

---

### Task 2: Extend `makeCard()` with the new plate and an optional status line

**Files:**
- Modify: `webapp/static/app.html:459-506`

**Interfaces:**
- Consumes: CSS classes from Task 1 (`.mtg-card-plate`, `.mtg-card-plate-name`, `.mtg-card-plate-status`).
- Produces: `makeCard(name, set_code, collector_number, badgeText, badgeCls, overlayText, statusText)` — new 7th parameter, optional. When provided, renders a second line inside the plate. All five existing callers (Sale ~line 663, ~784; Meta ~1038, ~1047; Wants ~1106, ~1117) already omit it and keep working unchanged since it's the last positional parameter.

- [ ] **Step 1: Replace the name-strip block with the plate block**

In `makeCard()`, replace:

```javascript
  const nameStrip = document.createElement('div');
  nameStrip.className = 'mtg-card-name';
  nameStrip.textContent = name;
  frame.appendChild(nameStrip);

  wrap.appendChild(frame);

  return wrap;
}
```

with:

```javascript
  const plate = document.createElement('div');
  plate.className = 'mtg-card-plate';
  const plateName = document.createElement('div');
  plateName.className = 'mtg-card-plate-name';
  plateName.textContent = name;
  plate.appendChild(plateName);
  if (statusText) {
    const plateStatus = document.createElement('div');
    plateStatus.className = 'mtg-card-plate-status';
    plateStatus.textContent = statusText;
    plate.appendChild(plateStatus);
  }
  frame.appendChild(plate);

  wrap.appendChild(frame);

  return wrap;
}
```

- [ ] **Step 2: Add the new parameter to the function signature**

Change:

```javascript
function makeCard(name, set_code, collector_number, badgeText, badgeCls, overlayText) {
```

to:

```javascript
function makeCard(name, set_code, collector_number, badgeText, badgeCls, overlayText, statusText) {
```

- [ ] **Step 3: Start the dev server and manually verify existing tabs are unaffected**

Run: `uvicorn webapp.main:app --reload` from the worktree root, then log in and open the Sale, Meta, Decks, and Wants tabs in a browser.

Expected: every card tile now shows the name in a solid plate at the bottom of the art (inset inside the bordered frame) instead of the old thin semi-transparent strip. No second status line appears anywhere yet (all existing callers omit `statusText`). Existing corner badges (price, quantity, foil) still render top-right, unchanged.

- [ ] **Step 4: Commit**

```bash
git add webapp/static/app.html
git commit -m "feat: add optional status line to makeCard's inset plate"
```

---

### Task 3: Group the Collection tab by card name and render the new grouped tile

**Files:**
- Modify: `webapp/static/app.html:510-546` (replace `renderCollection`)

**Interfaces:**
- Consumes: `makeCard(name, set_code, collector_number, badgeText, badgeCls, overlayText, statusText)` from Task 2; module-level `allocatedCards` (lower-name → `[{deckName, quantity}]`, populated in `init()`).
- Produces: `groupCollectionCards(cards)` → `Array<{ name, owner_icon, quantity, printings: Array<card> }>`, consumed by Task 4. `makeGroupedCardTile(group)` → the wrapper `<div class="mtg-card">` element, also consumed by Task 4 (which appends the expansion row as its next sibling).

- [ ] **Step 1: Replace `renderCollection` with the grouped version**

Replace the entire current function:

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
      el.appendChild(track);

      const label = document.createElement('div');
      label.className = 'mtg-card-deck-label';
      label.textContent = `${Math.min(inDeckQty, c.quantity)}/${c.quantity} in deck`;
      el.appendChild(label);

      el.title = 'In deck: ' + entries.map(e => `${e.deckName} (×${e.quantity})`).join(', ');
    }
    grid.appendChild(el);
  });
}
```

with:

```javascript
function renderCollection(cards) {
  document.getElementById('collection-loading').style.display = 'none';
  document.getElementById('collection-content').style.display = 'block';
  const grid = document.getElementById('collection-grid');
  grid.innerHTML = '';
  for (const group of groupCollectionCards(cards)) {
    grid.appendChild(makeGroupedCardTile(group));
  }
}

function groupCollectionCards(cards) {
  const byKey = new Map();
  for (const c of cards) {
    const key = c.name.toLowerCase() + '|' + (c.owner_user_id || '');
    if (!byKey.has(key)) {
      byKey.set(key, { name: c.name, owner_icon: c.owner_icon, quantity: 0, printings: [] });
    }
    const group = byKey.get(key);
    group.quantity += c.quantity;
    group.printings.push(c);
  }
  return Array.from(byKey.values());
}

function makeGroupedCardTile(group) {
  const primary = group.printings[Math.floor(Math.random() * group.printings.length)];
  const entries = allocatedCards[group.name.toLowerCase()];
  let statusText;
  let title = null;
  if (entries) {
    const inDeckQty = Math.min(entries.reduce((s, e) => s + e.quantity, 0), group.quantity);
    statusText = `${inDeckQty}/${group.quantity} in deck`;
    title = 'In deck: ' + entries.map(e => `${e.deckName} (×${e.quantity})`).join(', ');
  } else {
    statusText = `${group.quantity} owned`;
  }

  const el = makeCard(group.name, primary.set_code, primary.collector_number, null, '', null, statusText);
  if (title) el.title = title;

  if (group.owner_icon) {
    const ownerBadge = document.createElement('div');
    ownerBadge.className = 'mtg-card-owner';
    ownerBadge.textContent = group.owner_icon;
    el.querySelector('.mtg-card-frame').appendChild(ownerBadge);
  }

  if (group.printings.length > 1) {
    el.classList.add('stacked');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'mtg-card-printings-btn';
    btn.textContent = `▼ ${group.printings.length} printings`;
    btn.onclick = (e) => { e.stopPropagation(); togglePrintings(group, el, btn); };
    el.querySelector('.mtg-card-frame').appendChild(btn);
  }

  return el;
}
```

Note on `owner_user_id`/`owner_icon`: these fields only exist on cards from `combinedData.cards` (the multi-user combined collection view via `currentBaseCards()`); plain `collectionCards` rows have neither, so `c.owner_user_id || ''` collapses to `''` and every card groups under one owner bucket as before. This keeps different people's copies of the same card name in separate tiles when viewing a combined collection, instead of silently merging them.

- [ ] **Step 2: Start the dev server and manually verify the Collection tab**

Run: `uvicorn webapp.main:app --reload`, log in, open the Collection tab.

Expected, comparing against the approved mockup (https://claude.ai/code/artifact/fca11852-d2b6-453f-9c7a-3a1acdbd016e):
- Each unique card name appears exactly once (no duplicate tiles for a card owned in two printings).
- A card not in any deck shows `"{quantity} owned"` in accent-red text in the plate.
- A card in a deck shows `"{inDeck}/{quantity} in deck"` instead.
- No corner quantity badge appears on any tile.
- A card owned in >1 printing shows a faint stacked edge behind the frame and a "N printings" button top-right (button doesn't do anything yet — Task 4 wires it up).
- Search (`#col-search`) and the color-group filter chips still work (they call `filterCollection()` → `renderCollection()` unchanged).

- [ ] **Step 3: Commit**

```bash
git add webapp/static/app.html
git commit -m "feat: group Collection tab tiles by card name"
```

---

### Task 4: Wire up the "N printings" inline expand/collapse

**Files:**
- Modify: `webapp/static/app.html` — add `togglePrintings()` immediately after `makeGroupedCardTile()` (added in Task 3).

**Interfaces:**
- Consumes: `group` object and grouped tile element from Task 3's `makeGroupedCardTile()`; `makeCard()` from Task 2; `.mtg-card-printings-row` CSS from Task 1.
- Produces: `togglePrintings(group, groupEl, btn)`, called from the `onclick` handler already wired in Task 3.

- [ ] **Step 1: Add `togglePrintings()`**

```javascript
function togglePrintings(group, groupEl, btn) {
  const existing = groupEl.nextElementSibling;
  if (existing && existing.classList.contains('mtg-card-printings-row')) {
    existing.remove();
    btn.textContent = `▼ ${group.printings.length} printings`;
    return;
  }

  const row = document.createElement('div');
  row.className = 'mtg-card-printings-row';
  for (const p of group.printings) {
    const status = `${p.set_code} #${p.collector_number}${p.foil ? ' · ✦ Foil' : ''} · ×${p.quantity}`;
    row.appendChild(makeCard(p.name, p.set_code, p.collector_number, null, '', null, status));
  }
  groupEl.after(row);
  btn.textContent = '▲ Hide';
}
```

- [ ] **Step 2: Start the dev server and manually verify the expansion**

Run: `uvicorn webapp.main:app --reload`, log in, open the Collection tab, find a card owned in multiple printings, click "N printings".

Expected:
- A new row appears directly below that card's tile, spanning the full grid width, containing one tile per printing.
- Each printing tile uses the same plate design (no corner badge) with status line reading `SET #CN`, or `SET #CN · ✦ Foil` if foil, followed by `· ×qty` — e.g. `MH2 #90 · ×2` or `SLD #14 · ✦ Foil · ×1`.
- Button label flips to "▲ Hide".
- Clicking the button again removes the row and flips the label back to "▼ N printings".
- Typing in the search box while a row is expanded clears it (full re-render via `filterCollection()` → `renderCollection()` rebuilds `#collection-grid` from scratch) — this is expected; expansion state is not meant to persist across re-renders.

- [ ] **Step 3: Commit**

```bash
git add webapp/static/app.html
git commit -m "feat: expand grouped Collection tiles into individual printings"
```

---

### Task 5: Full manual regression pass across all tabs

**Files:** none (verification only).

**Interfaces:** none — this task only exercises the app in a browser.

- [ ] **Step 1: Run the full test suite to confirm no backend regressions**

Run: `pytest`
Expected: all existing tests pass (this change touches no Python files, so this is a safety check, not expected to catch anything — but confirms nothing else was accidentally modified).

- [ ] **Step 2: Manually verify every tab that uses `makeCard()`**

Run: `uvicorn webapp.main:app --reload`, log in, and click through: Collection, Sale (for-sale + extras views), Meta (owned/missing grids), Decks, Wants.

Expected: every tile across every tab shows the new inset plate (name, solid background, accent top border) instead of the old thin overlay strip. Tabs other than Collection show no second status line (since their `makeCard()` calls don't pass `statusText`) and their existing corner badges (price, quantity, foil markers) still render normally.

- [ ] **Step 3: Compare the Collection tab side-by-side against the approved mockup**

Open https://claude.ai/code/artifact/fca11852-d2b6-453f-9c7a-3a1acdbd016e next to the running app's Collection tab. Confirm: single-printing owned/in-deck cards, multi-printing stacked-edge + button, and the expanded-printings status line format all match.

- [ ] **Step 4: Final commit (if any cleanup was needed) or confirm the branch is clean**

```bash
git status
```

Expected: clean working tree (all changes already committed in Tasks 1–4). If Step 2 or 3 surfaced a bug, fix it, then:

```bash
git add webapp/static/app.html webapp/static/assets/modernist.css
git commit -m "fix: <describe the regression fixed during manual verification>"
```
