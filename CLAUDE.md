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
  moxfield.py    ← cloudscraper client for Moxfield API (collection sync + deck fetch)
  mtgtop8.py     ← BeautifulSoup scraper for MTGTop8 decklists
  goldfish.py    ← BeautifulSoup scraper for MTGGoldfish decklists
  cli.py         ← Click CLI entry point (mtg command)

api/
  handlers.py    ← business logic returning plain strings (shared by both interfaces)
  bot.py         ← Discord slash-command bot (discord.py); calls handlers, sends embeds
```

**Key design decisions:**

- `api/handlers.py` is the single source of business logic — both the Discord bot and the CLI call these functions, which return plain strings. The CLI in `cli.py` reimplements the same logic with Rich formatting.
- `db.get_conn()` is a context manager that auto-creates the schema and runs migrations on every connection. Safe to call repeatedly.
- Double-faced card names: MTGTop8 uses only the front face (`Ral, Monsoon Mage`); Moxfield stores full names (`Ral, Monsoon Mage // Ral, Leyline Prodigy`). `get_owned_quantity()` handles this with a `SUBSTR`/`INSTR` SQL pattern.
- Proxy support: when `build` is called and a card is unavailable, it's marked `is_proxy=1` in `allocated_cards`. Proxies don't reduce `get_available_quantity()` but are listed in the pick list output.
- `_auto_sync()` in both `cli.py` and `handlers.py` silently re-fetches all Moxfield packages before any read operation to keep the collection current.

**Database schema** (3 tables in `db.py`):
- `owned_cards` — keyed on `(name, set_code, collector_number, foil)`; synced from Moxfield
- `built_decks` — one row per built deck with `box_name`
- `allocated_cards` — join table linking cards to decks, with `is_proxy` flag

**Supported deck URL sources** (`sources.py`): `mtgtop8.com`, `moxfield.com`, `mtggoldfish.com`
