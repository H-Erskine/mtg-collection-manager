"""
Discord slash-command bot for MTG Manager.

Environment variables required (in .env):
  DISCORD_BOT_TOKEN  — from the Discord Developer Portal

Run locally:
  python -m api.bot
"""
import asyncio
import logging
import os

import discord
from discord import app_commands
from dotenv import load_dotenv

from .handlers import (
    HELP_TEXT,
    handle_boxes,
    handle_build,
    handle_card,
    handle_extras,
    handle_help,
    handle_missing,
    handle_search,
    handle_stats,
    handle_sync,
    handle_unbox,
)

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# No privileged intents needed for slash commands
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


def _split_message(text: str, limit: int = 1990) -> list[str]:
    """Split a long string into chunks that fit within Discord's 2000 char limit."""
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


async def _send(interaction: discord.Interaction, text: str) -> None:
    """Send a reply, splitting into follow-up messages if over 2000 chars."""
    chunks = _split_message(text)
    await interaction.followup.send(chunks[0])
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

@tree.command(name="help", description="Show all available MTG Manager commands")
async def cmd_help(interaction: discord.Interaction):
    await interaction.response.defer()
    await _send(interaction, handle_help())


@tree.command(name="sync", description="Fetch Moxfield packages and update the local collection")
@app_commands.describe(color_group="Only sync a specific color group (e.g. White)")
async def cmd_sync(interaction: discord.Interaction, color_group: str = None):
    await interaction.response.defer()
    reply = await asyncio.get_event_loop().run_in_executor(None, handle_sync, color_group)
    await _send(interaction, reply)


@tree.command(name="missing", description="Show cards you need to order for a deck or compare URL")
@app_commands.describe(
    url="MTGTop8 deck or compare URL",
    min_variants="Only show cards appearing in at least N variants",
    sideboard="Include sideboard cards",
)
async def cmd_missing(
    interaction: discord.Interaction,
    url: str,
    min_variants: int = 1,
    sideboard: bool = False,
):
    await interaction.response.defer()
    reply = await asyncio.get_event_loop().run_in_executor(
        None, handle_missing, url, sideboard, min_variants
    )
    await _send(interaction, reply)


@tree.command(name="build", description="Mark a deck as built and allocate its cards to a box")
@app_commands.describe(
    url="MTGTop8 single deck URL",
    box_name="Name of the box to store this deck in",
    sideboard="Include sideboard cards in the allocation",
)
async def cmd_build(
    interaction: discord.Interaction,
    url: str,
    box_name: str,
    sideboard: bool = False,
):
    await interaction.response.defer()
    reply = await asyncio.get_event_loop().run_in_executor(
        None, handle_build, url, box_name, sideboard
    )
    await _send(interaction, reply)


@tree.command(name="boxes", description="List all built decks grouped by box")
async def cmd_boxes(interaction: discord.Interaction):
    await interaction.response.defer()
    reply = await asyncio.get_event_loop().run_in_executor(None, handle_boxes)
    await _send(interaction, reply)


@tree.command(name="unbox", description="Remove a built deck and return its cards to the pool")
@app_commands.describe(deck_name="Deck name shown in /boxes")
async def cmd_unbox(interaction: discord.Interaction, deck_name: str):
    await interaction.response.defer()
    reply = await asyncio.get_event_loop().run_in_executor(None, handle_unbox, deck_name)
    await _send(interaction, reply)


@tree.command(name="extras", description="List cards you own more than N copies of — potential trade stock")
@app_commands.describe(limit="Flag cards with more than this many copies (default 4)")
async def cmd_extras(interaction: discord.Interaction, limit: int = 4):
    await interaction.response.defer()
    reply = await asyncio.get_event_loop().run_in_executor(None, handle_extras, limit)
    await _send(interaction, reply)


@tree.command(name="search", description="Search your collection for a card by name")
@app_commands.describe(query="Partial or full card name to search for")
async def cmd_search(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    reply = await asyncio.get_event_loop().run_in_executor(None, handle_search, query)
    await _send(interaction, reply)


@tree.command(name="stats", description="Show collection stats and breakdown by color group")
async def cmd_stats(interaction: discord.Interaction):
    await interaction.response.defer()
    reply = await asyncio.get_event_loop().run_in_executor(None, handle_stats)
    await _send(interaction, reply)


@tree.command(name="card", description="Look up a card on Scryfall — shows type, text, and price")
@app_commands.describe(name="Card name (fuzzy search supported)")
async def cmd_card(interaction: discord.Interaction, name: str):
    await interaction.response.defer()
    reply = await asyncio.get_event_loop().run_in_executor(None, handle_card, name)
    await _send(interaction, reply)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@client.event
async def on_ready():
    for guild in client.guilds:
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)
        logger.info(
            "Synced %d commands to guild '%s': %s",
            len(synced),
            guild.name,
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
