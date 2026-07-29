# Collection tile redesign — design spec

Date: 2026-07-29
Branch: `worktree-modernist-ui-redesign`
Files: `webapp/static/app.html`, `webapp/static/assets/modernist.css`

## Problem

The Collection tab's current card tile (as of commit `43c219d`) renders one tile per printing/foil row, with a corner quantity badge and the card name overlaid as a thin semi-opaque strip at the bottom of the art. This doesn't match the intended design (reference: `Example ux.png`, supplied by the user) and doesn't group multiple printings of the same card into a single slot.

A visual mockup was iterated live in an Artifact (https://claude.ai/code/artifact/fca11852-d2b6-453f-9c7a-3a1acdbd016e) and approved. This spec captures the final, approved design.

## Grouping

The Collection tab groups owned cards by name (case-insensitive), not by printing. Today's `renderCollection(cards)` receives a flat, already-filtered list of printing/foil rows (search + color-group filters operate on this flat list, unchanged). Before rendering tiles, add a grouping pass: bucket the flat list by lowercased name, summing `quantity` across all printing/foil rows for that name, and retain the individual rows on the group for the printings expansion. Stats text (`"X cards · Y printings"`) continues to be computed from the flat filtered list, not the grouped one — grouping only affects tile rendering.

## Grouped tile (default, collapsed state)

Single bordered frame (`aspect-ratio: 5/7`, existing `.mtg-card-frame` styling) containing the card art, with the name/status plate as an **inset overlay pinned to the bottom of the frame** — not a separate element in normal flow below it. This reverts yesterday's overlay-on-art tweak's *text handling* but keeps a bordered, boxed feel: the plate is opaque, sized larger than before, sits inside the same bordered box as the art.

- **No corner quantity badge** on grouped tiles.
- **Plate**: background `var(--color-neutral-100)`, `border-top: 3px solid var(--color-accent)`, containing two lines:
  - Card name (bold, `.mtg-card-plate-name`)
  - Status line (`.mtg-card-plate-status`, color `var(--color-accent-700)`):
    - `"{qty} owned"` if the card has no allocated-deck entries
    - `"{inDeckQty}/{qty} in deck"` if it does (replaces the old separate proportion-bar + label entirely — that markup/CSS is removed for grouped tiles)
- **Art**: one printing is chosen at random from the group's rows, re-picked on every render call (same pattern as the existing extras-tab `Math.floor(Math.random() * printings.length)`).
- **Multi-printing groups** (a name with >1 printing/foil row): a faint stacked-card edge behind the frame (offset `::before`/`::after`, per the mockup) plus a **"N printings"** button in the top-right corner — the same slot the old quantity badge occupied. Single-printing groups show neither the stacked edge nor this button.

## Printings expansion (click "N printings")

Clicking the button toggles an inline expansion: insert each of the group's individual printing/foil rows as their own tile, in the grid, directly after the grouped tile (spanning the full grid width as its own row — `grid-column: 1 / -1`). Click again (button label flips to "▲ Hide") to collapse/remove them.

Each expanded tile uses the **same new tile design** as the grouped tile (bordered frame + inset plate, no corner badge) — not the old per-printing style. Its plate status line differs from the grouped tile's: instead of deck info, it shows

```
{set_code} #{collector_number}{ · ✦ Foil if foil} · ×{quantity}
```

in that field order (set/number, then foil, then copy count). The name line still repeats the card name.

## Scope and shared CSS

`.mtg-card-frame`, `.mtg-card-plate`, `.mtg-card-badge`, etc. are shared CSS classes used by every tab that renders card tiles via `makeCard()` (Sale, Meta, Decks, Wants, Collection). The plate-overlay restyle is a change to this shared component, so it will visually affect those other tabs too — consistent with how each tab has been restyled to Modernist one at a time in prior commits. This is intentional, not scope creep.

Grouping, the stacked-edge visual, the "N printings" button, and the inline expansion logic are **Collection-tab-only** — implemented in `renderCollection()` and helpers local to it. `makeCard()`'s signature and behavior for other tabs (Sale, Meta, Decks, Wants) are unchanged.

## Out of scope

- No changes to the extras-tab printings picker (`▼ N versions` dropdown pattern) — that's a different, pre-existing UX and isn't touched.
- No changes to search/color-group filtering logic itself, only where grouping is inserted relative to it.
- No dark-mode/theming work — the app has a single committed light theme.
