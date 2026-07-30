# Moxfield package sections — design

Date: 2026-07-30
Scope: web multi-user registry (`api/users.py`, `webapp/config.py`, `webapp/static/config.html`) and the
web sync path (`api/handlers.py`). The CLI (`mtg_manager/cli.py`, `mtg_manager/config.py`'s `load_config`)
is being decommissioned and is explicitly **not** touched — it keeps its existing `$`/`"Wants"`
name-sniffing behavior.

## Problem

Moxfield packages are configured as a single flat list per user, with the package's *purpose*
(collection / for-sale / wants) inferred at sync time from the Moxfield deck's own title:
a `$`-prefixed name means "for sale", a name of exactly `"Wants"` means "wants list", anything
else is a plain collection package tagged with a `color_group`.

This has two problems:
1. It's implicit and fragile — the behavior is controlled by what you name the deck on Moxfield,
   not by anything in the app's own config.
2. It breaks with more than one package per purpose: `clear_all_for_sale_cards()` and
   `clear_wants_cards()` are called once **per package** during sync, so a second sale/wants
   package wipes out the first one's results from the same sync run.

There's also no way to configure a "deck" package — one whose cards should be auto-built into a
box (allocated, proxied if short) the way `mtg build`/`/build` do today, rather than added to the
collection.

## Data model

`user_packages` (SQLite, in the registry DB) is restructured with an explicit `section` column,
and its primary key changes from `(user_id, color_group)` to an auto-increment `id`:

```sql
CREATE TABLE user_packages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    section     TEXT NOT NULL CHECK (section IN ('collection','sale','wants','decks')),
    label       TEXT NOT NULL,       -- was color_group; a display label, no longer required to be unique
    public_id   TEXT NOT NULL,
    price       REAL,                -- sale-only; NULL for all other sections
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Moving to an `id` primary key (instead of keying on the label) is what makes "any number of
packages per section" safe: two packages with the same label no longer collide, and add/remove
target a specific row instead of a name.

`mtg_manager/config.py`'s `Config` dataclass gains three new fields alongside the existing
`packages: list[MoxfieldPackage]` (which now specifically means "collection" packages):

```python
@dataclass
class SalePackage:
    label: str
    public_id: str
    price: float

@dataclass
class Config:
    packages: list[MoxfieldPackage]        # collection
    sale_packages: list[SalePackage]        # NEW
    wants_packages: list[MoxfieldPackage]   # NEW
    deck_packages: list[MoxfieldPackage]    # NEW
    ...
```

The CLI's `load_config()` leaves the three new fields as empty lists (nothing in `config.toml`
populates them); `cli.py` is unmodified and keeps working exactly as it does today.

## Sync semantics

Each destination table is cleared **once per sync run**, not once per package, then every
package belonging to that section is upserted into it:

```
def sync(cfg, conn):
    clear_all_for_sale_cards(conn)
    clear_wants_cards(conn)

    for pkg in cfg.packages:               # collection
        clear_color_group(conn, pkg.label)
        cards = fetch(pkg)
        upsert_cards(conn, cards)

    for pkg in cfg.sale_packages:           # sale -> collection AND for-sale
        clear_color_group(conn, pkg.label)
        cards = fetch(pkg)
        upsert_cards(conn, cards)                     # sale cards now also count as owned
        upsert_for_sale_cards(conn, cards, pkg.price)  # price is an explicit config field, not parsed from a deck name

    for pkg in cfg.wants_packages:          # wants -> wants tab only
        cards = fetch(pkg)
        upsert_wants_cards(conn, cards)

    for pkg in cfg.deck_packages:           # decks -> Decks tab + build, nothing else
        build_deck_from_package(pkg, cfg, conn)
```

### Decks

`build_deck_from_package` reuses the existing `handle_build` allocation logic (checks owned
quantity via `get_available_quantity`, proxies any shortfall, records `allocated_cards`), driven
automatically per configured deck package instead of by a manually-submitted URL:

- The deck URL is derived as `https://www.moxfield.com/decks/{public_id}` and fetched via the
  existing `fetch_decklists` → `fetch_moxfield_deck` path.
- `box_name` (still a required column on `built_decks`) is set to a freshly generated UUID per
  deck — box-as-a-grouping-concept is being dropped from the deck package flow; the column exists
  only to satisfy the existing non-null constraint.
- Idempotent by `deck_id` (Moxfield's public_id): if a deck with that id is already built, sync
  skips it silently — identical to today's `handle_build` guard. There is no auto-rebuild if the
  Moxfield deck's contents change later; the user must `unbox` first, same as the manual flow.
- Removing a deck package from config does not auto-unbox it — it only stops future re-processing
  of that entry.

### Net effect

- **Sale + Collection** both land in `owned_cards`; sale additionally lands in `for_sale_cards`
  with its own configured price.
- **Wants** lands only in `wants_cards`, and is visible to other users through the existing
  combined "all sale" view (`get_all_sale`) — unchanged.
- **Decks** land only in `built_decks`/`allocated_cards` (Decks/Boxes tab) — no writes to
  `owned_cards`, `wants_cards`, or `for_sale_cards`.
- Any number of packages per section works because each destination table is cleared once per
  sync run, not once per package.

## API

`api/users.py`:
- `add_package(user_id, section, label, public_id, price=None) -> id`
- `remove_package(user_id, package_id) -> bool` (by id, not label)
- `list_packages(user_id, section=None) -> list[{id, label, public_id, price}]`, optionally
  filtered to one section
- `get_user_config()` builds `Config`'s four package lists from the one table, split by `section`

`webapp/config.py`:
- `GET /api/config` returns packages grouped by section:
  `{"collection": [...], "sale": [...], "wants": [...], "decks": [...]}`
- `POST /api/config/packages` request body gains `section` (required) and `price` (required iff
  `section == "sale"`)
- `DELETE /api/config/packages/{id}` replaces the label-keyed route

## UI

`webapp/static/config.html` shows four labeled lists — Collection / Sale / Wants / Decks — each
with its own add-row:
- Collection/Wants rows: label + Moxfield URL/public_id.
- Sale rows: label + Moxfield URL/public_id + price.
- Decks rows: Moxfield URL/public_id only; the label shown is the deck's own name (populated
  after the first successful sync) rather than a user-chosen label.

## Migration

On first load after deploy, a one-time `_migrate_package_sections` (following the existing
`_migrate_*` pattern in `api/users.py`, guarded by checking for the `section` column before
running) rebuilds `user_packages` with the new schema:

- For each existing row, fetch the Moxfield package once to read its name.
- Name starts with `$` → `section='sale'`, parse the price from the `$<number>` prefix.
- Name is exactly `"Wants"` → `section='wants'`.
- Anything else → `section='collection'`.
- `decks` starts empty for every user — it's new functionality with no prior data to infer.

This preserves current behavior for existing users without requiring them to manually refile
already-working packages.

## Out of scope

- The CLI (`cli.py`, `config.toml`, `load_config`) — left exactly as-is per the decommissioning
  plan.
- Discord bot Moxfield-tag-on-build logic (`api/bot.py` / `handle_build`'s binder-tagging path,
  which looks up a package by `color_group`) — that's a CLI/bot-specific feature, not touched.
- Auto-rebuilding a deck package when the underlying Moxfield decklist changes.
- Auto-unboxing when a deck package is removed from config.
