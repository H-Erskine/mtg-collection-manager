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


def test_email_not_whitelisted_by_default():
    assert not users_mod.is_whitelisted("nobody@example.com")


def test_add_whitelisted_email():
    users_mod.add_whitelisted_email("alice@example.com")
    assert users_mod.is_whitelisted("alice@example.com")
    assert not users_mod.is_whitelist_admin("alice@example.com")


def test_add_whitelisted_email_case_insensitive():
    users_mod.add_whitelisted_email("Alice@Example.com")
    assert users_mod.is_whitelisted("alice@example.com")


def test_add_whitelisted_email_as_admin():
    users_mod.add_whitelisted_email("boss@example.com", is_admin=True)
    assert users_mod.is_whitelist_admin("boss@example.com")


def test_add_whitelisted_email_upserts_admin_flag():
    users_mod.add_whitelisted_email("carol@example.com", is_admin=False)
    users_mod.add_whitelisted_email("carol@example.com", is_admin=True)
    assert users_mod.is_whitelist_admin("carol@example.com")


def test_seed_owner_whitelist_noop_when_unset(monkeypatch):
    monkeypatch.delenv("OWNER_GOOGLE_EMAIL", raising=False)
    users_mod.seed_owner_whitelist()
    assert not users_mod.is_whitelisted("owner@example.com")


def test_seed_owner_whitelist_adds_admin(monkeypatch):
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "owner@example.com")
    users_mod.seed_owner_whitelist()
    assert users_mod.is_whitelisted("owner@example.com")
    assert users_mod.is_whitelist_admin("owner@example.com")


def test_get_user_config_discord_owner_unchanged(tmp_path, monkeypatch):
    """Discord owner shortcut must stay exactly as before: no registry row needed."""
    monkeypatch.setenv("OWNER_DISCORD_ID", "999")
    toml = tmp_path / "config.toml"
    toml.write_text(
        "[moxfield]\npackages = []\nrequest_delay_seconds = 1.0\n"
        "[mtgtop8]\nrequest_delay_seconds = 1.5\ncache_ttl_hours = 24\n"
        f"[database]\npath = '{(tmp_path / 'owner.db').as_posix()}'\n"
    )
    monkeypatch.setattr(
        "mtg_manager.config.DEFAULT_CONFIG", toml
    )

    # No registry row exists for this user at all — the Discord owner path must not need one.
    cfg = users_mod.get_user_config("discord:999")
    assert cfg is not None
    assert cfg.db_path == tmp_path / "owner.db"


def test_get_user_config_google_owner_uses_registry_packages(tmp_path, monkeypatch):
    """Google owner must read packages/formats/sort from the registry, not config.toml."""
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "owner@example.com")
    toml = tmp_path / "config.toml"
    real_db = tmp_path / "real_collection.db"
    toml.write_text(
        "[moxfield]\n"
        "packages = [{color_group = 'Red', public_id = 'toml-real-package'}]\n"
        "request_delay_seconds = 1.0\n"
        "[mtgtop8]\nrequest_delay_seconds = 1.5\ncache_ttl_hours = 24\n"
        f"[database]\npath = '{real_db.as_posix()}'\n"
    )
    monkeypatch.setattr("mtg_manager.config.DEFAULT_CONFIG", toml)

    users_mod.ensure_user("google:owner@example.com")
    users_mod.add_package("google:owner@example.com", "Blue", "registry-package")

    cfg = users_mod.get_user_config("google:owner@example.com")
    assert cfg is not None
    assert len(cfg.packages) == 1
    assert cfg.packages[0].color_group == "Blue"
    assert cfg.packages[0].public_id == "registry-package"


def test_get_user_config_google_owner_db_path_points_at_real_collection(tmp_path, monkeypatch):
    """The whole point of this fix: sync must write to the REAL collection.db, not a fresh per-user file."""
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "owner@example.com")
    toml = tmp_path / "config.toml"
    real_db = tmp_path / "real_collection.db"
    toml.write_text(
        "[moxfield]\npackages = []\nrequest_delay_seconds = 1.0\n"
        "[mtgtop8]\nrequest_delay_seconds = 1.5\ncache_ttl_hours = 24\n"
        f"[database]\npath = '{real_db.as_posix()}'\n"
    )
    monkeypatch.setattr("mtg_manager.config.DEFAULT_CONFIG", toml)

    users_mod.ensure_user("google:owner@example.com")
    cfg = users_mod.get_user_config("google:owner@example.com")
    assert cfg is not None
    assert cfg.db_path == real_db


