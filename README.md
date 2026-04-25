# MTG Collection Manager

A personal CLI tool for tracking a physical Magic: The Gathering collection. Syncs card inventory from [Moxfield](https://www.moxfield.com) packages, then cross-references against decklists from MTGTop8, Moxfield, or MTGGoldfish to show what you're missing or need to order.

## Features

- **Sync** your Moxfield collection packages into a local SQLite database
- **Check missing cards** for any deck URL — supports compare pages with multiple variants
- **Build decks** — allocate a deck's cards to a named physical box, with proxy support for unavailable cards
- **Track boxes** — see which decks are built and where they're stored
- **Find extras** — list cards you own more than 4 copies of (trade/sell stock)
- **For-sale list** — track cards listed for sale with live CardMarket prices
- **Format legality** — flag owned cards illegal in your tracked formats
- **Scryfall search** — cross-reference any Scryfall search against your collection to find owned printings
- **Tagging** — attach custom labels (signed, foil, LP, etc.) to specific cards
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

[mtgtop8]
request_delay_seconds = 1.5
cache_ttl_hours = 24

[database]
# Path to your local SQLite database. ~ is expanded automatically.
path = "~/.mtg_manager/collection.db"

[output]
# How to sort cards in the pick list after building a deck.
# Options: colour, set, alphabetical, cmc
pick_list_sort = "colour"

[formats]
# Formats to track legality for. Names match Scryfall legality keys:
# standard, pioneer, modern, legacy, vintage, commander, pauper
tracked = ["modern", "standard"]
```

### Setting up Moxfield packages

This tool organises your physical collection using [Moxfield](https://www.moxfield.com) **packages** — Moxfield decks used as binders, one per colour group. Each package represents a section of your collection (e.g. all your White cards, all your Lands).

**Steps:**

1. Log in to [moxfield.com](https://www.moxfield.com) and create one deck per colour group you want to track. You can name them anything — "White Binder", "Lands", etc.
2. Add your physical cards to each deck on Moxfield. Set the quantity to match how many copies you own.
3. Make each deck **public** (Moxfield deck settings → Visibility → Public). The sync uses the public API, so private decks won't work.
4. Copy the `public_id` from each deck's URL:
   ```
   https://www.moxfield.com/decks/abc123XYZ
                                   ^^^^^^^^^^  ← this is the public_id
   ```
5. Paste each `public_id` into `config.toml` under `[moxfield] packages`, setting a matching `color_group` label.

You can use as many or as few packages as you like — the `color_group` label is just a display name. `mtg sync` will pull all of them.

### Setting up the Discord bot

**For the Discord bot**, you need a bot token from the [Discord Developer Portal](https://discord.com/developers/applications):

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**. Give it a name (e.g. "MTG Manager").
2. In the left sidebar, go to **Bot** → click **Add Bot** → confirm.
3. Under the Bot page, click **Reset Token** to reveal your bot token. Copy it — you won't see it again.
4. Scroll down and enable **Message Content Intent** and **Server Members Intent** under Privileged Gateway Intents (required by discord.py).
5. Go to **OAuth2 → URL Generator**. Under Scopes select `bot` and `applications.commands`. Under Bot Permissions select `Send Messages`, `Embed Links`, and `Use Slash Commands`.
6. Copy the generated URL, paste it in your browser, and invite the bot to your server.

Then create a `.env` file in the project root:
```
DISCORD_BOT_TOKEN=your_token_here
```

Run the bot locally with:
```bash
python -m api.bot
```

Discord slash commands are registered globally on startup — they may take up to an hour to appear in all servers after the first run.

## CLI Usage

```bash
# Sync your Moxfield collection to the local database
mtg sync

# Sync a single colour group only
mtg sync --color-group Red

# Re-fetch legality for all cards (use after bans/rotation)
mtg sync --refresh-legality

# Check what cards you're missing for a deck or compare URL
mtg missing <url>

# Only show cards missing from at least 3 of the compared variants
mtg missing <url> -m 3

# Include sideboard cards in the check
mtg missing <url> --sideboard

# Compare multiple deck URLs side-by-side
mtg missing <url1> <url2> <url3>

# Allocate a deck to a physical box (marks it as built)
mtg build <url> --box "White Box"

# List all built decks, grouped by box
mtg boxes

# Free a deck's cards back to the available pool
mtg unbox <deck_id>

# List cards you own more than 4 copies of
mtg extras

# List extras legal in a specific format
mtg extras --format modern

# List all cards marked for sale (with CardMarket prices)
mtg forsale

# Only show for-sale cards priced at or above €2
mtg forsale --min-price 2.0

# List owned cards illegal in your configured formats
mtg illegal

# Cross-reference a Scryfall search against your collection
mtg scryfall "<url>"

# Tag a specific card printing
mtg tag "Lightning Bolt" signed --set M11
mtg tag "Snapcaster Mage" "surge foil" --foil

# Remove a tag
mtg untag "Lightning Bolt" signed --set M11

# List all cards with a given tag
mtg tagged signed

# Remove a tag from every card that has it
mtg cleartag signed

# Show the current installed version
mtg version
```

## Discord Bot

The bot deploys automatically to an Oracle Cloud VM on every push to `main` via GitHub Actions. All CLI commands are available as Discord slash commands:

| Command | Description |
|---|---|
| `/sync` | Fetch Moxfield packages and update the collection |
| `/missing` | Show cards needed for a deck URL |
| `/build` | Mark a deck as built and allocate cards to a box |
| `/boxes` | List all built decks grouped by box |
| `/unbox` | Return a deck's cards to the available pool |
| `/extras` | List cards owned more than N copies of |
| `/forsale` | List for-sale cards with CardMarket prices |
| `/illegal` | List owned cards illegal in your tracked formats |
| `/search` | Search your collection by card name |
| `/scryfall` | Cross-reference a Scryfall search URL against your collection |
| `/stats` | Show collection stats and breakdown by colour group |
| `/tag` | Add a tag to a card |
| `/untag` | Remove a tag from a card |
| `/tagged` | List all cards with a given tag |
| `/cleartag` | Remove a tag from every card that has it |
| `/card` | Look up a card on Scryfall (type, text, price) |
| `/version` | Show the git commit the bot is running |
| `/help` | Show all available commands |

## How It Works

Every read command silently re-syncs your Moxfield collection first, so the database is always current before any comparison is made.

Card availability is tracked separately from ownership. `missing` distinguishes between two situations: cards you don't own enough of (listed under "need to order") and cards you own but are currently locked in a built deck (listed separately as "in boxes"). So if you own 4x Lightning Bolt but all 4 are allocated to a built deck, `missing` will not tell you to order more — it will tell you they're already in use. When `build` is called and a card is genuinely unavailable, it is marked as a proxy (listed in the pick list output but not deducted from your available count).

Double-faced cards are handled automatically: MTGTop8 uses only the front face name (`Ral, Monsoon Mage`) while Moxfield stores the full name (`Ral, Monsoon Mage // Ral, Leyline Prodigy`). Both forms resolve to the same owned quantity.

The `scryfall` command takes any Scryfall search URL directly from your browser and matches the results against your collection by exact printing (set code + collector number), so you can find specific versions of cards you own.

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
  scryfall.py    ← Scryfall API client (legality fetching + search)
  prices.py      ← CardMarket price fetching
  cli.py         ← Click CLI entry point (mtg command)

api/
  handlers.py    ← business logic as plain strings (shared by all interfaces)
  bot.py         ← Discord slash-command bot
```
