"""
Multi-user registry for the Discord bot.

Stores Discord identity → Moxfield packages + preferences in a small
registry SQLite at ~/mtg_data/registry.sqlite.
Each user's collection lives in ~/mtg_data/users/{discord_id}.sqlite.

The bot owner (OWNER_DISCORD_ID env var) is short-circuited to the
existing ~/.mtg_manager/config.toml so their CLI and bot share one DB.
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from mtg_manager.config import Config, MoxfieldPackage, load_config

_REGISTRY_PATH = Path("~/mtg_data/registry.sqlite").expanduser()
_USERS_DIR = Path("~/mtg_data/users").expanduser()

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    discord_id     TEXT PRIMARY KEY,
    pick_list_sort TEXT NOT NULL DEFAULT 'colour',
    formats        TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_synced_at TEXT
);
CREATE TABLE IF NOT EXISTS user_packages (
    discord_id  TEXT NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE,
    color_group TEXT NOT NULL,
    public_id   TEXT NOT NULL,
    PRIMARY KEY (discord_id, color_group)
);
"""

VALID_SORT_OPTIONS = ("colour", "alphabetical", "set", "cmc")


@contextmanager
def _registry_conn():
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_REGISTRY_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(REGISTRY_SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def is_owner(discord_id: str) -> bool:
    owner_id = os.environ.get("OWNER_DISCORD_ID")
    return owner_id is not None and discord_id == owner_id


def is_registered(discord_id: str) -> bool:
    with _registry_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE discord_id = ?", (discord_id,)
        ).fetchone()
        return row is not None


def ensure_user(discord_id: str) -> None:
    """Create a registry row for the user if one doesn't exist."""
    with _registry_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (discord_id) VALUES (?)",
            (discord_id,),
        )


def get_user_config(discord_id: str) -> Config | None:
    """Return a Config for this user.

    Owner is routed to ~/.mtg_manager/config.toml (same DB as CLI).
    Others are built from registry rows.
    """
    if is_owner(discord_id):
        try:
            return load_config()
        except FileNotFoundError:
            return None

    with _registry_conn() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE discord_id = ?", (discord_id,)
        ).fetchone()
        if not user:
            return None

        pkg_rows = conn.execute(
            "SELECT color_group, public_id FROM user_packages "
            "WHERE discord_id = ? ORDER BY color_group",
            (discord_id,),
        ).fetchall()

    packages = [
        MoxfieldPackage(color_group=r["color_group"], public_id=r["public_id"])
        for r in pkg_rows
    ]
    formats = (
        [f.strip() for f in user["formats"].split(",") if f.strip()]
        if user["formats"]
        else []
    )

    _USERS_DIR.mkdir(parents=True, exist_ok=True)

    return Config(
        packages=packages,
        moxfield_delay=1.0,
        mtgtop8_delay=1.5,
        mtgtop8_cache_ttl=24,
        db_path=_USERS_DIR / f"{discord_id}.sqlite",
        pick_list_sort=user["pick_list_sort"],
        formats=formats,
    )


def add_package(discord_id: str, color_group: str, public_id: str) -> None:
    """Add or update a Moxfield package for the user."""
    with _registry_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_packages (discord_id, color_group, public_id)
            VALUES (?, ?, ?)
            ON CONFLICT (discord_id, color_group)
            DO UPDATE SET public_id = excluded.public_id
            """,
            (discord_id, color_group.strip(), public_id.strip()),
        )


def remove_package(discord_id: str, color_group: str) -> bool:
    """Remove a package by color_group. Returns True if a row was deleted."""
    with _registry_conn() as conn:
        conn.execute(
            "DELETE FROM user_packages WHERE discord_id = ? AND LOWER(color_group) = LOWER(?)",
            (discord_id, color_group.strip()),
        )
        return conn.total_changes > 0


def list_packages(discord_id: str) -> list[tuple[str, str]]:
    """Return [(color_group, public_id), ...] sorted by color_group."""
    with _registry_conn() as conn:
        rows = conn.execute(
            "SELECT color_group, public_id FROM user_packages "
            "WHERE discord_id = ? ORDER BY color_group",
            (discord_id,),
        ).fetchall()
        return [(r["color_group"], r["public_id"]) for r in rows]


def set_sort(discord_id: str, sort_mode: str) -> None:
    if sort_mode not in VALID_SORT_OPTIONS:
        raise ValueError(
            f"Invalid sort mode '{sort_mode}'. Must be one of: {', '.join(VALID_SORT_OPTIONS)}"
        )
    with _registry_conn() as conn:
        conn.execute(
            "UPDATE users SET pick_list_sort = ? WHERE discord_id = ?",
            (sort_mode, discord_id),
        )


def set_formats(discord_id: str, formats: list[str]) -> None:
    value = ",".join(f.strip().lower() for f in formats if f.strip())
    with _registry_conn() as conn:
        conn.execute(
            "UPDATE users SET formats = ? WHERE discord_id = ?",
            (value, discord_id),
        )


def mark_seen(discord_id: str) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "UPDATE users SET last_seen_at = datetime('now') WHERE discord_id = ?",
            (discord_id,),
        )


def mark_synced(discord_id: str) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "UPDATE users SET last_synced_at = datetime('now'), "
            "last_seen_at = datetime('now') WHERE discord_id = ?",
            (discord_id,),
        )


def minutes_since_last_sync(discord_id: str) -> float | None:
    """Return minutes since last sync, or None if never synced."""
    with _registry_conn() as conn:
        row = conn.execute(
            """
            SELECT
                CASE
                    WHEN last_synced_at IS NULL THEN NULL
                    ELSE (julianday('now') - julianday(last_synced_at)) * 24 * 60
                END AS minutes_ago
            FROM users WHERE discord_id = ?
            """,
            (discord_id,),
        ).fetchone()
        if not row:
            return None
        return row["minutes_ago"]


def list_users_for_eviction(threshold_days: int = 7) -> list[str]:
    """Return discord_ids whose last_seen_at is older than threshold_days."""
    with _registry_conn() as conn:
        rows = conn.execute(
            "SELECT discord_id FROM users "
            "WHERE last_seen_at < datetime('now', ? || ' days')",
            (f"-{threshold_days}",),
        ).fetchall()
        return [r["discord_id"] for r in rows]
