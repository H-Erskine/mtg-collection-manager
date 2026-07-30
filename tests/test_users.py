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


def test_load_config_cli_path_unaffected_by_new_package_sections(tmp_path, monkeypatch):
    """The CLI's load_config() must keep working with zero changes to config.toml —
    the new section fields just default to empty lists."""
    from mtg_manager.config import load_config

    toml = tmp_path / "config.toml"
    toml.write_text(
        "[moxfield]\n"
        "packages = [{color_group = 'Red', public_id = 'toml-package'}]\n"
        "request_delay_seconds = 1.0\n"
        "[mtgtop8]\nrequest_delay_seconds = 1.5\ncache_ttl_hours = 24\n"
        f"[database]\npath = '{(tmp_path / 'collection.db').as_posix()}'\n"
    )

    cfg = load_config(toml)

    assert len(cfg.packages) == 1
    assert cfg.sale_packages == []
    assert cfg.wants_packages == []
    assert cfg.deck_packages == []


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
    users_mod.add_package("discord:222", "collection", "Red", "abc123")
    cfg = users_mod.get_user_config("discord:222")
    assert cfg is not None
    assert len(cfg.packages) == 1
    assert cfg.packages[0].color_group == "Red"
    assert cfg.packages[0].public_id == "abc123"


def test_add_and_list_packages():
    users_mod.ensure_user("discord:333")
    users_mod.add_package("discord:333", "collection", "White", "w1")
    users_mod.add_package("discord:333", "collection", "Blue", "u1")
    pkgs = users_mod.list_packages("discord:333")
    color_groups = {p["color_group"] for p in pkgs}
    assert color_groups == {"White", "Blue"}


def test_add_package_allows_duplicate_labels_in_same_section():
    """Any number of packages per section — duplicate labels must not collide or overwrite."""
    users_mod.ensure_user("discord:333")
    id1 = users_mod.add_package("discord:333", "collection", "White", "w1")
    id2 = users_mod.add_package("discord:333", "collection", "White", "w2")
    assert id1 != id2
    pkgs = users_mod.list_packages("discord:333", section="collection")
    assert {p["public_id"] for p in pkgs} == {"w1", "w2"}


def test_add_package_rejects_invalid_section():
    users_mod.ensure_user("discord:333")
    with pytest.raises(ValueError):
        users_mod.add_package("discord:333", "bogus", "White", "w1")


def test_add_sale_package_stores_price():
    users_mod.ensure_user("discord:333")
    users_mod.add_package("discord:333", "sale", "Binder A", "s1", price=5.0)
    pkgs = users_mod.list_packages("discord:333", section="sale")
    assert pkgs[0]["price"] == 5.0


def test_list_packages_filters_by_section():
    users_mod.ensure_user("discord:333")
    users_mod.add_package("discord:333", "collection", "White", "w1")
    users_mod.add_package("discord:333", "sale", "Binder A", "s1", price=5.0)
    users_mod.add_package("discord:333", "wants", "Wants", "n1")
    users_mod.add_package("discord:333", "decks", "Burn", "d1")

    assert len(users_mod.list_packages("discord:333", section="collection")) == 1
    assert len(users_mod.list_packages("discord:333", section="sale")) == 1
    assert len(users_mod.list_packages("discord:333", section="wants")) == 1
    assert len(users_mod.list_packages("discord:333", section="decks")) == 1
    assert len(users_mod.list_packages("discord:333")) == 4


def test_remove_package():
    users_mod.ensure_user("discord:444")
    pkg_id = users_mod.add_package("discord:444", "collection", "Green", "g1")
    removed = users_mod.remove_package("discord:444", pkg_id)
    assert removed
    assert users_mod.list_packages("discord:444") == []


def test_remove_nonexistent_package_returns_false():
    users_mod.ensure_user("discord:444")
    assert not users_mod.remove_package("discord:444", 999999)


def test_get_user_config_splits_packages_by_section():
    users_mod.ensure_user("discord:222")
    users_mod.add_package("discord:222", "collection", "Red", "c1")
    users_mod.add_package("discord:222", "sale", "Binder A", "s1", price=7.5)
    users_mod.add_package("discord:222", "wants", "Wants", "n1")
    users_mod.add_package("discord:222", "decks", "Burn", "d1")

    cfg = users_mod.get_user_config("discord:222")

    assert len(cfg.packages) == 1
    assert cfg.packages[0].color_group == "Red"
    assert len(cfg.sale_packages) == 1
    assert cfg.sale_packages[0].color_group == "Binder A"
    assert cfg.sale_packages[0].price == 7.5
    assert len(cfg.wants_packages) == 1
    assert cfg.wants_packages[0].color_group == "Wants"
    assert len(cfg.deck_packages) == 1
    assert cfg.deck_packages[0].color_group == "Burn"


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
    users_mod.add_package("google:owner@example.com", "collection", "Blue", "registry-package")

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
    users_mod.add_package("google:alice@example.com", "collection", "Red", "abc123")
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


