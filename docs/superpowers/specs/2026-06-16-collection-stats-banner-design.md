# Collection Stats Banner & Lands Breakdown Design

**Date:** 2026-06-16  
**Status:** Implemented

## Overview

Two new UI features on the Collection tab of `web/static/index.html`:

1. **Stats banner** — a row of colour tiles always visible above the toolbar, showing total card counts per colour group.
2. **Lands breakdown sidebar** — a left-hand panel showing land cycle completion stats, appears automatically when the Lands chip is active, toggleable.

No backend changes. All data comes from the existing `collection.json` payload.

---

## Stats Banner

A row of 8 coloured tiles rendered above the search/filter toolbar. Computed once at page load from the full `collectionCards` array (totals never change with filters).

| Colour | Icon | Number colour | Notes |
|--------|------|--------------|-------|
| White | ☀️ | `#e8e8d0` | warm cream |
| Blue | 💧 | `#6ab0f5` | |
| Black | 💀 | `#aaaaaa` | |
| Red | 🔥 | `#e94560` | |
| Green | 🌿 | `#4caf50` | |
| Multi | 🌈 | `#f0c040` | gold |
| Lands | 🗺️ | `#c8894a` | earthy brown |
| Colourless | ⬡ | `#b0b0b0` | icon forced white (`#ffffff`) |

Each tile has a colour-tinted background and border matching its identity. Counts are `quantity` sums across all printings, matching the `color_group` field on each card.

---

## Lands Breakdown Sidebar

**Trigger:** selecting the Lands chip opens the sidebar automatically. Switching to any other chip hides it. A "📊 Breakdown / 📊 Hide" toggle button in the toolbar shows/hides it while remaining on Lands.

**Layout:** `#collection-body` is a flex row — sidebar (220px, left, sticky) + `#collection-grid-wrap` (flex: 1, right).

**Per-cycle row shows:**
- Cycle name
- `Xu · Yp` — X = unique cards owned (≥1 copy), Y = cards with ≥4 copies (full playset)
- Progress bar driven by playset count (`Y / cycle_size × 100%`)
- Bar/label colour: green if fully complete, gold if partial, red if zero playsets

**Hardcoded cycles (`LAND_CYCLES` constant):**

| Cycle | Size | Key cards |
|-------|------|-----------|
| Shocklands | 10 | Blood Crypt, Breeding Pool, … |
| Fetchlands | 13 | Arid Mesa, Polluted Delta, … + Fabled Passage, Evolving Wilds, Terramorphic Expanse |
| Fast lands | 10 | Blackcleave Cliffs, Spirebluff Canal, … |
| Pain lands | 10 | Adarkar Wastes, Shivan Reef, … |
| Verge lands | 9 | Bleachbone Verge, Floodfarm Verge, … |
| Horizon lands | 6 | Fiery Islet, Sunbaked Canyon, … |
| Filter lands | 10 | Mystic Gate, Sunken Ruins, … |
| Bounce lands | 10 | Gruul Turf, Golgari Rot Farm, … |
| Basics | 5 | Plains, Island, Swamp, Mountain, Forest |
| Other | dynamic | anything not in the above lists — shows unique count only, no bar |

---

## Implementation

All changes in `web/static/index.html`:

- **CSS:** `.stats-banner`, `.stat-tile`, `#collection-body`, `#collection-grid-wrap`, `.lands-sidebar`, `.cycle-row`, `.breakdown-btn`
- **HTML:** `#collection-stats` div above toolbar; `#breakdown-toggle` button in toolbar; `#collection-body` flex wrapper with `#lands-sidebar` and `#collection-grid-wrap`
- **JS constants:** `COLOUR_TILES`, `LAND_CYCLES`
- **JS functions:** `buildStatsBanner()`, `buildLandsSidebar()`, `toggleLandsSidebar()`
- **Modified:** `setGroup()` — manages sidebar visibility on Lands toggle; `init()` — calls `buildStatsBanner()` after data loads
