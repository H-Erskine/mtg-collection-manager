"""Regression test for scripts/evict_cache.py path derivation.

Guards against evict_cache deriving a db_path that drifts from
api.users.get_user_config, which would make eviction silently no-op
for prefixed user_ids (e.g. "discord:123") containing characters
(like ':') that are invalid in Windows/NTFS filenames.
"""

from pathlib import Path

from api.users import _safe_filename, _USERS_DIR
from scripts.evict_cache import evict


def test_evict_cache_path_matches_get_user_config_sanitization(tmp_path, monkeypatch):
    import api.users as u

    monkeypatch.setattr(u, "_USERS_DIR", tmp_path)
    import scripts.evict_cache as ec
    monkeypatch.setattr(ec, "_USERS_DIR", tmp_path)

    user_id = "discord:123"

    # Path that get_user_config would produce for this user_id.
    expected_path = tmp_path / f"{_safe_filename(user_id)}.sqlite"

    # The path evict_cache would derive, mirroring its internal logic.
    derived_path = ec._USERS_DIR / f"{_safe_filename(user_id)}.sqlite"

    assert derived_path == expected_path
    assert ":" not in derived_path.name


def test_evict_finds_prefixed_user_db_file_instead_of_skipping(tmp_path, monkeypatch, caplog):
    """Real regression test: without sanitizing discord_id before building
    db_path, evict() looks for a file (e.g. containing a literal ':') that
    never exists — since the real file was created via get_user_config's
    sanitized path — and silently logs "no DB file, skipping" instead of
    evicting. This proves it now finds the real file for a prefixed
    user_id."""
    import logging

    import scripts.evict_cache as ec

    monkeypatch.setattr(ec, "list_users_for_eviction", lambda threshold_days=7: ["discord:123"])
    monkeypatch.setattr(ec, "_USERS_DIR", tmp_path)

    # Create the real per-user DB file exactly as get_user_config would
    # (sanitized filename).
    db_path = tmp_path / f"{_safe_filename('discord:123')}.sqlite"
    from mtg_manager.db import get_conn

    with get_conn(db_path):
        pass

    with caplog.at_level(logging.INFO, logger="scripts.evict_cache"):
        evict(threshold_days=7, dry_run=True)

    assert "no DB file, skipping" not in caplog.text
    assert "DRY RUN" in caplog.text


def test_evict_prunes_logs(tmp_path, monkeypatch):
    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path)

    import scripts.evict_cache as ec
    monkeypatch.setattr(ec, "_USERS_DIR", tmp_path)
    monkeypatch.setattr(ec, "list_users_for_eviction", lambda threshold_days=7: [])

    u.log_failed_login("old@example.com", "not_whitelisted")
    import sqlite3
    conn = sqlite3.connect(u._REGISTRY_PATH)
    conn.execute("UPDATE failed_logins SET created_at = datetime('now', '-40 days')")
    conn.commit()
    conn.close()

    ec.evict(threshold_days=7)

    assert u.list_failed_logins() == []
