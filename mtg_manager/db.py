import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .models import OwnedCard


SCHEMA = """
CREATE TABLE IF NOT EXISTS owned_cards (
    name                TEXT NOT NULL,
    set_code            TEXT NOT NULL DEFAULT '',
    collector_number    TEXT NOT NULL DEFAULT '',
    color_group         TEXT NOT NULL DEFAULT '',
    foil                INTEGER NOT NULL DEFAULT 0,
    quantity            INTEGER NOT NULL DEFAULT 0,
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
        INSERT INTO owned_cards (name, set_code, collector_number, color_group, foil, quantity)
        VALUES (:name, :set_code, :collector_number, :color_group, :foil, :quantity)
        ON CONFLICT (name, set_code, collector_number, foil)
        DO UPDATE SET quantity = excluded.quantity,
                      color_group = excluded.color_group
        """,
        [
            {
                "name": c.name,
                "set_code": c.set_code,
                "collector_number": c.collector_number,
                "color_group": c.color_group,
                "foil": int(c.foil),
                "quantity": c.quantity,
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


BASIC_LANDS = {"forest", "island", "mountain", "plains", "swamp"}


def get_cards_over_limit(conn: sqlite3.Connection, limit: int = 4) -> list[sqlite3.Row]:
    """Return all versions of cards whose total quantity exceeds `limit`, excluding basic lands.
    Rows are ordered by name then quantity DESC so the largest version comes first."""
    return conn.execute(
        """
        SELECT name, set_code, foil, quantity, color_group
        FROM owned_cards
        WHERE LOWER(name) NOT IN ('forest','island','mountain','plains','swamp')
          AND LOWER(name) IN (
            SELECT LOWER(name) FROM owned_cards
            GROUP BY LOWER(name)
            HAVING SUM(quantity) > ?
          )
        ORDER BY LOWER(name), quantity DESC
        """,
        (limit,),
    ).fetchall()


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
