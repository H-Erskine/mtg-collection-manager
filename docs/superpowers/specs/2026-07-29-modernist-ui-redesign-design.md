# Modernist UI Redesign — Design

## Overview

Replace the web app's current dark, rounded, colorful UI with the **Modernist** design system: flat, architectural, Archivo type, single red accent (`#ec3013`), zero corner radius, 2px rules, light mono-ink surfaces. Source: an external design handoff (`design_handoff_ui_redesign/README.md`, high-fidelity HTML reference + screenshot, provided by the user) covering Collection, Decks, Missing, Meta, For Sale, Config, and Admin.

This is a from-scratch visual re-implementation into the existing architecture — plain HTML/CSS/JS served from FastAPI (`webapp/static/*.html`), inline `<style>` per page, vanilla JS DOM rendering, no build step, no framework. The reference HTML is not copied in as-is; it exists only to pin exact colors/type/spacing/component shapes.

## Scope decisions (resolved during brainstorming)

- **`admin-user.html`** — not covered by the handoff doc. Restyle it to match Modernist by extrapolating the system's tokens/components (tables, section headings, dividers) already specified for `admin.html`, so no page is left on the old theme.
- **`onboarding.html`** — also uncovered by the handoff doc. Same treatment: restyle to match, using the closest matching component patterns (form inputs, primary button, flush section layout) from Config.
- **CardMarket profile link** — genuinely new functionality (not just styling), bundled into this same effort per the handoff doc: a new `cardmarket_url` config field, a save endpoint, and a conditional link on For Sale. Implemented in this plan rather than split out.
- **Rollout** — one shared design-system pass first (tokens + shared components: nav, card grid, tables, buttons, form controls), then all 7+ screens converted together in one effort. Avoids a period where the app is half old-theme/half new-theme.
- **Verification** — run the app locally (via the project's `run` skill) and visually walk every screen against the reference screenshot/HTML before calling this done.

## Design tokens

Copied from the handoff's `_ds/.../styles.css` `:root` block — this becomes the new `:root` in the app's stylesheet, replacing the current dark theme's tokens:

```css
--color-bg: #f3f2f2;
--color-surface: #eae9e9;
--color-text: #201e1d;
--color-accent: #ec3013;
--color-accent-2: #e15b47;
--color-divider: color-mix(in srgb, #201e1d 40%, transparent);

--color-neutral-100..900: #f8f4f4 … #2d2b2b;
--color-accent-100..900:  #fff2ef … #4d170e; /* 700 = #ae1800, for accent text on light bg */

--font-heading: "Archivo", system-ui, sans-serif;   /* weight 800 for headings */
--font-body: "Archivo", system-ui, sans-serif;       /* 400/600 */

--space-1..8: 4 / 8 / 12 / 16 / 24 / 32px;
--radius-*: 0px everywhere;
--shadow-sm/md/lg: ink-tinted, used sparingly (mostly flat, no elevation);
```

Archivo loaded via Google Fonts (400/600/800). No icon library; emoji removed entirely (the current nav brand uses "⚔ MTG Collection" — replaced with plain text).

## Where this lives in the codebase

All work is inside `webapp/static/*.html` (5 existing pages + no new files):
`app.html` (Collection/Decks/Missing/Meta/For Sale tabs), `config.html`, `admin.html`, `admin-user.html`, `onboarding.html`.

Each page keeps its current inline `<style>` block, vanilla JS rendering pattern, and existing global JS state (`collectionCards`, `allocatedCards`, `viewScope`, `saleView`, `metaData`, etc. in `app.html`; equivalent per-page state elsewhere). No new state paradigm, no build step, no JS framework introduced.

Backend: `webapp/config.py` already has the pattern to mirror for the new field — `/api/config/formats` (GET returns it in `/api/config`, POST `/api/config/formats` saves it). `cardmarket_url` follows the same shape.

## Components (shared across pages)

Rebuilt once, reused everywhere, per the handoff spec:

- **Nav** — single persistent 56px bar, all tabs always visible regardless of auth state (fixes today's per-page tab-visibility logic), brand flush left, tabs 28px gaps (active = ink + 2px accent bottom border), identity/logout flush right. Second row (2px top divider) holds the page's search input full-width.
- **Card grid / thumb** — `repeat(auto-fill, minmax(76px,1fr))`, 6px gap, 5:7 placeholder, 1px divider border, bottom name strip (10% ink tint), quantity badge (ink chip, white text; accent chip if foil). Reused by Collection, Decks (expanded), Missing, Meta, For Sale.
- **In-deck proportion bar (new visual, existing data)** — 3px bar above the name strip: grey track (`--color-neutral-300`), red fill (`--color-accent`) sized to `copies_in_deck / copies_owned`, plus a 9px accent-700 `"{in_deck}/{owned} in deck"` label. Replaces the old single-color glow ring so partial vs. full allocation are visually distinct. Computed from data already available client-side (`allocatedCards` vs. the collection map) — rendering only, no new data plumbing.
- **Flush link-rail** (130px) — used for the people/group scope selector on Collection and For Sale.
- **`.seg` segmented control** — replaces `<select>` for For Sale/Wants toggle, Pick List Sort, and Admin's Activity/Actions toggle.
- **`.table`** — flush-row table style for Admin's Users, Failed Logins, Activity.
- Buttons, inputs, section headings (11px uppercase accent h6 + 1px divider), focus-visible (2px accent outline, 2px offset, never a default blue ring), hover states (7% ink tint or accent-600 bg) — one definition, shared.

## Per-screen notes (deltas from the handoff doc worth calling out)

- **Meta art bar** needs new selection logic (not just rendering): pick the highest-CMC non-land creature/planeswalker in the decklist, then fetch/display its Scryfall image cropped into a `flex:1` bar next to the fixed-width name/stats block. This is new client (or server) logic, unlike the proportion bar which is pure rendering.
- **Config → Moxfield Packages** row changes from showing the raw `public_id` as inline code to showing the package display name + a "View on Moxfield ↗" link — no backend change, template-only.
- **Config → CardMarket** (new section): one field + Save, per the `/api/config/formats` pattern. For Sale reads this value to conditionally render "CardMarket profile ↗"; omitted entirely (no placeholder) when unset.
- **`admin-user.html`** and **`onboarding.html`**: no dedicated mockup exists. Apply the same section/table/form component patterns used on `admin.html`/`config.html` respectively, keeping current page logic and data untouched — styling-only changes for these two pages.

## Error handling / edge cases

- CardMarket URL: no format validation beyond what's already standard for URL-ish config fields elsewhere in `config.py` (mirror existing package/URL field handling, don't add new validation logic that doesn't exist for sibling fields).
- Meta art-bar card selection: if no non-land creature/planeswalker exists in the decklist (e.g., a land-only or all-instant/sorcery deck), fall back to omitting the art bar image (blank neutral-100 fill, no broken-image icon).
- In-deck proportion bar: only rendered when a card has `copies_in_deck > 0`; otherwise the old (now removed) glow-ring markup simply isn't emitted — no zero-width bar artifacts.

## Testing / verification plan

No new automated test coverage is warranted for pure CSS/markup changes (styling isn't unit-testable in this codebase's existing test suite). Verification is manual:

1. Start the app via the project's `run` skill.
2. Walk each of the 7 screens (Collection, Decks, Missing, Meta, For Sale, Config, Admin) plus `admin-user.html` and `onboarding.html`, comparing against `screenshots/1b-full-direction.png` and the reference HTML.
3. Specifically check: nav tabs always visible logged-out vs. logged-in, in-deck bar renders correctly for partial (e.g. 1/2) vs full (4/4) allocation, Meta art bar picks a sensible card and handles the no-candidate fallback, CardMarket link appears/disappears correctly based on config state, focus-visible outlines use the accent ring (not browser default).
4. Existing `pytest` suite must still pass (no backend logic beyond the new CardMarket endpoint is touched, but run it to confirm nothing broke).

## Out of scope

- Any change to `mtg_manager/` core library, CLI, or Discord bot — this is `webapp/` only.
- New icons/illustrations, animations/transitions beyond instant state swaps.
- Dark-mode/theme toggle — Modernist replaces the old theme outright; no user-facing theme switch is introduced.