def test_get_user_config_google_owner_returns_none_without_registry_row(monkeypatch):
    """Google owner still needs ensure_user() to have run (via login) before config exists — same as any user."""
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "owner@example.com")
    assert users_mod.get_user_config("google:owner@example.com") is None


def test_set_and_get_profile_via_list_profiles():
    users_mod.ensure_user("google:alice@example.com")
    users_mod.set_profile("google:alice@example.com", "Alice", "🐉")

    profiles = users_mod.list_profiles()
    alice = next(p for p in profiles if p["user_id"] == "google:alice@example.com")
    assert alice["display_name"] == "Alice"
    assert alice["icon"] == "🐉"


def test_list_profiles_defaults_to_empty_strings_when_unset():
    users_mod.ensure_user("google:bob@example.com")
    profiles = users_mod.list_profiles()
    bob = next(p for p in profiles if p["user_id"] == "google:bob@example.com")
    assert bob["display_name"] == ""
    assert bob["icon"] == ""


def test_list_profiles_includes_all_registered_users():
    users_mod.ensure_user("google:alice@example.com")
    users_mod.ensure_user("discord:12345")
    profiles = users_mod.list_profiles()
    ids = {p["user_id"] for p in profiles}
    assert "google:alice@example.com" in ids
    assert "discord:12345" in ids


def test_is_onboarded_defaults_false():
    users_mod.ensure_user("google:alice@example.com")
    assert not users_mod.is_onboarded("google:alice@example.com")


def test_mark_onboarded_sets_true():
    users_mod.ensure_user("google:alice@example.com")
    users_mod.mark_onboarded("google:alice@example.com")
    assert users_mod.is_onboarded("google:alice@example.com")


def test_is_onboarded_false_for_unregistered_user():
    assert not users_mod.is_onboarded("google:nobody@example.com")


def test_onboarding_migration_safe_on_fresh_registry(tmp_path, monkeypatch):
    """A brand-new registry (no users table yet) must not fail the onboarded column migration guard."""
    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "fresh_registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "fresh_users")
    u.ensure_user("google:fresh@example.com")  # must not raise
    assert not u.is_onboarded("google:fresh@example.com")


def test_profile_migration_safe_on_fresh_registry(tmp_path, monkeypatch):
    """A brand-new registry (no users table yet) must not fail the display_name/icon migration guard."""
    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "fresh_registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "fresh_users")
    u.ensure_user("google:fresh@example.com")  # must not raise
    assert u.is_registered("google:fresh@example.com")


def test_remove_whitelisted_user_deletes_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(users_mod, "_USERS_DIR", tmp_path / "users")
    users_mod.ensure_user("google:alice@example.com")
    users_mod.add_package("google:alice@example.com", "Red", "abc123")
    users_mod.add_whitelisted_email("alice@example.com")

    # Force creation of alice's per-user db file on disk
    cfg = users_mod.get_user_config("google:alice@example.com")
    import sqlite3
    conn = sqlite3.connect(cfg.db_path)
    conn.close()
    assert cfg.db_path.exists()

    users_mod.remove_whitelisted_user("alice@example.com")

    assert not users_mod.is_registered("google:alice@example.com")
    assert users_mod.list_packages("google:alice@example.com") == []
    assert not users_mod.is_whitelisted("alice@example.com")
    assert not cfg.db_path.exists()


def test_remove_whitelisted_user_refuses_to_remove_owner(monkeypatch):
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "owner@example.com")
    users_mod.ensure_user("google:owner@example.com")
    users_mod.add_whitelisted_email("owner@example.com", is_admin=True)

    with pytest.raises(ValueError):
        users_mod.remove_whitelisted_user("owner@example.com")

    assert users_mod.is_whitelisted("owner@example.com")