def test_list_request_log_filters_by_user_id():
    users_mod.ensure_user("google:alice@example.com")
    users_mod.ensure_user("google:bob@example.com")
    users_mod.log_request("google:alice@example.com", "GET", "/api/config", 200)
    users_mod.log_request("google:bob@example.com", "GET", "/api/collection", 200)
    users_mod.log_request(None, "GET", "/login", 302)

    rows = users_mod.list_request_log(user_id="google:alice@example.com")

    assert len(rows) == 1
    assert rows[0]["path"] == "/api/config"
    assert rows[0]["user_id"] == "google:alice@example.com"


def test_list_request_log_user_filter_respects_limit():
    users_mod.ensure_user("google:alice@example.com")
    for i in range(5):
        users_mod.log_request("google:alice@example.com", "GET", f"/path{i}", 200)

    rows = users_mod.list_request_log(limit=2, user_id="google:alice@example.com")

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


def test_add_group_member_then_list_group_members():
    from api.users import add_group_member, create_group, ensure_user, list_group_members

    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    group_id = create_group("google:alice@example.com", "Cube Night")
    add_group_member("google:alice@example.com", group_id, "google:bob@example.com")

    members = list_group_members(group_id)

    assert members == [{"user_id": "google:bob@example.com", "display_name": "google:bob@example.com", "icon": "🂠"}]


def test_list_group_members_uses_display_name_and_icon_when_set():
    from api.users import add_group_member, create_group, ensure_user, list_group_members, set_profile

    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    set_profile("google:bob@example.com", "Bob", "🐉")
    group_id = create_group("google:alice@example.com", "Cube Night")
    add_group_member("google:alice@example.com", group_id, "google:bob@example.com")

    members = list_group_members(group_id)

    assert members == [{"user_id": "google:bob@example.com", "display_name": "Bob", "icon": "🐉"}]


def test_list_groups_empty_when_none_created():
    from api.users import ensure_user, list_groups

    ensure_user("google:alice@example.com")

    assert list_groups("google:alice@example.com") == []


def test_add_group_member_is_one_directional():
    from api.users import add_group_member, create_group, ensure_user, list_groups

    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    group_id = create_group("google:alice@example.com", "Cube Night")
    add_group_member("google:alice@example.com", group_id, "google:bob@example.com")

    assert list_groups("google:bob@example.com") == []


def test_remove_group_member_returns_true_when_removed():
    from api.users import add_group_member, create_group, ensure_user, list_group_members, remove_group_member

    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    group_id = create_group("google:alice@example.com", "Cube Night")
    add_group_member("google:alice@example.com", group_id, "google:bob@example.com")

    removed = remove_group_member("google:alice@example.com", group_id, "google:bob@example.com")

    assert removed is True
    assert list_group_members(group_id) == []


def test_remove_group_member_returns_false_when_not_present():
    from api.users import create_group, ensure_user, remove_group_member

    ensure_user("google:alice@example.com")
    group_id = create_group("google:alice@example.com", "Cube Night")

    assert remove_group_member("google:alice@example.com", group_id, "google:nobody@example.com") is False


def test_add_group_member_twice_is_idempotent():
    from api.users import add_group_member, create_group, ensure_user, list_group_members

    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    group_id = create_group("google:alice@example.com", "Cube Night")
    add_group_member("google:alice@example.com", group_id, "google:bob@example.com")
    add_group_member("google:alice@example.com", group_id, "google:bob@example.com")

    assert len(list_group_members(group_id)) == 1


def test_add_group_member_rejects_group_not_owned_by_caller():
    from api.users import add_group_member, create_group, ensure_user, list_group_members

    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    ensure_user("google:mallory@example.com")
    group_id = create_group("google:alice@example.com", "Cube Night")

    ok = add_group_member("google:mallory@example.com", group_id, "google:bob@example.com")

    assert ok is False
    assert list_group_members(group_id) == []


def test_create_multiple_groups_for_same_owner():
    from api.users import create_group, ensure_user, list_groups

    ensure_user("google:alice@example.com")
    create_group("google:alice@example.com", "Cube Night")
    create_group("google:alice@example.com", "Commander Pod")

    names = {g["name"] for g in list_groups("google:alice@example.com")}
    assert names == {"Cube Night", "Commander Pod"}


