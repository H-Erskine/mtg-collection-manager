"""
Multi-user registry for the Discord bot.

Stores user identity → Moxfield packages + preferences in a small
registry SQLite at ~/mtg_data/registry.sqlite.
Each user's collection lives in ~/mtg_data/users/{user_id}.sqlite.

Users are keyed by a prefixed user_id (e.g. "discord:12345" or
"google:person@example.com") so multiple auth providers can share the
same registry without colliding.

The bot owner (OWNER_DISCORD_ID / OWNER_GOOGLE_EMAIL env vars) is
short-circuited to the existing ~/.mtg_manager/config.toml so their
CLI and bot share one DB.
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
    user_id        TEXT PRIMARY KEY,
    pick_list_sort TEXT NOT NULL DEFAULT 'colour',
    formats        TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_synced_at TEXT
);
CREATE TABLE IF NOT EXISTS user_packages (
    user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    color_group TEXT NOT NULL,
    public_id   TEXT NOT NULL,
    PRIMARY KEY (user_id, color_group)
);
CREATE TABLE IF NOT EXISTS whitelisted_emails (
    email    TEXT PRIMARY KEY,
    is_admin INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS failed_logins (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT NOT NULL,
    reason     TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS request_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT,
    method     TEXT NOT NULL,
    path       TEXT NOT NULL,
    status     INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_request_log_created_at ON request_log(created_at);
CREATE INDEX IF NOT EXISTS idx_failed_logins_created_at ON failed_logins(created_at);
"""

VALID_SORT_OPTIONS = ("colour", "alphabetical", "set", "cmc")


def _safe_filename(user_id: str) -> str:
    """Sanitize a prefixed user_id (e.g. 'discord:123' or 'google:a@b.com') for use as a filename component. Windows disallows ':' in paths."""
    return user_id.replace(":", "_")


def _migrate_legacy_discord_id(conn: sqlite3.Connection) -> None:
    """One-time rename of discord_id -> user_id for pre-existing registries."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    if "users" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "discord_id" in cols and "user_id" not in cols:
            conn.execute("ALTER TABLE users RENAME COLUMN discord_id TO user_id")
            conn.execute(
                "UPDATE users SET user_id = 'discord:' || user_id "
                "WHERE user_id NOT LIKE '%:%'"
            )

    if "user_packages" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(user_packages)").fetchall()}
        if "discord_id" in cols and "user_id" not in cols:
            conn.execute("ALTER TABLE user_packages RENAME COLUMN discord_id TO user_id")
            conn.execute(
                "UPDATE user_packages SET user_id = 'discord:' || user_id "
                "WHERE user_id NOT LIKE '%:%'"
            )


def _migrate_profile_columns(conn: sqlite3.Connection) -> None:
    """One-time addition of display_name/icon columns for pre-existing registries."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "users" not in tables:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "display_name" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
    if "icon" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN icon TEXT NOT NULL DEFAULT ''")


@contextmanager
def _registry_conn():
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_REGISTRY_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        _migrate_legacy_discord_id(conn)
        conn.executescript(REGISTRY_SCHEMA)
        _migrate_profile_columns(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def is_owner(user_id: str) -> bool:
    owner_discord_id = os.environ.get("OWNER_DISCORD_ID")
    if owner_discord_id is not None and user_id == f"discord:{owner_discord_id}":
        return True
    owner_google_email = os.environ.get("OWNER_GOOGLE_EMAIL")
    if owner_google_email is not None and user_id == f"google:{owner_google_email}":
        return True
    return False


def is_registered(user_id: str) -> bool:
    with _registry_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row is not None


def ensure_user(user_id: str) -> None:
    """Create a registry row for the user if one doesn't exist."""
    with _registry_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            (user_id,),
        )


