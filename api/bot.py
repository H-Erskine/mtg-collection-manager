"""
Discord slash-command bot for MTG Manager.

Environment variables (in .env):
  DISCORD_BOT_TOKEN   — from the Discord Developer Portal
  OWNER_DISCORD_ID    — your Discord user ID; routes to ~/.mtg_manager/config.toml
                        and keeps auto-sync behaviour. Optional — if unset, no
                        special owner treatment.

Run locally:
  python -m api.bot
"""
import asyncio
import logging
import os

import discord
from discord import app_commands
from dotenv import load_dotenv

from . import users
from .handlers import (
    fetch_card_data,
    handle_boxes,
    handle_build,
    handle_cleartag,
    handle_extras,
    handle_forsale,
    handle_help,
    handle_illegal,
    handle_missing,
    handle_proxy,
    handle_scryfall,
    handle_search,
    handle_stats,
    handle_sync,
    handle_tag,
    handle_tagged,
    handle_unbox,
    handle_untag,
    handle_version,
)

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

SYNC_THROTTLE_MINUTES = 60

# ---------------------------------------------------------------------------
# Embed colour palette
# ---------------------------------------------------------------------------

COLOR_SUCCESS = 0x57F287
COLOR_ERROR   = 0xED4245
COLOR_INFO    = 0x5865F2
COLOR_WARNING = 0xFEE75C

_MTG_COLOR_MAP = {
    "W": 0xF8F6F1,
    "U": 0x0E68AB,
    "B": 0x2C2C2C,
    "R": 0xD3202A,
    "G": 0x00733E,
}


def _card_embed_color(colors: list[str]) -> int:
    if not colors:
        return 0x9C9C9C
    if len(colors) > 1:
        return 0xC8A227
    return _MTG_COLOR_MAP.get(colors[0], COLOR_INFO)


# ---------------------------------------------------------------------------
# Message / embed helpers
# ---------------------------------------------------------------------------

def _split_message(text: str, limit: int = 1990) -> list[str]:
    chunks = []
    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


async def _send_embed(
    interaction: discord.Interaction,
    text: str,
    title: str = "",
    color: int = COLOR_INFO,
    code_block: bool = False,
) -> None:
    WRAP = ("```\n", "\n```") if code_block else ("", "")
    content_limit = 4096 - len(WRAP[0]) - len(WRAP[1])
    chunks = _split_message(text, limit=content_limit)
    first = True
    for chunk in chunks:
        description = WRAP[0] + chunk + WRAP[1]
        embed = discord.Embed(
            title=title if first else "",
            description=description,
            color=color,
        )
        await interaction.followup.send(embed=embed)
        first = False


async def _send_card_embed(interaction: discord.Interaction, data: dict) -> None:
    color = _card_embed_color(data["colors"])
    embed = discord.Embed(
        title=data["name"],
        url=data["scryfall_uri"] or None,
        color=color,
    )
    if data["mana_cost"]:
        embed.add_field(name="Mana Cost", value=data["mana_cost"], inline=True)
    if data["type_line"]:
        embed.add_field(name="Type", value=data["type_line"], inline=True)
    if data["oracle_text"]:
        embed.description = data["oracle_text"]
    price_parts = []
    if data["price_usd"]:
        price_parts.append(f"${data['price_usd']}")
    if data["price_usd_foil"]:
        price_parts.append(f"${data['price_usd_foil']} foil")
    if price_parts:
        embed.add_field(name="Price (USD)", value=" | ".join(price_parts), inline=False)
    if data["image_uri"]:
        embed.set_thumbnail(url=data["image_uri"])
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# User resolution helper
# ---------------------------------------------------------------------------

async def _resolve_user(interaction: discord.Interaction, require_packages: bool = False):
    """Return (cfg, is_owner) or send an error reply and return (None, False).

    When require_packages=True, also errors if the user has no packages set up.
    """
    discord_id = str(interaction.user.id)
    owner = users.is_owner(discord_id)

    cfg = await asyncio.get_event_loop().run_in_executor(
        None, users.get_user_config, discord_id
    )
    if cfg is None:
        await _send_embed(
            interaction,
            "You're not set up yet. Run `/setup` to register, then `/addpackage` to add your Moxfield collection.",
            title="Not Registered",
            color=COLOR_ERROR,
        )
        return None, False

    if require_packages and not cfg.packages:
        await _send_embed(
            interaction,
            "No Moxfield packages added yet.\nUse `/addpackage <color_group> <public_id>` to add your collection, then `/sync`.",
            title="No Packages",
            color=COLOR_WARNING,
        )
        return None, False

    if not owner:
        await asyncio.get_event_loop().run_in_executor(None, users.mark_seen, discord_id)

    return cfg, owner


