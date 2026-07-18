"""
Meta decklist refresh script — run nightly via systemd timer or cron.

Fetches the top meta decklists per tracked format from MTGGoldfish and saves
them to the meta_decks/meta_deck_cards tables. Comparison against the owned
collection happens live (in handle_meta and the web meta.json export), so
this script only needs to keep the saved decklists themselves fresh.

Usage:
  python -m scripts.refresh_meta [--format modern --format standard] [--count 30]
"""

import argparse
import logging
import sys
from pathlib import Path

# Allow running as `python -m scripts.refresh_meta` from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from mtg_manager.config import load_config
from mtg_manager.db import get_conn, replace_meta_decks
from mtg_manager.goldfish import fetch_meta_decklists

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def refresh(cfg, formats: list[str], count: int = 30) -> None:
    with get_conn(cfg.db_path) as conn:
        for fmt in formats:
            logger.info("Fetching %s meta (%d decks)…", fmt, count)
            try:
                decklists = fetch_meta_decklists(fmt, limit=count, delay=cfg.mtgtop8_delay)
            except Exception as exc:
                logger.error("  %s — fetch failed: %s", fmt, exc)
                continue

            if not decklists:
                logger.warning("  %s — no decklists found, leaving saved data untouched.", fmt)
                continue

            replace_meta_decks(conn, fmt, decklists)
            logger.info("  %s — saved %d decklists.", fmt, len(decklists))


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh saved meta decklists from MTGGoldfish.")
    parser.add_argument(
        "--format", "-f", dest="formats", action="append",
        default=[], metavar="FORMAT",
        help="Format to refresh (repeatable, default: config [formats].tracked)",
    )
    parser.add_argument("--count", "-n", type=int, default=30, help="Decks per format")
    args = parser.parse_args()

    cfg = load_config()
    formats = args.formats or cfg.formats or ["modern", "standard"]
    refresh(cfg, formats, count=args.count)


if __name__ == "__main__":
    main()