def get_user_config(user_id: str) -> Config | None:
    """Return a Config for this user.

    The Discord owner identity is routed to ~/.mtg_manager/config.toml directly
    (same DB as CLI) and needs no registry row — this is unchanged from before.
    The Google owner identity is registry-backed like any other user (so
    packages/formats/sort are editable via the web self-service config page),
    except its db_path is overridden to the real collection.db from config.toml
    so sync never creates a separate, empty per-user database for the owner.
    Everyone else is built entirely from registry rows.
    """
    owner_discord_id = os.environ.get("OWNER_DISCORD_ID")
    if owner_discord_id is not None and user_id == f"discord:{owner_discord_id}":
        try:
            return load_config()
        except FileNotFoundError:
            return None

    with _registry_conn() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not user:
            return None

        pkg_rows = conn.execute(
            "SELECT color_group, public_id FROM user_packages "
            "WHERE user_id = ? ORDER BY color_group",
            (user_id,),
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
    db_path = _USERS_DIR / f"{_safe_filename(user_id)}.sqlite"

    if is_owner(user_id):
        # Google owner: keep writing to the real, existing collection.db.
        try:
            db_path = load_config().db_path
        except FileNotFoundError:
            pass

    return Config(
        packages=packages,
        moxfield_delay=1.0,
        mtgtop8_delay=1.5,
        mtgtop8_cache_ttl=24,
        db_path=db_path,
        pick_list_sort=user["pick_list_sort"],
        formats=formats,
    )


def add_package(user_id: str, color_group: str, public_id: str) -> None:
    """Add or update a Moxfield package for the user."""
    with _registry_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_packages (user_id, color_group, public_id)
            VALUES (?, ?, ?)
            ON CONFLICT (user_id, color_group)
            DO UPDATE SET public_id = excluded.public_id
            """,
            (user_id, color_group.strip(), public_id.strip()),
        )


def remove_package(user_id: str, color_group: str) -> bool:
    """Remove a package by color_group. Returns True if a row was deleted."""
    with _registry_conn() as conn:
        conn.execute(
            "DELETE FROM user_packages WHERE user_id = ? AND LOWER(color_group) = LOWER(?)",
            (user_id, color_group.strip()),
        )
        return conn.total_changes > 0


def list_packages(user_id: str) -> list[tuple[str, str]]:
    """Return [(color_group, public_id), ...] sorted by color_group."""
    with _registry_conn() as conn:
        rows = conn.execute(
            "SELECT color_group, public_id FROM user_packages "
            "WHERE user_id = ? ORDER BY color_group",
            (user_id,),
        ).fetchall()
        return [(r["color_group"], r["public_id"]) for r in rows]


def set_sort(user_id: str, sort_mode: str) -> None:
    if sort_mode not in VALID_SORT_OPTIONS:
        raise ValueError(
            f"Invalid sort mode '{sort_mode}'. Must be one of: {', '.join(VALID_SORT_OPTIONS)}"
        )
    with _registry_conn() as conn:
        conn.execute(
            "UPDATE users SET pick_list_sort = ? WHERE user_id = ?",
            (sort_mode, user_id),
        )


def set_formats(user_id: str, formats: list[str]) -> None:
    value = ",".join(f.strip().lower() for f in formats if f.strip())
    with _registry_conn() as conn:
        conn.execute(
            "UPDATE users SET formats = ? WHERE user_id = ?",
            (value, user_id),
        )


def mark_seen(user_id: str) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "UPDATE users SET last_seen_at = datetime('now') WHERE user_id = ?",
            (user_id,),
        )


def mark_synced(user_id: str) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "UPDATE users SET last_synced_at = datetime('now'), "
            "last_seen_at = datetime('now') WHERE user_id = ?",
            (user_id,),
        )


def minutes_since_last_sync(user_id: str) -> float | None:
    """Return minutes since last sync, or None if never synced."""
    with _registry_conn() as conn:
        row = conn.execute(
            """
            SELECT
                CASE
                    WHEN last_synced_at IS NULL THEN NULL
                    ELSE (julianday('now') - julianday(last_synced_at)) * 24 * 60
                END AS minutes_ago
            FROM users WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return row["minutes_ago"]


def list_users_for_eviction(threshold_days: int = 7) -> list[str]:
    """Return user_ids whose last_seen_at is older than threshold_days."""
    with _registry_conn() as conn:
        rows = conn.execute(
            "SELECT user_id FROM users "
            "WHERE last_seen_at < datetime('now', ? || ' days')",
            (f"-{threshold_days}",),
        ).fetchall()
        return [r["user_id"] for r in rows]


def is_whitelisted(email: str) -> bool:
    with _registry_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM whitelisted_emails WHERE email = ?", (email.lower(),)
        ).fetchone()
        return row is not None