def test_remove_whitelisted_user_case_insensitive_owner_guard(monkeypatch):
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "Owner@Example.com")
    with pytest.raises(ValueError):
        users_mod.remove_whitelisted_user("owner@example.com")


def test_remove_whitelisted_user_is_safe_for_unregistered_email():
    """Removing an email that was never whitelisted/registered should not raise."""
    users_mod.remove_whitelisted_user("nobody@example.com")


def test_log_failed_login_and_list():
    users_mod.log_failed_login("Stranger@Example.com", "not_whitelisted")
    rows = users_mod.list_failed_logins()
    assert len(rows) == 1
    assert rows[0]["email"] == "stranger@example.com"
    assert rows[0]["reason"] == "not_whitelisted"


def test_list_failed_logins_respects_limit():
    for i in range(5):
        users_mod.log_failed_login(f"user{i}@example.com", "not_whitelisted")
    rows = users_mod.list_failed_logins(limit=2)
    assert len(rows) == 2


def test_log_request_and_list():
    users_mod.ensure_user("google:alice@example.com")
    users_mod.log_request("google:alice@example.com", "GET", "/api/config", 200)
    rows = users_mod.list_request_log()
    assert len(rows) == 1
    assert rows[0]["method"] == "GET"
    assert rows[0]["path"] == "/api/config"
    assert rows[0]["status"] == 200
    assert rows[0]["user_id"] == "google:alice@example.com"


def test_log_request_allows_null_user_id():
    users_mod.log_request(None, "GET", "/login", 302)
    rows = users_mod.list_request_log()
    assert rows[0]["user_id"] is None


def test_list_request_log_respects_limit():
    for i in range(5):
        users_mod.log_request(None, "GET", f"/path{i}", 200)
    rows = users_mod.list_request_log(limit=2)
    assert len(rows) == 2


def test_list_users_includes_admin_flag():
    users_mod.ensure_user("google:boss@example.com")
    users_mod.add_whitelisted_email("boss@example.com", is_admin=True)
    users_mod.ensure_user("google:alice@example.com")

    rows = {r["user_id"]: r for r in users_mod.list_users()}
    assert rows["google:boss@example.com"]["is_admin"] is True
    assert rows["google:alice@example.com"]["is_admin"] is False


def test_prune_logs_deletes_rows_older_than_cutoff():
    from api.users import _REGISTRY_PATH

    users_mod.log_failed_login("old@example.com", "not_whitelisted")
    users_mod.log_request(None, "GET", "/old", 200)

    conn = sqlite3.connect(_REGISTRY_PATH)
    conn.execute("UPDATE failed_logins SET created_at = datetime('now', '-40 days')")
    conn.execute("UPDATE request_log SET created_at = datetime('now', '-40 days')")
    conn.commit()
    conn.close()

    users_mod.log_failed_login("recent@example.com", "not_whitelisted")
    users_mod.log_request(None, "GET", "/recent", 200)

    users_mod.prune_logs(days=30)

    failed_emails = [r["email"] for r in users_mod.list_failed_logins()]
    assert "old@example.com" not in failed_emails
    assert "recent@example.com" in failed_emails

    paths = [r["path"] for r in users_mod.list_request_log()]
    assert "/old" not in paths
    assert "/recent" in paths


def test_seconds_since_last_auto_sync_none_when_never_synced():
    from api.users import ensure_user, seconds_since_last_auto_sync

    ensure_user("google:alice@example.com")

    assert seconds_since_last_auto_sync("google:alice@example.com") is None


def test_mark_auto_synced_then_seconds_since_is_near_zero():
    from api.users import ensure_user, mark_auto_synced, seconds_since_last_auto_sync

    ensure_user("google:alice@example.com")
    mark_auto_synced("google:alice@example.com")

    seconds = seconds_since_last_auto_sync("google:alice@example.com")

    assert seconds is not None
    assert 0 <= seconds < 5


def test_seconds_since_last_auto_sync_unknown_user_returns_none():
    from api.users import seconds_since_last_auto_sync

    assert seconds_since_last_auto_sync("google:nobody@example.com") is None
