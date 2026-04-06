# MTG Collection Manager

A personal CLI tool for tracking a physical Magic: The Gathering collection. Syncs card inventory from [Moxfield](https://www.moxfield.com) packages, then cross-references against decklists from MTGTop8, Moxfield, or MTGGoldfish to show what you're missing or need to order.

## Features

- **Sync** your Moxfield collection packages into a local SQLite database
- **Check missing cards** for any deck URL — supports compare pages with multiple variants
- **Build decks** — allocate a deck's cards to a named physical box, with proxy support for unavailable cards
- **Track boxes** — see which decks are built and where they're stored
- **Find extras** — list cards you own more than 4 copies of (trade/sell stock)
- **Discord bot** — all commands available as Discord slash commands via a hosted bot

## Supported Deck Sources

| Site | URL format |
|---|---|
| [MTGTop8](https://www.mtgtop8.com) | Single deck or compare page |
| [Moxfield](https://www.moxfield.com) | Single deck |
| [MTGGoldfish](https://www.mtggoldfish.com) | Single deck |

## Installation

**Prerequisites:** Python 3.11+

**CLI only:**
```bash
pip install -e .
```

**With Discord bot:**
```bash
pip install -e . && pip install -r requirements-api.txt
```

## Configuration

Create `~/.mtg_manager/config.toml` based on `config.toml.example`:

```toml
[moxfield]
# One entry per Moxfield package — the public_id is the slug at the end of the deck URL.
# e.g. https://www.moxfield.com/decks/abc123XYZ  →  public_id = "abc123XYZ"
packages = [
    { color_group = "White",      public_id = "REPLACE_ME" },
    { color_group = "Blue",       public_id = "REPLACE_ME" },
    { color_group = "Black",      public_id = "REPLACE_ME" },
    { color_group = "Red",        public_id = "REPLACE_ME" },
    { color_group = "Green",      public_id = "REPLACE_ME" },
    { color_group = "Multicolor", public_id = "REPLACE_ME" },
    { color_group = "Lands",      public_id = "REPLACE_ME" },
    { color_group = "Colorless",  public_id = "REPLACE_ME" },
]
request_delay_seconds = 1.0

[database]
path = "~/.mtg_manager/collection.db"
```

**For the Discord bot**, create a `.env` file in the project root:
```
DISCORD_BOT_TOKEN=your_token_here
```

## CLI Usage

```bash
# Sync your Moxfield collection to the local database
mtg sync

# Sync a single colour group only
mtg sync --color-group Red

# Check what cards you're missing for a deck or compare URL
mtg missing <url>

# Only show cards missing from at least 3 of the compared variants
mtg missing <url> -m 3

# Include sideboard cards in the check
mtg missing <url> --sideboard

# Allocate a deck to a physical box (marks it as built)
mtg build <url> --box "White Box"

# List all built decks, grouped by box
mtg boxes

# Free a deck's cards back to the available pool
mtg unbox <deck_id>

# List cards you own more than 4 copies of
mtg extras

# List extras with a custom threshold
mtg extras --limit 2
```

## Discord Bot

Start the bot:
```bash
python -m api.bot
```

All CLI commands are available as Discord slash commands (`/sync`, `/missing`, `/build`, `/boxes`, `/unbox`, `/extras`).

## How It Works

Every read command silently re-syncs your Moxfield collection first, so the database is always current before any comparison is made.

Card availability is tracked separately from ownership. `missing` distinguishes between two situations: cards you don't own enough of (listed under "need to order") and cards you own but are currently locked in a built deck (listed separately as "in boxes"). So if you own 4x Lightning Bolt but all 4 are allocated to a built deck, `missing` will not tell you to order more — it will tell you they're already in use. When `build` is called and a card is genuinely unavailable, it is marked as a proxy (listed in the pick list output but not deducted from your available count).

Double-faced cards are handled automatically: MTGTop8 uses only the front face name (`Ral, Monsoon Mage`) while Moxfield stores the full name (`Ral, Monsoon Mage // Ral, Leyline Prodigy`). Both forms resolve to the same owned quantity.

## Running Tests

```bash
pytest

# Single test
pytest tests/test_foo.py::test_bar
```

## Project Structure

```
mtg_manager/     ← core library
  config.py      ← loads ~/.mtg_manager/config.toml
  models.py      ← dataclasses: OwnedCard, DeckCard, Decklist, MissingCard, BoxedCard
  db.py          ← SQLite via get_conn(); schema auto-migrated on connect
  sources.py     ← URL router dispatching to the correct scraper
  moxfield.py    ← Moxfield API client (collection sync + deck fetch)
  mtgtop8.py     ← MTGTop8 scraper
  goldfish.py    ← MTGGoldfish scraper
  cli.py         ← Click CLI entry point (mtg command)

api/
  handlers.py    ← business logic as plain strings (shared by all interfaces)
  bot.py         ← Discord slash-command bot
```
