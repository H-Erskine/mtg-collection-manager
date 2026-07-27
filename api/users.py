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
    last_synced_at TEXT,
    onboarded      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS user_packages (
    user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    color_group TEXT NOT NULL,
    public_id   TEXT NOT NULL,
    PRIMARY KEY (user_id, color_group)
);
CREATE TABLE IF NOT EXISTS friend_groups (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id  TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS friend_group_members (
    group_id       INTEGER NOT NULL REFERENCES friend_groups(id) ON DELETE CASCADE,
    member_user_id TEXT NOT NULL,
    added_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (group_id, member_user_id)
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


def _migrate_onboarding_column(conn: sqlite3.Connection) -> None:
    """One-time addition of the onboarded column for pre-existing registries."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "users" not in tables:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "onboarded" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN onboarded INTEGER NOT NULL DEFAULT 0")


def _migrate_auto_sync_column(conn: sqlite3.Connection) -> None:
    """One-time addition of the last_auto_synced_at column for pre-existing registries."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "users" not in tables:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "last_auto_synced_at" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN last_auto_synced_at TEXT")


def _migrate_privacy_column(conn: sqlite3.Connection) -> None:
    """One-time addition of the is_private column for pre-existing registries."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "users" not in tables:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "is_private" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_private INTEGER NOT NULL DEFAULT 0")


def _migrate_named_groups(conn: sqlite3.Connection) -> None:
    """One-time migration from the old single-flat-group schema (user_groups)
    to named groups (friend_groups / friend_group_members). Each owner's
    existing members are folded into one auto-created group called 'My Group',
    then the old table is dropped."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "user_groups" not in tables:
        return

    owners = [r[0] for r in conn.execute(
        "SELECT DISTINCT owner_user_id FROM user_groups"
    ).fetchall()]

    for owner_user_id in owners:
        cur = conn.execute(
            "INSERT INTO friend_groups (owner_user_id, name) VALUES (?, 'My Group')",
            (owner_user_id,),
        )
        group_id = cur.lastrowid
        members = conn.execute(
            "SELECT member_user_id, added_at FROM user_groups WHERE owner_user_id = ?",
            (owner_user_id,),
        ).fetchall()
        conn.executemany(
            "INSERT INTO friend_group_members (group_id, member_user_id, added_at) VALUES (?, ?, ?)",
            [(group_id, m[0], m[1]) for m in members],
        )

    conn.execute("DROP TABLE user_groups")


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
        _migrate_onboarding_column(conn)
        _migrate_auto_sync_column(conn)
        _migrate_privacy_column(conn)
        _migrate_named_groups(conn)
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


def get_owner_config() -> Config | None:
    """Resolve the site owner's Config directly, with no session involved.

    Used to serve the public (logged-out) collection/sale views.
    """
    owner_discord_id = os.environ.get("OWNER_DISCORD_ID")
    if owner_discord_id is not None:
        return get_user_config(f"discord:{owner_discord_id}")
    owner_google_email = os.environ.get("OWNER_GOOGLE_EMAIL")
    if owner_google_email is not None:
        return get_user_config(f"google:{owner_google_email}")
    return None


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


def _owns_group(conn: sqlite3.Connection, owner_user_id: str, group_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM friend_groups WHERE id = ? AND owner_user_id = ?",
        (group_id, owner_user_id),
    ).fetchone()
    return row is not None


def create_group(owner_user_id: str, name: str) -> int:
    """Create a new named friend group for owner_user_id. Returns the new group's id."""
    name = name.strip()
    if not name:
        raise ValueError("Group name cannot be empty.")
    with _registry_conn() as conn:
        cur = conn.execute(
            "INSERT INTO friend_groups (owner_user_id, name) VALUES (?, ?)",
            (owner_user_id, name),
        )
        return cur.lastrowid


def rename_group(owner_user_id: str, group_id: int, name: str) -> bool:
    """Rename a group owned by owner_user_id. Returns True if renamed."""
    name = name.strip()
    if not name:
        raise ValueError("Group name cannot be empty.")
    with _registry_conn() as conn:
        if not _owns_group(conn, owner_user_id, group_id):
            return False
        conn.execute("UPDATE friend_groups SET name = ? WHERE id = ?", (name, group_id))
        return True


def delete_group(owner_user_id: str, group_id: int) -> bool:
    """Delete a group owned by owner_user_id (members cascade). Returns True if deleted."""
    with _registry_conn() as conn:
        if not _owns_group(conn, owner_user_id, group_id):
            return False
        conn.execute("DELETE FROM friend_groups WHERE id = ?", (group_id,))
        return True


def add_group_member(owner_user_id: str, group_id: int, member_user_id: str) -> bool:
    """Add another user to one of owner_user_id's groups. One-directional and
    idempotent -- does not require member_user_id's consent and does not
    affect member_user_id's own groups. Returns True if the group is owned
    by owner_user_id (regardless of whether the member was already present)."""
    with _registry_conn() as conn:
        if not _owns_group(conn, owner_user_id, group_id):
            return False
        conn.execute(
            """
            INSERT INTO friend_group_members (group_id, member_user_id)
            VALUES (?, ?)
            ON CONFLICT (group_id, member_user_id) DO NOTHING
            """,
            (group_id, member_user_id),
        )
        return True


def remove_group_member(owner_user_id: str, group_id: int, member_user_id: str) -> bool:
    """Remove a member from one of owner_user_id's groups. Returns True if a row was deleted."""
    with _registry_conn() as conn:
        if not _owns_group(conn, owner_user_id, group_id):
            return False
        conn.execute(
            "DELETE FROM friend_group_members WHERE group_id = ? AND member_user_id = ?",
            (group_id, member_user_id),
        )
        return conn.total_changes > 0


def _list_group_members(conn: sqlite3.Connection, group_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT u.user_id, u.display_name, u.icon
        FROM friend_group_members m
        JOIN users u ON u.user_id = m.member_user_id
        WHERE m.group_id = ?
        ORDER BY u.user_id
        """,
        (group_id,),
    ).fetchall()
    return [_display_profile(dict(r)) for r in rows]


def list_group_members(group_id: int) -> list[dict]:
    """Return [{"user_id", "display_name", "icon"}, ...] for a single group, by id."""
    with _registry_conn() as conn:
        return _list_group_members(conn, group_id)


def list_groups(owner_user_id: str) -> list[dict]:
    """Return [{"id", "name", "members": [...]}, ...] for all of owner_user_id's groups."""
    with _registry_conn() as conn:
        groups = conn.execute(
            "SELECT id, name FROM friend_groups WHERE owner_user_id = ? ORDER BY name",
            (owner_user_id,),
        ).fetchall()
        return [
            {"id": g["id"], "name": g["name"], "members": _list_group_members(conn, g["id"])}
            for g in groups
        ]


def group_owner(group_id: int) -> str | None:
    """Return the owner_user_id of a group, or None if it doesn't exist."""
    with _registry_conn() as conn:
        row = conn.execute(
            "SELECT owner_user_id FROM friend_groups WHERE id = ?", (group_id,)
        ).fetchone()
        return row["owner_user_id"] if row else None


def all_group_member_ids(owner_user_id: str) -> set[str]:
    """Return the union of member user_ids across all of owner_user_id's groups."""
    with _registry_conn() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT m.member_user_id
            FROM friend_group_members m
            JOIN friend_groups g ON g.id = m.group_id
            WHERE g.owner_user_id = ?
            """,
            (owner_user_id,),
        ).fetchall()
        return {r["member_user_id"] for r in rows}


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


def mark_auto_synced(user_id: str) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "UPDATE users SET last_auto_synced_at = datetime('now') WHERE user_id = ?",
            (user_id,),
        )


def seconds_since_last_auto_sync(user_id: str) -> float | None:
    """Return seconds since the last auto-triggered sync, or None if never auto-synced."""
    with _registry_conn() as conn:
        row = conn.execute(
            """
            SELECT
                CASE
                    WHEN last_auto_synced_at IS NULL THEN NULL
                    ELSE (julianday('now') - julianday(last_auto_synced_at)) * 24 * 60 * 60
                END AS seconds_ago
            FROM users WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return row["seconds_ago"]


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


def is_onboarded(user_id: str) -> bool:
    with _registry_conn() as conn:
        row = conn.execute(
            "SELECT onboarded FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return bool(row and row["onboarded"])


def mark_onboarded(user_id: str) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "UPDATE users SET onboarded = 1 WHERE user_id = ?", (user_id,)
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
        conn.execute("DELETE FROM friend_group_members WHERE member_user_id = ?", (user_id,))

    db_path = _USERS_DIR / f"{_safe_filename(user_id)}.sqlite"
    if db_path.exists():
        db_path.unlink()


def _display_profile(row: dict) -> dict:
    """Apply the same display_name/icon fallback app.html already does client-side
    (p.display_name || p.user_id, p.icon || '🂠'), so every server-side aggregation
    (directory, group listing, group-scoped collections) is consistent."""
    return {
        "user_id": row["user_id"],
        "display_name": row["display_name"] or row["user_id"],
        "icon": row["icon"] or "🂠",
    }


def set_privacy(user_id: str, is_private: bool) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "UPDATE users SET is_private = ? WHERE user_id = ?",
            (int(is_private), user_id),
        )


def is_private(user_id: str) -> bool:
    with _registry_conn() as conn:
        row = conn.execute(
            "SELECT is_private FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return bool(row and row["is_private"])


def list_profiles(viewer_is_admin: bool = False) -> list[dict]:
    """Return every registered user's profile. Private accounts are omitted
    unless the viewer is an admin (see is_private / set_privacy)."""
    with _registry_conn() as conn:
        query = "SELECT user_id, display_name, icon FROM users"
        if not viewer_is_admin:
            query += " WHERE is_private = 0"
        rows = conn.execute(query + " ORDER BY user_id").fetchall()
    return [
        {"user_id": r["user_id"], "display_name": r["display_name"], "icon": r["icon"]}
        for r in rows
    ]


def get_profiles_by_ids(user_ids: set[str]) -> list[dict]:
    """Return profiles for a specific set of user_ids, ignoring privacy --
    for resolving people already in the caller's own group."""
    if not user_ids:
        return []
    with _registry_conn() as conn:
        placeholders = ",".join("?" for _ in user_ids)
        rows = conn.execute(
            f"SELECT user_id, display_name, icon FROM users "
            f"WHERE user_id IN ({placeholders}) ORDER BY user_id",
            tuple(user_ids),
        ).fetchall()
    return [_display_profile(dict(r)) for r in rows]


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


def list_request_log(limit: int = 200, user_id: str | None = None) -> list[dict]:
    with _registry_conn() as conn:
        query = "SELECT user_id, method, path, status, created_at FROM request_log"
        params: list = []
        if user_id is not None:
            query += " WHERE user_id = ?"
            params.append(user_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
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
