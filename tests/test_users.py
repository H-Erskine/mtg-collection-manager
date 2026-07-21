"""Tests for api/users.py registry CRUD."""

import sqlite3

import pytest

# Point registry + user DBs at a temp dir for every test
@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "users")
    # Ensure no owner leak between tests
    monkeypatch.delenv("OWNER_DISCORD_ID", raising=False)
    monkeypatch.delenv("OWNER_GOOGLE_EMAIL", raising=False)
    yield


import api.users as users_mod


def test_ensure_user_creates_row():
    users_mod.ensure_user("discord:111")
    assert users_mod.is_registered("discord:111")


def test_ensure_user_idempotent():
    users_mod.ensure_user("discord:111")
    users_mod.ensure_user("discord:111")  # should not raise
    assert users_mod.is_registered("discord:111")


def test_not_registered_by_default():
    assert not users_mod.is_registered("discord:999")


def test_get_user_config_returns_none_for_unknown():
    assert users_mod.get_user_config("discord:999") is None


def test_get_user_config_returns_config_for_known(tmp_path):
    users_mod.ensure_user("discord:222")
    users_mod.add_package("discord:222", "Red", "abc123")
    cfg = users_mod.get_user_config("discord:222")
    assert cfg is not None
    assert len(cfg.packages) == 1
    assert cfg.packages[0].color_group == "Red"
    assert cfg.packages[0].public_id == "abc123"


def test_add_and_list_packages():
    users_mod.ensure_user("discord:333")
    users_mod.add_package("discord:333", "White", "w1")
    users_mod.add_package("discord:333", "Blue", "u1")
    pkgs = users_mod.list_packages("discord:333")
    assert ("Blue", "u1") in pkgs
    assert ("White", "w1") in pkgs


def test_add_package_upserts():
    users_mod.ensure_user("discord:333")
    users_mod.add_package("discord:333", "White", "w1")
    users_mod.add_package("discord:333", "White", "w2")  # update
    pkgs = dict(users_mod.list_packages("discord:333"))
    assert pkgs["White"] == "w2"


def test_remove_package():
    users_mod.ensure_user("discord:444")
    users_mod.add_package("discord:444", "Green", "g1")
    removed = users_mod.remove_package("discord:444", "Green")
    assert removed
    assert users_mod.list_packages("discord:444") == []


def test_remove_package_case_insensitive():
    users_mod.ensure_user("discord:444")
    users_mod.add_package("discord:444", "Green", "g1")
    assert users_mod.remove_package("discord:444", "green")


def test_remove_nonexistent_package_returns_false():
    users_mod.ensure_user("discord:444")
    assert not users_mod.remove_package("discord:444", "Nonexistent")


def test_set_sort_valid():
    users_mod.ensure_user("discord:555")
    users_mod.set_sort("discord:555", "cmc")
    cfg = users_mod.get_user_config("discord:555")
    assert cfg.pick_list_sort == "cmc"


def test_set_sort_invalid():
    users_mod.ensure_user("discord:555")
    with pytest.raises(ValueError):
        users_mod.set_sort("discord:555", "random")


def test_set_formats():
    users_mod.ensure_user("discord:666")
    users_mod.set_formats("discord:666", ["modern", "legacy"])
    cfg = users_mod.get_user_config("discord:666")
    assert "modern" in cfg.formats
    assert "legacy" in cfg.formats


def test_mark_seen_updates_timestamp():
    users_mod.ensure_user("discord:777")
    users_mod.mark_seen("discord:777")  # should not raise


def test_mark_synced_and_throttle():
    users_mod.ensure_user("discord:888")
    assert users_mod.minutes_since_last_sync("discord:888") is None
    users_mod.mark_synced("discord:888")
    mins = users_mod.minutes_since_last_sync("discord:888")
    assert mins is not None
    assert mins < 1  # just synced


def test_minutes_since_sync_returns_none_for_unknown():
    assert users_mod.minutes_since_last_sync("discord:nobody") is None


def test_list_users_for_eviction(tmp_path):
    users_mod.ensure_user("discord:active")
    users_mod.ensure_user("discord:stale")

    # Backdate stale user's last_seen_at
    from api.users import _REGISTRY_PATH
    conn = sqlite3.connect(_REGISTRY_PATH)
    conn.execute(
        "UPDATE users SET last_seen_at = datetime('now', '-8 days') WHERE user_id = 'discord:stale'"
    )
    conn.commit()
    conn.close()

    evictable = users_mod.list_users_for_eviction(threshold_days=7)
    assert "discord:stale" in evictable
    assert "discord:active" not in evictable


def test_is_owner_matches_discord_env(monkeypatch):
    monkeypatch.setenv("OWNER_DISCORD_ID", "42")
    assert users_mod.is_owner("discord:42")
    assert not users_mod.is_owner("discord:43")


def test_is_owner_matches_google_env(monkeypatch):
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "owner@example.com")
    assert users_mod.is_owner("google:owner@example.com")
    assert not users_mod.is_owner("google:someone-else@example.com")


def test_is_owner_unset(monkeypatch):
    monkeypatch.delenv("OWNER_DISCORD_ID", raising=False)
    monkeypatch.delenv("OWNER_GOOGLE_EMAIL", raising=False)
    assert not users_mod.is_owner("discord:42")


def test_get_user_config_db_path_uses_users_dir(tmp_path):
    users_mod.ensure_user("discord:999")
    cfg = users_mod.get_user_config("discord:999")
    assert cfg is not None
    assert "999" in str(cfg.db_path)


def test_get_user_config_db_path_is_openable_on_disk():
    users_mod.ensure_user("discord:12345")
    cfg = users_mod.get_user_config("discord:12345")
    assert cfg is not None
    conn = sqlite3.connect(cfg.db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
    conn.close()
    assert cfg.db_path.exists()


def test_get_user_config_db_path_openable_for_google_user():
    users_mod.ensure_user("google:alice@example.com")
    cfg = users_mod.get_user_config("google:alice@example.com")
    assert cfg is not None
    conn = sqlite3.connect(cfg.db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
    conn.close()
    assert cfg.db_path.exists()
