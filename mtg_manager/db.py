import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .models import BoxedCard, MissingCard, OwnedCard


SCHEMA = """
CREATE TABLE IF NOT EXISTS card_tags (
    name        TEXT NOT NULL,
    set_code    TEXT NOT NULL DEFAULT '',
    foil        INTEGER NOT NULL DEFAULT 0,
    tag         TEXT NOT NULL,
    PRIMARY KEY (name, set_code, foil, tag)
);

CREATE TABLE IF NOT EXISTS for_sale_cards (
    name                TEXT NOT NULL,
    set_code            TEXT NOT NULL DEFAULT '',
    collector_number    TEXT NOT NULL DEFAULT '',
    foil                INTEGER NOT NULL DEFAULT 0,
    quantity            INTEGER NOT NULL DEFAULT 0,
    price               REAL NOT NULL DEFAULT 0,
    color_group         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (name, set_code, collector_number, foil)
);

CREATE TABLE IF NOT EXISTS owned_cards (
    name                TEXT NOT NULL,
    set_code            TEXT NOT NULL DEFAULT '',
    collector_number    TEXT NOT NULL DEFAULT '',
    color_group         TEXT NOT NULL DEFAULT '',
    foil                INTEGER NOT NULL DEFAULT 0,
    quantity            INTEGER NOT NULL DEFAULT 0,
    cmc                 REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (name, set_code, collector_number, foil)
);

CREATE INDEX IF NOT EXISTS idx_owned_cards_name ON owned_cards (name);

CREATE TABLE IF NOT EXISTS built_decks (
    deck_id     TEXT NOT NULL PRIMARY KEY,
    deck_name   TEXT NOT NULL,
    deck_url    TEXT NOT NULL,
    box_name    TEXT NOT NULL DEFAULT 'white box',
    built_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS allocated_cards (
    deck_id     TEXT NOT NULL REFERENCES built_decks(deck_id),
    card_name   TEXT NOT NULL,
    quantity    INTEGER NOT NULL DEFAULT 1,
    is_proxy    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (deck_id, card_name)
);

CREATE INDEX IF NOT EXISTS idx_allocated_card_name ON allocated_cards (card_name);

CREATE TABLE IF NOT EXISTS card_legalities (
    name     TEXT NOT NULL,
    format   TEXT NOT NULL,
    is_legal INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (name, format)
);
"""


@dataclass
class BoxAllocation:
    box_name: str
    deck_id: str
    deck_name: str
    quantity: int