def add_whitelisted_email(email: str, is_admin: bool = False) -> None:
    with _registry_conn() as conn:
        conn.execute(
            """
            INSERT INTO whitelisted_emails (email, is_admin) VALUES (?, ?)
            ON CONFLICT (email) DO UPDATE SET is_admin = excluded.is_admin
            """,
            (email.lower().strip(), int(is_admin)),
        )


def is_whitelist_admin(email: str) -> bool:
    with _registry_conn() as conn:
        row = conn.execute(
            "SELECT is_admin FROM whitelisted_emails WHERE email = ?", (email.lower(),)
        ).fetchone()
        return bool(row and row["is_admin"])


def seed_owner_whitelist() -> None:
    """Ensure OWNER_GOOGLE_EMAIL (if set) is whitelisted as admin. Safe to call repeatedly."""
    owner_email = os.environ.get("OWNER_GOOGLE_EMAIL")
    if owner_email:
        add_whitelisted_email(owner_email, is_admin=True)


def set_profile(user_id: str, display_name: str, icon: str) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "UPDATE users SET display_name = ?, icon = ? WHERE user_id = ?",
            (display_name.strip(), icon.strip(), user_id),
        )


def remove_whitelisted_user(email: str) -> None:
    """Fully delete a whitelisted user's account: whitelist entry, registry
    row (packages/formats/sort cascade via FK), and their per-user DB file.
    Irreversible. Refuses to remove the owner's own email."""
    email = email.strip().lower()
    owner_email = os.environ.get("OWNER_GOOGLE_EMAIL")
    if owner_email is not None and email == owner_email.lower():
        raise ValueError("Cannot remove the owner's account.")

    user_id = f"google:{email}"

    with _registry_conn() as conn:
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM whitelisted_emails WHERE email = ?", (email,))

    db_path = _USERS_DIR / f"{_safe_filename(user_id)}.sqlite"
    if db_path.exists():
        db_path.unlink()


def list_profiles() -> list[dict]:
    with _registry_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, display_name, icon FROM users ORDER BY user_id"
        ).fetchall()
    return [
        {"user_id": r["user_id"], "display_name": r["display_name"], "icon": r["icon"]}
        for r in rows
    ]


def log_failed_login(email: str, reason: str) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "INSERT INTO failed_logins (email, reason) VALUES (?, ?)",
            (email.lower().strip(), reason),
        )


def log_request(user_id: str | None, method: str, path: str, status: int) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "INSERT INTO request_log (user_id, method, path, status) VALUES (?, ?, ?, ?)",
            (user_id, method, path, status),
        )


def list_users() -> list[dict]:
    with _registry_conn() as conn:
        rows = conn.execute(
            """
            SELECT u.user_id, u.last_seen_at, u.last_synced_at,
                   COALESCE(w.is_admin, 0) AS is_admin
            FROM users u
            LEFT JOIN whitelisted_emails w
                ON u.user_id = 'google:' || w.email
            ORDER BY u.user_id
            """
        ).fetchall()
    return [
        {
            "user_id": r["user_id"],
            "last_seen_at": r["last_seen_at"],
            "last_synced_at": r["last_synced_at"],
            "is_admin": bool(r["is_admin"]),
        }
        for r in rows
    ]


def list_failed_logins(limit: int = 200) -> list[dict]:
    with _registry_conn() as conn:
        rows = conn.execute(
            "SELECT email, reason, created_at FROM failed_logins "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"email": r["email"], "reason": r["reason"], "created_at": r["created_at"]}
        for r in rows
    ]


def list_request_log(limit: int = 200) -> list[dict]:
    with _registry_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, method, path, status, created_at FROM request_log "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "user_id": r["user_id"],
            "method": r["method"],
            "path": r["path"],
            "status": r["status"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def prune_logs(days: int = 30) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "DELETE FROM request_log WHERE created_at < datetime('now', ? || ' days')",
            (f"-{days}",),
        )
        conn.execute(
            "DELETE FROM failed_logins WHERE created_at < datetime('now', ? || ' days')",
            (f"-{days}",),
        )