def test_rename_group():
    from api.users import create_group, ensure_user, list_groups, rename_group

    ensure_user("google:alice@example.com")
    group_id = create_group("google:alice@example.com", "Cube Night")

    assert rename_group("google:alice@example.com", group_id, "Legacy Cube") is True
    assert list_groups("google:alice@example.com")[0]["name"] == "Legacy Cube"


def test_rename_group_rejects_non_owner():
    from api.users import create_group, ensure_user, rename_group

    ensure_user("google:alice@example.com")
    ensure_user("google:mallory@example.com")
    group_id = create_group("google:alice@example.com", "Cube Night")

    assert rename_group("google:mallory@example.com", group_id, "Hacked") is False


def test_delete_group_cascades_members():
    from api.users import add_group_member, create_group, delete_group, ensure_user, list_group_members

    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    group_id = create_group("google:alice@example.com", "Cube Night")
    add_group_member("google:alice@example.com", group_id, "google:bob@example.com")

    assert delete_group("google:alice@example.com", group_id) is True
    assert list_group_members(group_id) == []


def test_all_group_member_ids_unions_across_groups():
    from api.users import add_group_member, all_group_member_ids, create_group, ensure_user

    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    ensure_user("google:carol@example.com")
    g1 = create_group("google:alice@example.com", "Cube Night")
    g2 = create_group("google:alice@example.com", "Commander Pod")
    add_group_member("google:alice@example.com", g1, "google:bob@example.com")
    add_group_member("google:alice@example.com", g2, "google:carol@example.com")

    assert all_group_member_ids("google:alice@example.com") == {
        "google:bob@example.com",
        "google:carol@example.com",
    }


def test_privacy_defaults_to_public_and_can_be_set():
    from api.users import ensure_user, is_private, set_privacy

    ensure_user("google:alice@example.com")
    assert is_private("google:alice@example.com") is False

    set_privacy("google:alice@example.com", True)
    assert is_private("google:alice@example.com") is True


def test_cardmarket_url_defaults_to_none():
    from api.users import ensure_user, get_cardmarket_url

    ensure_user("user1")
    assert get_cardmarket_url("user1") is None


def test_set_and_get_cardmarket_url():
    from api.users import ensure_user, get_cardmarket_url, set_cardmarket_url

    ensure_user("user1")
    set_cardmarket_url("user1", "https://www.cardmarket.com/en/Magic/Users/example")
    assert get_cardmarket_url("user1") == "https://www.cardmarket.com/en/Magic/Users/example"


def test_list_profiles_hides_private_users_unless_viewer_is_admin():
    from api.users import ensure_user, list_profiles, set_privacy

    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    set_privacy("google:bob@example.com", True)

    public_ids = {p["user_id"] for p in list_profiles()}
    admin_ids = {p["user_id"] for p in list_profiles(viewer_is_admin=True)}

    assert public_ids == {"google:alice@example.com"}
    assert admin_ids == {"google:alice@example.com", "google:bob@example.com"}


def test_migrate_package_sections_preserves_all_rows_as_collection(tmp_path, monkeypatch):
    """The old (user_id, color_group)-keyed user_packages table must migrate to
    the new sectioned schema with zero network calls and zero data loss --
    every row lands in section='collection' regardless of its old name/label.
    (An earlier version of this migration made a live Moxfield API call per row
    to reclassify sale/wants packages, which made a crash mid-migration strand
    the original data in user_packages_old with only the new table's schema
    change (already-committed DDL) visible on restart.)"""
    import api.users as u

    registry_path = tmp_path / "registry.sqlite"
    monkeypatch.setattr(u, "_REGISTRY_PATH", registry_path)
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "users")

    # Build a pre-existing old-schema registry file directly (no 'section' column).
    conn = sqlite3.connect(registry_path)
    conn.execute("""
        CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            pick_list_sort TEXT NOT NULL DEFAULT 'colour',
            formats TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_synced_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE user_packages (
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            color_group TEXT NOT NULL,
            public_id   TEXT NOT NULL,
            PRIMARY KEY (user_id, color_group)
        )
    """)
    conn.execute("INSERT INTO users (user_id) VALUES ('discord:1')")
    conn.executemany(
        "INSERT INTO user_packages (user_id, color_group, public_id) VALUES (?, ?, ?)",
        [
            ("discord:1", "White", "pub-white"),
            ("discord:1", "$5", "pub-sale"),
            ("discord:1", "Wants", "pub-wants"),
        ],
    )
    conn.commit()
    conn.close()

    packages = users_mod.list_packages("discord:1")

    assert len(packages) == 3
    assert {p["public_id"] for p in packages} == {"pub-white", "pub-sale", "pub-wants"}
    assert all(p["section"] == "collection" for p in packages)
    assert all(p["price"] is None for p in packages)