_OWNER_MSG = "You're the bot operator — edit `~/.mtg_manager/config.toml` directly to change packages or preferences."

# ---------------------------------------------------------------------------
# Account setup commands (ephemeral)
# ---------------------------------------------------------------------------

@tree.command(name="setup", description="Register your account to use the MTG Manager bot")
async def cmd_setup(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    discord_id = str(interaction.user.id)

    if users.is_owner(discord_id):
        await _send_embed(interaction, _OWNER_MSG, title="Bot Operator", color=COLOR_INFO)
        return

    await asyncio.get_event_loop().run_in_executor(None, users.ensure_user, discord_id)
    await _send_embed(
        interaction,
        "Account created! Next steps:\n"
        "1. `/addpackage <color_group> <public_id>` — add each Moxfield package\n"
        "   (find the public_id in your Moxfield deck URL)\n"
        "2. `/sync` — fetch your collection\n"
        "3. `/missing <url>` — check what you need for a deck\n\n"
        "Run `/listpackages` to see what you've added.",
        title="Welcome to MTG Manager",
        color=COLOR_SUCCESS,
    )


@tree.command(name="addpackage", description="Add a Moxfield package to your account")
@app_commands.describe(
    color_group="Label for this package (e.g. White, Blue, Multicolour)",
    public_id="The slug at the end of your Moxfield deck URL",
)
async def cmd_addpackage(interaction: discord.Interaction, color_group: str, public_id: str):
    await interaction.response.defer(ephemeral=True)
    discord_id = str(interaction.user.id)

    if users.is_owner(discord_id):
        await _send_embed(interaction, _OWNER_MSG, title="Bot Operator", color=COLOR_INFO)
        return

    if not users.is_registered(discord_id):
        await _send_embed(interaction, "Run `/setup` first.", title="Not Registered", color=COLOR_ERROR)
        return

    await asyncio.get_event_loop().run_in_executor(
        None, users.add_package, discord_id, color_group, public_id
    )
    pkgs = await asyncio.get_event_loop().run_in_executor(None, users.list_packages, discord_id)
    pkg_lines = "\n".join(f"  {cg} → `{pid}`" for cg, pid in pkgs)
    await _send_embed(
        interaction,
        f"Added **{color_group}** (`{public_id}`).\n\nYour packages:\n{pkg_lines}\n\nRun `/sync` to fetch your collection.",
        title="Package Added",
        color=COLOR_SUCCESS,
    )


@tree.command(name="removepackage", description="Remove a Moxfield package from your account")
@app_commands.describe(color_group="The color_group label to remove")
async def cmd_removepackage(interaction: discord.Interaction, color_group: str):
    await interaction.response.defer(ephemeral=True)
    discord_id = str(interaction.user.id)

    if users.is_owner(discord_id):
        await _send_embed(interaction, _OWNER_MSG, title="Bot Operator", color=COLOR_INFO)
        return

    if not users.is_registered(discord_id):
        await _send_embed(interaction, "Run `/setup` first.", title="Not Registered", color=COLOR_ERROR)
        return

    removed = await asyncio.get_event_loop().run_in_executor(
        None, users.remove_package, discord_id, color_group
    )
    if not removed:
        await _send_embed(interaction, f"No package named '{color_group}' found.", title="Not Found", color=COLOR_WARNING)
        return

    pkgs = await asyncio.get_event_loop().run_in_executor(None, users.list_packages, discord_id)
    remaining = "\n".join(f"  {cg} → `{pid}`" for cg, pid in pkgs) if pkgs else "  (none)"
    await _send_embed(
        interaction,
        f"Removed **{color_group}**.\n\nRemaining packages:\n{remaining}",
        title="Package Removed",
        color=COLOR_SUCCESS,
    )


@tree.command(name="listpackages", description="Show your registered Moxfield packages")
async def cmd_listpackages(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    discord_id = str(interaction.user.id)

    if users.is_owner(discord_id):
        await _send_embed(interaction, _OWNER_MSG, title="Bot Operator", color=COLOR_INFO)
        return

    if not users.is_registered(discord_id):
        await _send_embed(interaction, "Run `/setup` first.", title="Not Registered", color=COLOR_ERROR)
        return

    pkgs = await asyncio.get_event_loop().run_in_executor(None, users.list_packages, discord_id)
    if not pkgs:
        body = "No packages yet. Use `/addpackage` to add one."
    else:
        body = "\n".join(f"  {cg} → `{pid}`" for cg, pid in pkgs)
    await _send_embed(interaction, body, title="Your Packages", color=COLOR_INFO)


@tree.command(name="setsort", description="Set your pick list sort order")
@app_commands.describe(mode="One of: colour, alphabetical, set, cmc")
async def cmd_setsort(interaction: discord.Interaction, mode: str):
    await interaction.response.defer(ephemeral=True)
    discord_id = str(interaction.user.id)

    if users.is_owner(discord_id):
        await _send_embed(interaction, _OWNER_MSG, title="Bot Operator", color=COLOR_INFO)
        return

    if not users.is_registered(discord_id):
        await _send_embed(interaction, "Run `/setup` first.", title="Not Registered", color=COLOR_ERROR)
        return

    try:
        await asyncio.get_event_loop().run_in_executor(None, users.set_sort, discord_id, mode)
    except ValueError as e:
        await _send_embed(interaction, str(e), title="Invalid Sort Mode", color=COLOR_ERROR)
        return

    await _send_embed(interaction, f"Pick list sort set to **{mode}**.", title="Preference Saved", color=COLOR_SUCCESS)


@tree.command(name="setformats", description="Set your tracked formats for legality checks")
@app_commands.describe(formats="Comma-separated formats, e.g. modern,legacy,pioneer")
async def cmd_setformats(interaction: discord.Interaction, formats: str):
    await interaction.response.defer(ephemeral=True)
    discord_id = str(interaction.user.id)

    if users.is_owner(discord_id):
        await _send_embed(interaction, _OWNER_MSG, title="Bot Operator", color=COLOR_INFO)
        return

    if not users.is_registered(discord_id):
        await _send_embed(interaction, "Run `/setup` first.", title="Not Registered", color=COLOR_ERROR)
        return

    fmt_list = [f.strip() for f in formats.split(",") if f.strip()]
    await asyncio.get_event_loop().run_in_executor(None, users.set_formats, discord_id, fmt_list)
    await _send_embed(
        interaction,
        f"Tracked formats set to: **{', '.join(fmt_list)}**",
        title="Preference Saved",
        color=COLOR_SUCCESS,
    )


# ---------------------------------------------------------------------------
# Slash commands — ephemeral (personal/admin)
# ---------------------------------------------------------------------------

@tree.command(name="help", description="Show all available MTG Manager commands")
async def cmd_help(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await _send_embed(
        interaction, handle_help(),
        title="MTG Manager — Commands",
        color=COLOR_INFO,
        code_block=True,
    )


@tree.command(name="sync", description="Fetch Moxfield packages and update your collection")
@app_commands.describe(color_group="Only sync a specific color group (e.g. White)")
async def cmd_sync(interaction: discord.Interaction, color_group: str = None):
    await interaction.response.defer(ephemeral=True)
    discord_id = str(interaction.user.id)
    cfg, owner = await _resolve_user(interaction, require_packages=True)
    if cfg is None:
        return

    # Non-owners are throttled to once per 60 minutes
    if not owner:
        mins = await asyncio.get_event_loop().run_in_executor(
            None, users.minutes_since_last_sync, discord_id
        )
        if mins is not None and mins < SYNC_THROTTLE_MINUTES:
            remaining = int(SYNC_THROTTLE_MINUTES - mins)
            await _send_embed(
                interaction,
                f"Already synced {int(mins)} min ago. Try again in {remaining} min.",
                title="Sync Throttled",
                color=COLOR_WARNING,
            )
            return

    reply = await asyncio.get_event_loop().run_in_executor(
        None, handle_sync, cfg, owner, color_group
    )

    if not owner:
        await asyncio.get_event_loop().run_in_executor(None, users.mark_synced, discord_id)

    is_error = reply.startswith("Error:") or "Failed" in reply
    await _send_embed(
        interaction, reply,
        title="Collection Sync",
        color=COLOR_ERROR if is_error else COLOR_SUCCESS,
    )


@tree.command(name="extras", description="List cards you own more than N copies of — potential trade stock")
@app_commands.describe(
    limit="Flag cards with more than this many copies (default 4)",
    basic="Just list card names without set codes",
    fmt="Only show cards legal in this format (e.g. modern)",
)
async def cmd_extras(interaction: discord.Interaction, limit: int = 4, basic: bool = False, fmt: str = ""):
    await interaction.response.defer(ephemeral=True)
    cfg, owner = await _resolve_user(interaction, require_packages=True)
    if cfg is None:
        return

    reply = await asyncio.get_event_loop().run_in_executor(
        None, handle_extras, cfg, owner, limit, basic, fmt or None
    )
    title = f"Spare Cards (>{limit} copies)"
    if fmt:
        title += f" — legal in {fmt}"
    await _send_embed(interaction, reply, title=title, color=COLOR_WARNING, code_block=True)


@tree.command(name="stats", description="Show collection stats and breakdown by color group")
async def cmd_stats(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    cfg, owner = await _resolve_user(interaction, require_packages=True)
    if cfg is None:
        return

    reply = await asyncio.get_event_loop().run_in_executor(None, handle_stats, cfg, owner)
    await _send_embed(interaction, reply, title="Collection Stats", color=COLOR_INFO)


@tree.command(name="search", description="Search your collection for a card by name")
@app_commands.describe(query="Partial or full card name to search for")
async def cmd_search(interaction: discord.Interaction, query: str):
    await interaction.response.defer(ephemeral=True)
    cfg, owner = await _resolve_user(interaction, require_packages=True)
    if cfg is None:
        return

    reply = await asyncio.get_event_loop().run_in_executor(None, handle_search, query, cfg, owner)
    not_found = reply.startswith("No cards found")
    await _send_embed(
        interaction, reply,
        title=f'Search: "{query}"',
        color=COLOR_WARNING if not_found else COLOR_INFO,
        code_block=not not_found,
    )


@tree.command(name="illegal", description="List owned cards not legal in any of your configured formats")
@app_commands.describe(fmt="Override format (e.g. modern). Default: formats set via /setformats.")
async def cmd_illegal(interaction: discord.Interaction, fmt: str = ""):
    await interaction.response.defer(ephemeral=True)
    cfg, owner = await _resolve_user(interaction, require_packages=True)
    if cfg is None:
        return

    reply = await asyncio.get_event_loop().run_in_executor(None, handle_illegal, cfg, owner, fmt or None)
    is_error = reply.startswith("Error:") or "No formats configured" in reply
    await _send_embed(
        interaction, reply,
        title="Illegal Cards",
        color=COLOR_ERROR if is_error else COLOR_WARNING,
        code_block=True,
    )


@tree.command(name="tag", description="Add a tag to a card (e.g. signed, surge foil, LP)")
@app_commands.describe(
    name="Card name",
    tag="Tag to add",
    set_code="Set code to narrow the match (e.g. MH3)",
    foil="Target the foil printing",
)
async def cmd_tag(interaction: discord.Interaction, name: str, tag: str, set_code: str = "", foil: bool = False):
    await interaction.response.defer(ephemeral=True)
    cfg, owner = await _resolve_user(interaction)
    if cfg is None:
        return

    reply = await asyncio.get_event_loop().run_in_executor(
        None, handle_tag, name, tag, cfg, owner, set_code, foil
    )
    await _send_embed(interaction, reply, title="Card Tagged",
                      color=COLOR_ERROR if reply.startswith("Error:") else COLOR_SUCCESS)


@tree.command(name="untag", description="Remove a tag from a card")
@app_commands.describe(name="Card name", tag="Tag to remove", set_code="Set code to narrow the match", foil="Target the foil printing")
async def cmd_untag(interaction: discord.Interaction, name: str, tag: str, set_code: str = "", foil: bool = False):
    await interaction.response.defer(ephemeral=True)
    cfg, owner = await _resolve_user(interaction)
    if cfg is None:
        return

    reply = await asyncio.get_event_loop().run_in_executor(
        None, handle_untag, name, tag, cfg, owner, set_code, foil
    )
    await _send_embed(interaction, reply, title="Tag Removed",
                      color=COLOR_WARNING if "not found" in reply else COLOR_SUCCESS)


@tree.command(name="tagged", description="List all cards with a given tag")
@app_commands.describe(tag="Tag to search for (e.g. signed)")
async def cmd_tagged(interaction: discord.Interaction, tag: str):
    await interaction.response.defer(ephemeral=True)
    cfg, owner = await _resolve_user(interaction)
    if cfg is None:
        return

    reply = await asyncio.get_event_loop().run_in_executor(None, handle_tagged, tag, cfg, owner)
    await _send_embed(interaction, reply, title=f"Cards tagged '{tag}'", color=COLOR_INFO)


@tree.command(name="cleartag", description="Remove a tag from all cards that have it")
@app_commands.describe(tag="Tag to clear from all cards")
async def cmd_cleartag(interaction: discord.Interaction, tag: str):
    await interaction.response.defer(ephemeral=True)
    cfg, owner = await _resolve_user(interaction)
    if cfg is None:
        return

    reply = await asyncio.get_event_loop().run_in_executor(None, handle_cleartag, tag, cfg, owner)
    await _send_embed(interaction, reply, title="Tag Cleared",
                      color=COLOR_ERROR if reply.startswith("Error:") else COLOR_SUCCESS)


@tree.command(name="version", description="Show the current git commit the bot is running")
async def cmd_version(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    reply = await asyncio.get_event_loop().run_in_executor(None, handle_version)
    await _send_embed(interaction, reply, title="Version", color=COLOR_INFO)


# ---------------------------------------------------------------------------
# Slash commands — public by default (share-friendly), private flag available
# ---------------------------------------------------------------------------

@tree.command(name="missing", description="Show cards you need to order for a deck or compare URL")
@app_commands.describe(
    url="MTGTop8 deck or compare URL",
    min_variants="Only show cards appearing in at least N variants",
    sideboard="Include sideboard cards",
    private="Only show the response to you (default: public)",
)
async def cmd_missing(
    interaction: discord.Interaction,
    url: str,
    min_variants: int = 1,
    sideboard: bool = False,
    private: bool = False,
):
    await interaction.response.defer(ephemeral=private)
    cfg, owner = await _resolve_user(interaction, require_packages=True)
    if cfg is None:
        return

    reply = await asyncio.get_event_loop().run_in_executor(
        None, handle_missing, url, cfg, owner, sideboard, min_variants
    )
    is_error = reply.startswith("Error:") or reply.startswith("Failed") or reply.startswith("No decklist")
    all_good = "You have all the cards" in reply
    color = COLOR_ERROR if is_error else (COLOR_SUCCESS if all_good else COLOR_WARNING)
    await _send_embed(interaction, reply, title="Missing Cards", color=color, code_block=True)


@tree.command(name="build", description="Mark a deck as built and allocate its cards to a box")
@app_commands.describe(
    url="MTGTop8 single deck URL",
    box_name="Name of the box to store this deck in",
    sideboard="Include sideboard cards in the allocation",
    private="Only show the response to you (default: public)",
)
async def cmd_build(
    interaction: discord.Interaction,
    url: str,
    box_name: str,
    sideboard: bool = False,
    private: bool = False,
):
    await interaction.response.defer(ephemeral=private)
    cfg, owner = await _resolve_user(interaction, require_packages=True)
    if cfg is None:
        return

    reply = await asyncio.get_event_loop().run_in_executor(
        None, handle_build, url, box_name, cfg, owner, sideboard
    )
    is_error = reply.startswith("Error:") or reply.startswith("Failed") or reply.startswith("No decklist")
    has_warning = "Warning" in reply
    color = COLOR_ERROR if is_error else (COLOR_WARNING if has_warning else COLOR_SUCCESS)
    await _send_embed(interaction, reply, title="Deck Built", color=color, code_block=True)


@tree.command(name="boxes", description="List all built decks grouped by box")
@app_commands.describe(private="Only show the response to you (default: public)")
async def cmd_boxes(interaction: discord.Interaction, private: bool = False):
    await interaction.response.defer(ephemeral=private)
    cfg, owner = await _resolve_user(interaction, require_packages=True)
    if cfg is None:
        return

    reply = await asyncio.get_event_loop().run_in_executor(None, handle_boxes, cfg, owner)
    await _send_embed(interaction, reply, title="Built Decks", color=COLOR_INFO, code_block=True)


@tree.command(name="unbox", description="Remove a built deck and return its cards to the pool")
@app_commands.describe(
    deck_name="Deck name shown in /boxes",
    private="Only show the response to you (default: public)",
)
async def cmd_unbox(interaction: discord.Interaction, deck_name: str, private: bool = False):
    await interaction.response.defer(ephemeral=private)
    cfg, owner = await _resolve_user(interaction)
    if cfg is None:
        return

    reply = await asyncio.get_event_loop().run_in_executor(None, handle_unbox, deck_name, cfg, owner)
    is_error = "No built deck found" in reply or "Multiple decks" in reply
    await _send_embed(
        interaction, reply,
        title="Deck Unboxed",
        color=COLOR_WARNING if "Multiple decks" in reply else (COLOR_ERROR if is_error else COLOR_SUCCESS),
    )


@tree.command(name="forsale", description="List all cards for sale with CardMarket prices")
@app_commands.describe(
    min_price="Only show cards priced at or above this value in EUR",
    hide_price="Hide the price column — useful for posting publicly",
    fmt="Only show cards legal in this format (e.g. modern)",
    private="Only show the response to you (default: public)",
)
async def cmd_forsale(
    interaction: discord.Interaction,
    min_price: float = 0.0,
    hide_price: bool = False,
    fmt: str = "",
    private: bool = False,
):
    await interaction.response.defer(ephemeral=private)
    cfg, owner = await _resolve_user(interaction, require_packages=True)
    if cfg is None:
        return

    reply = await asyncio.get_event_loop().run_in_executor(
        None, handle_forsale, cfg, owner, min_price, not hide_price, fmt or None
    )
    is_empty = "No cards listed" in reply
    await _send_embed(
        interaction, reply,
        title="For Sale",
        color=COLOR_WARNING if is_empty else COLOR_INFO,
        code_block=True,
    )


@tree.command(name="proxy", description="Analyse decklists to find stock (core) cards vs flex slots")
@app_commands.describe(
    urls="Space- or comma-separated deck or compare URLs",
    threshold="% of lists a card must appear in to count as stock (default 75)",
    sideboard="Include sideboard cards in the analysis",
    private="Only show the response to you (default: public)",
)
async def cmd_proxy(
    interaction: discord.Interaction,
    urls: str,
    threshold: int = 75,
    sideboard: bool = False,
    private: bool = False,
):
    await interaction.response.defer(ephemeral=private)
    cfg, owner = await _resolve_user(interaction)
    if cfg is None:
        return

    reply = await asyncio.get_event_loop().run_in_executor(
        None, handle_proxy, urls, cfg, owner, threshold, sideboard
    )
    is_error = reply.startswith("Error:") or reply.startswith("Failed")
    await _send_embed(
        interaction, reply,
        title="Stock / Flex Analysis",
        color=COLOR_ERROR if is_error else COLOR_INFO,
        code_block=True,
    )


@tree.command(name="scryfall", description="Cross-reference a Scryfall search URL against your collection")
@app_commands.describe(
    url="Scryfall search URL — paste directly from your browser",
    private="Only show the response to you (default: public)",
)
async def cmd_scryfall(interaction: discord.Interaction, url: str, private: bool = False):
    await interaction.response.defer(ephemeral=private)
    cfg, owner = await _resolve_user(interaction, require_packages=True)
    if cfg is None:
        return

    reply = await asyncio.get_event_loop().run_in_executor(None, handle_scryfall, url, cfg, owner)
    is_error = reply.startswith("Error") or "none of those" in reply
    await _send_embed(
        interaction, reply,
        title="Scryfall Collection Match",
        color=COLOR_ERROR if is_error else COLOR_INFO,
        code_block=True,
    )


@tree.command(name="card", description="Look up a card on Scryfall — shows type, text, and price")
@app_commands.describe(
    name="Card name (fuzzy search supported)",
    private="Only show the response to you (default: public)",
)
async def cmd_card(interaction: discord.Interaction, name: str, private: bool = False):
    await interaction.response.defer(ephemeral=private)
    data = await asyncio.get_event_loop().run_in_executor(None, fetch_card_data, name)
    if isinstance(data, str):
        await _send_embed(interaction, data, title="Card Lookup", color=COLOR_ERROR)
        return
    await _send_card_embed(interaction, data)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@client.event
async def on_ready():
    synced = await tree.sync()
    logger.info(
        "Synced %d global commands: %s",
        len(synced),
        [cmd.name for cmd in synced],
    )
    logger.info("Logged in as %s", client.user)


def main():
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN not set in environment / .env file")
    client.run(token)


if __name__ == "__main__":
    main()