@contextmanager
def get_conn(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        # Migration: add is_proxy to existing databases
        try:
            conn.execute("ALTER TABLE allocated_cards ADD COLUMN is_proxy INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists
        # Migration: add cmc to owned_cards
        try:
            conn.execute("ALTER TABLE owned_cards ADD COLUMN cmc REAL NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_cards(conn: sqlite3.Connection, cards: list[OwnedCard]) -> int:
    """Insert or replace owned cards. Returns number of rows affected."""
    conn.executemany(
        """
        INSERT INTO owned_cards (name, set_code, collector_number, color_group, foil, quantity, cmc)
        VALUES (:name, :set_code, :collector_number, :color_group, :foil, :quantity, :cmc)
        ON CONFLICT (name, set_code, collector_number, foil)
        DO UPDATE SET quantity = excluded.quantity,
                      color_group = excluded.color_group,
                      cmc = excluded.cmc
        """,
        [
            {
                "name": c.name,
                "set_code": c.set_code,
                "collector_number": c.collector_number,
                "color_group": c.color_group,
                "foil": int(c.foil),
                "quantity": c.quantity,
                "cmc": c.cmc,
            }
            for c in cards
        ],
    )
    return conn.total_changes


def get_owned_quantity(conn: sqlite3.Connection, card_name: str) -> int:
    """Return total copies owned for a card name (case-insensitive).

    Handles double-faced cards: MTGTop8 uses only the front face name
    (e.g. 'Ral, Monsoon Mage') while Moxfield stores the full name
    ('Ral, Monsoon Mage // Ral, Leyline Prodigy'). We match either way.
    """
    name_lower = card_name.lower()
    row = conn.execute(
        """
        SELECT COALESCE(SUM(quantity), 0) AS total
        FROM owned_cards
        WHERE LOWER(name) = ?
           OR LOWER(SUBSTR(name, 1, INSTR(name, ' // ') - 1)) = ?
        """,
        (name_lower, name_lower),
    ).fetchone()
    return row["total"] if row else 0


def get_card_set_code(conn: sqlite3.Connection, card_name: str) -> str:
    """Return the set code for a card (first owned copy, or '?' if unknown)."""
    name_lower = card_name.lower()
    row = conn.execute(
        """
        SELECT set_code FROM owned_cards
        WHERE LOWER(name) = ?
           OR LOWER(SUBSTR(name, 1, INSTR(name, ' // ') - 1)) = ?
        ORDER BY quantity DESC
        LIMIT 1
        """,
        (name_lower, name_lower),
    ).fetchone()
    return (row["set_code"] or "?").upper() if row else "?"


def get_card_cmc(conn: sqlite3.Connection, card_name: str) -> float:
    """Return the CMC for a card (highest-quantity copy), or 0 if unknown."""
    name_lower = card_name.lower()
    row = conn.execute(
        """
        SELECT cmc FROM owned_cards
        WHERE LOWER(name) = ?
           OR LOWER(SUBSTR(name, 1, INSTR(name, ' // ') - 1)) = ?
        ORDER BY quantity DESC
        LIMIT 1
        """,
        (name_lower, name_lower),
    ).fetchone()
    return row["cmc"] if row else 0.0


def get_card_color_group(conn: sqlite3.Connection, card_name: str) -> str:
    """Return the color group(s) that own copies of a card, as a display string."""
    name_lower = card_name.lower()
    rows = conn.execute(
        """
        SELECT DISTINCT color_group
        FROM owned_cards
        WHERE LOWER(name) = ?
           OR LOWER(SUBSTR(name, 1, INSTR(name, ' // ') - 1)) = ?
        ORDER BY color_group
        """,
        (name_lower, name_lower),
    ).fetchall()
    groups = [r["color_group"] for r in rows if r["color_group"]]
    return ", ".join(groups) if groups else "?"


def clear_color_group(conn: sqlite3.Connection, color_group: str) -> None:
    """Remove all cards for a color group before re-syncing it."""
    conn.execute("DELETE FROM owned_cards WHERE color_group = ?", (color_group,))


def upsert_for_sale_cards(conn: sqlite3.Connection, cards: list[OwnedCard], price: float) -> int:
    """Insert or replace for-sale cards at the given price. Returns rows affected."""
    conn.executemany(
        """
        INSERT INTO for_sale_cards (name, set_code, collector_number, foil, quantity, price, color_group)
        VALUES (:name, :set_code, :collector_number, :foil, :quantity, :price, :color_group)
        ON CONFLICT (name, set_code, collector_number, foil)
        DO UPDATE SET quantity = excluded.quantity,
                      price = excluded.price,
                      color_group = excluded.color_group
        """,
        [
            {
                "name": c.name,
                "set_code": c.set_code,
                "collector_number": c.collector_number,
                "foil": int(c.foil),
                "quantity": c.quantity,
                "price": price,
                "color_group": c.color_group,
            }
            for c in cards
        ],
    )
    return conn.total_changes


def clear_for_sale_color_group(conn: sqlite3.Connection, color_group: str) -> None:
    """Remove all for-sale cards for a color group before re-syncing it."""
    conn.execute("DELETE FROM for_sale_cards WHERE color_group = ?", (color_group,))


def list_for_sale_cards(
    conn: sqlite3.Connection,
    legal_formats: list[str] | None = None,
) -> list[sqlite3.Row]:
    """Return all for-sale cards ordered by price desc then name.

    When legal_formats is provided, only cards legal in at least one of those formats
    are returned. Each row gains an extra `legal_in` column (comma-separated format names).
    """
    if legal_formats:
        placeholders = ",".join("?" * len(legal_formats))
        return conn.execute(
            f"""
            SELECT fs.name, fs.set_code, fs.collector_number, fs.foil, fs.quantity, fs.price,
                   GROUP_CONCAT(cl.format) AS legal_in
            FROM for_sale_cards fs
            JOIN card_legalities cl
              ON LOWER(cl.name) = LOWER(fs.name)
             AND cl.format IN ({placeholders})
             AND cl.is_legal = 1
            GROUP BY fs.name, fs.set_code, fs.collector_number, fs.foil, fs.quantity, fs.price
            ORDER BY fs.price DESC, fs.name
            """,
            legal_formats,
        ).fetchall()
    return conn.execute(
        """
        SELECT name, set_code, collector_number, foil, quantity, price
        FROM for_sale_cards
        ORDER BY price DESC, name
        """
    ).fetchall()


def update_sale_prices(conn: sqlite3.Connection, prices: list[tuple[str, str, str, bool, float]]) -> int:
    """Bulk-update market prices for for-sale cards.

    prices: list of (name, set_code, collector_number, foil, price)
    Returns number of rows updated.
    """
    conn.executemany(
        """
        UPDATE for_sale_cards SET price = ?
        WHERE LOWER(name) = LOWER(?)
          AND LOWER(set_code) = LOWER(?)
          AND collector_number = ?
          AND foil = ?
        """,
        [(price, name, set_code, cn, int(foil)) for name, set_code, cn, foil, price in prices],
    )
    return conn.total_changes


# ---------------------------------------------------------------------------
# Card tags
# ---------------------------------------------------------------------------

def add_card_tag(conn: sqlite3.Connection, name: str, set_code: str, foil: bool, tag: str) -> None:
    """Add a tag to a card. No-op if the tag already exists."""
    conn.execute(
        "INSERT OR IGNORE INTO card_tags (name, set_code, foil, tag) VALUES (?, ?, ?, ?)",
        (name, set_code.lower(), int(foil), tag.strip()),
    )


def remove_card_tag(conn: sqlite3.Connection, name: str, set_code: str, foil: bool, tag: str) -> bool:
    """Remove a specific tag from a card. Returns True if a row was deleted."""
    conn.execute(
        "DELETE FROM card_tags WHERE LOWER(name) = ? AND LOWER(set_code) = ? AND foil = ? AND LOWER(tag) = ?",
        (name.lower(), set_code.lower(), int(foil), tag.strip().lower()),
    )
    return conn.total_changes > 0


def get_card_tags(conn: sqlite3.Connection, name: str, set_code: str = "", foil: bool | None = None) -> list[str]:
    """Return tags for a card. Matches on name (case-insensitive) and optionally set/foil."""
    params: list = [name.lower(), set_code.lower()]
    foil_clause = ""
    if foil is not None:
        foil_clause = "AND foil = ?"
        params.append(int(foil))
    rows = conn.execute(
        f"""
        SELECT tag FROM card_tags
        WHERE LOWER(name) = ? AND LOWER(set_code) = ?
        {foil_clause}
        ORDER BY tag
        """,
        params,
    ).fetchall()
    return [r["tag"] for r in rows]


def get_tags_for_sale_cards(conn: sqlite3.Connection) -> dict[tuple, list[str]]:
    """Return a dict mapping (lower_name, lower_set, foil_int) -> [tag, ...] for all tagged cards."""
    rows = conn.execute("SELECT name, set_code, foil, tag FROM card_tags ORDER BY name, tag").fetchall()
    result: dict[tuple, list[str]] = {}
    for r in rows:
        key = (r["name"].lower(), r["set_code"].lower(), r["foil"])
        result.setdefault(key, []).append(r["tag"])
    return result


def get_cards_by_tag(conn: sqlite3.Connection, tag: str) -> list[sqlite3.Row]:
    """Return distinct (name, set_code, foil) for all cards carrying the given tag."""
    return conn.execute(
        "SELECT DISTINCT name, set_code, foil FROM card_tags WHERE LOWER(tag) = ? ORDER BY name",
        (tag.strip().lower(),),
    ).fetchall()


def remove_all_cards_with_tag(conn: sqlite3.Connection, tag: str) -> int:
    """Remove a tag from every card that has it. Returns number of rows deleted."""
    conn.execute(
        "DELETE FROM card_tags WHERE LOWER(tag) = ?",
        (tag.strip().lower(),),
    )
    return conn.total_changes


BASIC_LANDS = {"forest", "island", "mountain", "plains", "swamp"}


def get_cards_over_limit(
    conn: sqlite3.Connection,
    limit: int = 4,
    legal_formats: list[str] | None = None,
) -> list[sqlite3.Row]:
    """Return all versions of cards whose total quantity exceeds `limit`, excluding basic lands
    and any cards currently listed in for_sale_cards.
    Rows are ordered by name then quantity DESC so the largest version comes first.

    When legal_formats is provided, only cards legal in at least one of those formats
    are returned. Each row gains an extra `legal_in` column (comma-separated format names).
    """
    if legal_formats:
        placeholders = ",".join("?" * len(legal_formats))
        return conn.execute(
            f"""
            SELECT oc.name, oc.set_code, oc.foil, oc.quantity, oc.color_group,
                   GROUP_CONCAT(cl.format) AS legal_in
            FROM owned_cards oc
            JOIN card_legalities cl
              ON LOWER(cl.name) = LOWER(oc.name)
             AND cl.format IN ({placeholders})
             AND cl.is_legal = 1
            WHERE LOWER(oc.name) NOT IN ('forest','island','mountain','plains','swamp')
              AND LOWER(oc.name) NOT IN (SELECT LOWER(name) FROM for_sale_cards)
              AND LOWER(oc.name) IN (
                SELECT LOWER(name) FROM owned_cards
                GROUP BY LOWER(name)
                HAVING SUM(quantity) > ?
              )
            GROUP BY oc.name, oc.set_code, oc.foil, oc.quantity, oc.color_group
            ORDER BY LOWER(oc.name), oc.quantity DESC
            """,
            (*legal_formats, limit),
        ).fetchall()
    return conn.execute(
        """
        SELECT name, set_code, foil, quantity, color_group
        FROM owned_cards
        WHERE LOWER(name) NOT IN ('forest','island','mountain','plains','swamp')
          AND LOWER(name) NOT IN (SELECT LOWER(name) FROM for_sale_cards)
          AND LOWER(name) IN (
            SELECT LOWER(name) FROM owned_cards
            GROUP BY LOWER(name)
            HAVING SUM(quantity) > ?
          )
        ORDER BY LOWER(name), quantity DESC
        """,
        (limit,),
    ).fetchall()


def upsert_legalities(conn: sqlite3.Connection, legality_map: dict[str, dict[str, bool]]) -> int:
    """Upsert format legality for a set of cards.

    legality_map: {card_name: {format: is_legal}}
    Returns number of rows affected.
    """
    rows = [
        (name, fmt, int(is_legal))
        for name, formats in legality_map.items()
        for fmt, is_legal in formats.items()
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO card_legalities (name, format, is_legal) VALUES (?, ?, ?)",
        rows,
    )
    return conn.total_changes


def get_names_missing_legality(conn: sqlite3.Connection, formats: list[str]) -> list[str]:
    """Return distinct card names in owned_cards missing a legality entry for at least one of the given formats."""
    placeholders = ",".join("?" * len(formats))
    rows = conn.execute(
        f"""
        SELECT DISTINCT oc.name
        FROM owned_cards oc
        WHERE (
            SELECT COUNT(DISTINCT cl.format)
            FROM card_legalities cl
            WHERE LOWER(cl.name) = LOWER(oc.name)
              AND cl.format IN ({placeholders})
        ) < ?
        """,
        (*formats, len(formats)),
    ).fetchall()
    return [r["name"] for r in rows]


def get_all_owned_names(conn: sqlite3.Connection) -> list[str]:
    """Return all distinct card names in owned_cards."""
    rows = conn.execute("SELECT DISTINCT name FROM owned_cards").fetchall()
    return [r["name"] for r in rows]


def get_illegal_owned_cards(conn: sqlite3.Connection, formats: list[str]) -> list[sqlite3.Row]:
    """Return owned cards (not for-sale) illegal in ALL of the given formats.

    Each row: name, set_code, foil, quantity, illegal_in (comma-separated formats).
    Sorted by name ASC.
    """
    placeholders = ",".join("?" * len(formats))
    return conn.execute(
        f"""
        SELECT agg.name,
               agg.set_code,
               agg.foil,
               agg.quantity,
               GROUP_CONCAT(DISTINCT cl.format) AS illegal_in
        FROM (
            SELECT name, set_code, foil, SUM(quantity) AS quantity
            FROM owned_cards
            WHERE LOWER(name) NOT IN (SELECT LOWER(name) FROM for_sale_cards)
            GROUP BY name, set_code, foil
        ) agg
        JOIN card_legalities cl
          ON LOWER(cl.name) = LOWER(agg.name)
         AND cl.format IN ({placeholders})
         AND cl.is_legal = 0
        GROUP BY agg.name, agg.set_code, agg.foil
        HAVING COUNT(DISTINCT cl.format) = ?
        ORDER BY agg.name
        """,
        (*formats, len(formats)),
    ).fetchall()


_PRINTING_BATCH = 100  # stay well under SQLite's expression-tree depth limit


def get_owned_by_printings(
    conn: sqlite3.Connection,
    printings: list[tuple[str, str]],
) -> list[sqlite3.Row]:
    """Return owned cards matching any of the given (set_code, collector_number) pairs.

    printings: list of (set_code, collector_number) — set_code is matched case-insensitively.
    Batches queries to avoid SQLite's expression-tree depth limit.
    Rows ordered by name then foil.
    """
    if not printings:
        return []
    rows: list[sqlite3.Row] = []
    for i in range(0, len(printings), _PRINTING_BATCH):
        batch = printings[i : i + _PRINTING_BATCH]
        conditions = " OR ".join(
            "(LOWER(set_code) = ? AND collector_number = ?)" for _ in batch
        )
        params = [v for p in batch for v in (p[0].lower(), p[1])]
        rows.extend(conn.execute(
            f"""
            SELECT name, set_code, collector_number, foil, quantity, color_group
            FROM owned_cards
            WHERE {conditions}
            ORDER BY name, set_code, foil
            """,
            params,
        ).fetchall())
    return rows


def card_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(SUM(quantity), 0) AS total FROM owned_cards").fetchone()
    return row["total"] if row else 0


# ---------------------------------------------------------------------------
# Box / allocation queries
# ---------------------------------------------------------------------------

def get_allocated_quantity(conn: sqlite3.Connection, card_name: str) -> int:
    """Total copies of a card currently allocated across all built decks."""
    name_lower = card_name.lower()
    row = conn.execute(
        """
        SELECT COALESCE(SUM(quantity), 0) AS total
        FROM allocated_cards
        WHERE LOWER(card_name) = ? AND is_proxy = 0
        """,
        (name_lower,),
    ).fetchone()
    return row["total"] if row else 0


def get_available_quantity(conn: sqlite3.Connection, card_name: str) -> int:
    """Owned copies minus allocated copies."""
    return get_owned_quantity(conn, card_name) - get_allocated_quantity(conn, card_name)


def get_card_allocations(conn: sqlite3.Connection, card_name: str) -> list[BoxAllocation]:
    """Return all box allocations for a given card name."""
    name_lower = card_name.lower()
    rows = conn.execute(
        """
        SELECT bd.box_name, ac.deck_id, bd.deck_name, ac.quantity
        FROM allocated_cards ac
        JOIN built_decks bd ON bd.deck_id = ac.deck_id
        WHERE LOWER(ac.card_name) = ?
        ORDER BY bd.box_name, bd.deck_name
        """,
        (name_lower,),
    ).fetchall()
    return [BoxAllocation(r["box_name"], r["deck_id"], r["deck_name"], r["quantity"]) for r in rows]


def get_deck(conn: sqlite3.Connection, deck_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM built_decks WHERE deck_id = ?", (deck_id,)
    ).fetchone()


def get_deck_by_url(conn: sqlite3.Connection, url: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM built_decks WHERE deck_url = ?", (url,)
    ).fetchone()


def get_decks_by_name(conn: sqlite3.Connection, deck_name: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM built_decks WHERE LOWER(deck_name) = LOWER(?)", (deck_name,)
    ).fetchall()


def insert_built_deck(
    conn: sqlite3.Connection,
    deck_id: str,
    deck_name: str,
    deck_url: str,
    box_name: str,
    cards: list[tuple[str, int, bool]],
) -> None:
    """Record a built deck and allocate its cards. cards is (name, qty, is_proxy)."""
    conn.execute(
        """
        INSERT INTO built_decks (deck_id, deck_name, deck_url, box_name)
        VALUES (?, ?, ?, ?)
        """,
        (deck_id, deck_name, deck_url, box_name),
    )
    conn.executemany(
        "INSERT INTO allocated_cards (deck_id, card_name, quantity, is_proxy) VALUES (?, ?, ?, ?)",
        [(deck_id, name, qty, int(is_proxy)) for name, qty, is_proxy in cards],
    )


def get_deck_return_list(conn: sqlite3.Connection, deck_id: str) -> list[sqlite3.Row]:
    """Return cards allocated to a deck with their owned_cards metadata for the return list.

    Each row: card_name, quantity, is_proxy, color_group, cmc.
    Cards not found in owned_cards (pure proxies never synced) get empty color_group and cmc=0.
    """
    return conn.execute(
        """
        SELECT
            ac.card_name,
            ac.quantity,
            ac.is_proxy,
            COALESCE(oc.color_group, '') AS color_group,
            COALESCE(oc.cmc, 0)         AS cmc
        FROM allocated_cards ac
        LEFT JOIN (
            SELECT
                name,
                color_group,
                cmc,
                ROW_NUMBER() OVER (PARTITION BY LOWER(name) ORDER BY quantity DESC) AS rn
            FROM owned_cards
        ) oc ON (
            LOWER(oc.name) = LOWER(ac.card_name)
            OR LOWER(SUBSTR(oc.name, 1, INSTR(oc.name, ' // ') - 1)) = LOWER(ac.card_name)
        ) AND oc.rn = 1
        WHERE ac.deck_id = ?
        ORDER BY ac.card_name
        """,
        (deck_id,),
    ).fetchall()


def delete_built_deck(conn: sqlite3.Connection, deck_id: str) -> bool:
    """Remove a built deck and return its cards to the pool. Returns False if not found."""
    if not get_deck(conn, deck_id):
        return False
    conn.execute("DELETE FROM allocated_cards WHERE deck_id = ?", (deck_id,))
    conn.execute("DELETE FROM built_decks WHERE deck_id = ?", (deck_id,))
    return True


def list_built_decks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM built_decks ORDER BY box_name, deck_name"
    ).fetchall()


def categorise_missing_cards(
    conn: sqlite3.Connection,
    card_needs: list[tuple[str, int, int]],
    total_variants: int,
) -> tuple[list[MissingCard], list[BoxedCard], list[MissingCard]]:
    """Split a list of card requirements into missing, boxed, and available cards.

    card_needs: list of (canonical_name, needed_qty, variant_count_for_this_card)
    total_variants: total number of deck variants being compared (for MissingCard.total_variants)

    Returns (missing, boxed, available):
      missing:   need to order (owned < needed)
      boxed:     owned but allocated to a box (available < needed)
      available: owned and ready to use (short=0)
    """
    missing: list[MissingCard] = []
    boxed: list[BoxedCard] = []
    available: list[MissingCard] = []
    for name, needed, variants in card_needs:
        owned = get_owned_quantity(conn, name)
        allocs = get_card_allocations(conn, name)
        avail_qty = owned - sum(a.quantity for a in allocs)
        if owned < needed:
            missing.append(MissingCard(
                name=name, needed=needed, owned=owned,
                short=needed - owned, variants=variants,
                total_variants=total_variants,
            ))
        elif avail_qty < needed and allocs:
            boxed.append(BoxedCard(name=name, needed=needed, owned=owned, allocations=allocs))
        else:
            available.append(MissingCard(
                name=name, needed=needed, owned=owned,
                short=0, variants=variants,
                total_variants=total_variants,
            ))
    return missing, boxed, available
