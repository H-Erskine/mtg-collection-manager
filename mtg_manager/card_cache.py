"""Shared, cross-user cache of Scryfall printing data (scryfall_id -> cmc, etc).

Lives at ~/mtg_data/cards_cache.sqlite, separate from the per-user registry
and collection databases, since it holds bulk card-printing facts rather
than account/user metadata. Any lookup that needs a card's CMC by Scryfall
ID (e.g. ManaBox import) should check this cache first via get_cached_cmc,
falling back to resolve_cmc (added in a later task) for cache misses.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

_CACHE_PATH = Path("~/mtg_data/cards_cache.sqlite").expanduser()

SCHEMA = """
CREATE TABLE IF NOT EXISTS card_printings (
    scryfall_id      TEXT NOT NULL PRIMARY KEY,
    name             TEXT NOT NULL,
    set_code         TEXT NOT NULL,
    collector_number TEXT NOT NULL,
    cmc              REAL NOT NULL DEFAULT 0,
    cached_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@contextmanager
def _cache_conn():
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_CACHE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_cached_cmc(scryfall_ids: list[str]) -> dict[str, float]:
    """Return {scryfall_id: cmc} for whichever of the given ids are cached."""
    if not scryfall_ids:
        return {}
    with _cache_conn() as conn:
        placeholders = ",".join("?" * len(scryfall_ids))
        rows = conn.execute(
            f"SELECT scryfall_id, cmc FROM card_printings WHERE scryfall_id IN ({placeholders})",
            scryfall_ids,
        ).fetchall()
        return {r["scryfall_id"]: r["cmc"] for r in rows}


def cache_printings(printings: list[dict]) -> None:
    """Insert or update printing rows. Each dict needs scryfall_id, name, set_code, collector_number, cmc."""
    if not printings:
        return
    with _cache_conn() as conn:
        conn.executemany(
            """
            INSERT INTO card_printings (scryfall_id, name, set_code, collector_number, cmc, cached_at)
            VALUES (:scryfall_id, :name, :set_code, :collector_number, :cmc, datetime('now'))
            ON CONFLICT (scryfall_id) DO UPDATE SET
                name = excluded.name,
                set_code = excluded.set_code,
                collector_number = excluded.collector_number,
                cmc = excluded.cmc,
                cached_at = excluded.cached_at
            """,
            printings,
        )
