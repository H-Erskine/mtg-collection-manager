"""Shared, cross-user cache of Scryfall printing data (scryfall_id -> cmc, etc).

Lives at ~/mtg_data/cards_cache.sqlite, separate from the per-user registry
and collection databases, since it holds bulk card-printing facts rather
than account/user metadata. Any lookup that needs a card's CMC by Scryfall
ID (e.g. ManaBox import) should check this cache first via get_cached_cmc,
falling back to resolve_cmc (added in a later task) for cache misses.
"""

import json
import sqlite3
import sys
import time
import urllib.request
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


def _scryfall_collection_by_id(scryfall_ids: list[str]) -> list[dict]:
    """POST a batch (<=75) of Scryfall IDs to /cards/collection, return matched card objects."""
    identifiers = [{"id": sid} for sid in scryfall_ids]
    payload = json.dumps({"identifiers": identifiers}).encode()
    req = urllib.request.Request(
        "https://api.scryfall.com/cards/collection",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "mtg-manager/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data.get("data", [])


def resolve_cmc(scryfall_ids: list[str]) -> dict[str, float]:
    """Return {scryfall_id: cmc} for every non-blank id, using the cache first.

    Cache misses are batched to Scryfall's /cards/collection (75 ids per
    request, matching web/export_meta.py's existing batching convention) and
    the results are written back to the cache for every future caller.
    """
    unique_ids = list(dict.fromkeys(sid for sid in scryfall_ids if sid))
    result = get_cached_cmc(unique_ids)
    missing = [sid for sid in unique_ids if sid not in result]

    batch_size = 75
    for i in range(0, len(missing), batch_size):
        batch = missing[i:i + batch_size]
        batch_num = i // batch_size + 1
        try:
            cards = _scryfall_collection_by_id(batch)
        except Exception as e:
            print(f"[warn] Scryfall CMC batch {batch_num} failed: {e}", file=sys.stderr)
            continue
        printings = []
        for card in cards:
            sid = card.get("id")
            if not sid:
                continue
            cmc = float(card.get("cmc", 0) or 0)
            result[sid] = cmc
            printings.append({
                "scryfall_id": sid,
                "name": card.get("name", ""),
                "set_code": card.get("set", ""),
                "collector_number": card.get("collector_number", ""),
                "cmc": cmc,
            })
        cache_printings(printings)
        if i + batch_size < len(missing):
            time.sleep(0.15)

    return result
