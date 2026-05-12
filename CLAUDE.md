# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

**Install (CLI only):**
```bash
pip install -e .
```

**Install (with Discord bot):**
```bash
pip install -e . && pip install -r requirements-api.txt
```

**Config file** — required at `~/.mtg_manager/config.toml` (see `config.toml.example`). The DB path and Moxfield package public IDs are set here.

**Environment variables** (for the Discord bot) — stored in `.env`:
- `DISCORD_BOT_TOKEN` — from Discord Developer Portal
- `OWNER_DISCORD_ID` — your Discord user ID; routes you to `~/.mtg_manager/config.toml` (same DB as CLI) and keeps auto-sync. Optional — if unset no owner special-casing applies.

## Commands

**CLI:**
```bash
mtg sync                          # fetch Moxfield packages → SQLite
mtg missing <url>                 # compare decklist against collection
mtg build <url> --box "Box Name"  # allocate deck to a box
mtg boxes                         # list built decks
mtg unbox <deck_id>               # free a deck's cards
mtg extras                        # list cards owned >4 copies
```

**Discord bot:**
```bash
python -m api.bot
```

**Deployment:**

The Discord bot runs on an Oracle VM and deploys automatically via GitHub Actions on every push to `main` (`.github/workflows/deploy.yml`). The workflow SSHs into the VM, runs `git pull`, and restarts the `mtg-bot` systemd service. There is no manual deploy step — merging to main is sufficient.

**Tests:**
```bash
pytest
pytest tests/test_foo.py::test_bar   # single test
```

## Architecture

The project has two interfaces over shared business logic:

```
mtg_manager/     ← core library
  config.py      ← loads ~/.mtg_manager/config.toml (TOML, stdlib tomllib)
  models.py      ← dataclasses: OwnedCard, DeckCard, Decklist, MissingCard, BoxedCard
  db.py          ← SQLite via contextmanager get_conn(); schema auto-migrated on connect
  sources.py     ← URL router: dispatches to mtgtop8, moxfield, or goldfish scrapers
  moxfield.py    ← cloudscraper client for Moxfield API; global threading.Lock serialises
                   all outbound requests at ~1 req/s regardless of concurrent users
  mtgtop8.py     ← BeautifulSoup scraper for MTGTop8 decklists
  goldfish.py    ← BeautifulSoup scraper for MTGGoldfish decklists
  cli.py         ← Click CLI entry point (mtg command) — fully independent of api/

api/
  users.py       ← multi-user registry (~/mtg_data/registry.sqlite); per-user Config routing
  handlers.py    ← business logic returning plain strings; accepts (cfg, is_owner) params
  bot.py         ← Discord slash-command bot (discord.py); resolves user → cfg → handler

scripts/
  evict_cache.py ← nightly cron: deletes owned_cards/for_sale_cards for users inactive >7 days

tests/
  test_users.py  ← registry CRUD unit tests
```

**Multi-user data layout:**
```
~/.mtg_manager/config.toml     ← bot owner's CLI config (unchanged)
~/.mtg_manager/collection.db   ← bot owner's collection DB (shared with CLI)

~/mtg_data/registry.sqlite     ← Discord ID → packages + prefs for all friend users
~/mtg_data/users/<id>.sqlite   ← per-friend collection DB (same schema as collection.db)
```

**Key design decisions:**

- **CLI (`cli.py`) is fully independent** — it has its own `_auto_sync` and `_load_cfg` and never imports from `api/`. Handler signature changes don't affect it.
- **`api/handlers.py`** receives a pre-resolved `cfg: Config` and `is_owner: bool` from the bot. It never loads config itself. `_auto_sync()` only runs when `is_owner=True` — friends use opt-in `/sync`.
- **Owner shortcut** — `OWNER_DISCORD_ID` in `.env` routes the bot operator to their existing `~/.mtg_manager/config.toml`, so their CLI and Discord bot share one DB with no divergence. Owner is exempt from sync throttling and eviction.
- **Sync throttle** — non-owner `/sync` is rate-limited to once per 60 minutes (checked in `bot.py` via `users.minutes_since_last_sync`).
- **Global Moxfield lock** — `_MOXFIELD_LOCK` in `moxfield.py` ensures all HTTP requests to Moxfield serialise, preventing concurrent Discord users from bursting the API.
- **7-day cache eviction** — `scripts/evict_cache.py` (run nightly) deletes `owned_cards` and `for_sale_cards` for inactive users. `built_decks`, `allocated_cards`, `card_tags`, and `card_legalities` are preserved. Next `/sync` rehydrates the cache.
- **Ephemeral defaults** — personal/admin commands (`/sync`, `/extras`, `/stats`, `/search`, `/illegal`, `/tag*`, `/setup*`) are ephemeral by default. Share-friendly commands (`/missing`, `/build`, `/boxes`, `/forsale`, `/proxy`, `/scryfall`, `/card`) are public by default with an optional `private: bool` flag.
- `db.get_conn()` takes a `db_path` argument — routing to a per-user DB requires no schema changes.
- Double-faced card names: MTGTop8 uses only the front face; Moxfield stores full names. `get_owned_quantity()` handles this with a `SUBSTR`/`INSTR` SQL pattern.
- Proxy support: when `build` is called and a card is unavailable, it's marked `is_proxy=1` in `allocated_cards`. Proxies don't reduce `get_available_quantity()` but are listed in the pick list output.

**Database schema** (`db.py` — identical for owner DB and per-user DBs):
- `owned_cards` — keyed on `(name, set_code, collector_number, foil)`; synced from Moxfield
- `built_decks` — one row per built deck with `box_name`
- `allocated_cards` — join table linking cards to decks, with `is_proxy` flag
- `for_sale_cards`, `card_tags`, `card_legalities` — for sale list, tags, format legality

**Registry schema** (`api/users.py`):
- `users` — `discord_id`, sort pref, tracked formats, `last_seen_at`, `last_synced_at`
- `user_packages` — `(discord_id, color_group)` → Moxfield `public_id`

**Supported deck URL sources** (`sources.py`): `mtgtop8.com`, `moxfield.com`, `mtggoldfish.com`

## Eviction cron (VM setup)

Add a daily systemd timer on the Oracle VM after deploy:
```bash
# /etc/systemd/system/mtg-evict.service
[Unit]
Description=MTG Manager cache eviction

[Service]
Type=oneshot
WorkingDirectory=/home/ubuntu/mtg-manager
ExecStart=/home/ubuntu/mtg-manager/venv/bin/python -m scripts.evict_cache
User=ubuntu

# /etc/systemd/system/mtg-evict.timer
[Unit]
Description=Run MTG eviction daily

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```
```bash
sudo systemctl enable --now mtg-evict.timer
```
