"""
Cache eviction script — run nightly via systemd timer or cron.

Deletes owned_cards and for_sale_cards for any user who hasn't been seen
in the last THRESHOLD_DAYS days. Built decks, card tags, and legality data
are preserved (user-created, hard to regenerate).

The bot owner is never evicted because they have no registry row.

Usage:
  python -m scripts.evict_cache [--days N] [--dry-run]
"""

import argparse
import logging
import sys
from pathlib import Path

# Allow running as `python -m scripts.evict_cache` from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.users import list_users_for_eviction, _USERS_DIR
from mtg_manager.db import get_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def evict(threshold_days: int = 7, dry_run: bool = False) -> None:
    stale_ids = list_users_for_eviction(threshold_days)

    if not stale_ids:
        logger.info("No users to evict (threshold: %d days).", threshold_days)
        return

    logger.info("Found %d user(s) inactive for >%d days.", len(stale_ids), threshold_days)

    for discord_id in stale_ids:
        db_path = _USERS_DIR / f"{discord_id}.sqlite"
        if not db_path.exists():
            logger.info("  %s — no DB file, skipping.", discord_id)
            continue

        if dry_run:
            logger.info("  %s — DRY RUN, would evict owned_cards + for_sale_cards.", discord_id)
            continue

        try:
            with get_conn(db_path) as conn:
                owned = conn.execute("SELECT COUNT(*) FROM owned_cards").fetchone()[0]
                sale = conn.execute("SELECT COUNT(*) FROM for_sale_cards").fetchone()[0]
                conn.execute("DELETE FROM owned_cards")
                conn.execute("DELETE FROM for_sale_cards")
                conn.execute("VACUUM")
            logger.info(
                "  %s — evicted %d owned_cards + %d for_sale_cards rows.",
                discord_id, owned, sale,
            )
        except Exception as exc:
            logger.error("  %s — eviction failed: %s", discord_id, exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evict stale user cache rows.")
    parser.add_argument("--days", type=int, default=7, help="Inactivity threshold in days (default: 7)")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be evicted without deleting")
    args = parser.parse_args()

    evict(threshold_days=args.days, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
