"""Live (non-exported) per-user collection/deck data for the web app."""

from datetime import datetime, timezone

from mtg_manager.config import Config
from mtg_manager.db import get_conn
from web.export import get_collection_data, get_decks_data


def get_collection(cfg: Config) -> dict:
    with get_conn(cfg.db_path) as conn:
        cards = get_collection_data(conn)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cards": cards,
    }


def get_decks(cfg: Config) -> dict:
    with get_conn(cfg.db_path) as conn:
        decks = get_decks_data(conn)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "decks": decks,
    }
