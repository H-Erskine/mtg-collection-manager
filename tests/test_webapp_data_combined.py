from mtg_manager.db import get_conn, upsert_cards
from mtg_manager.models import OwnedCard

import api.users as users_mod
from webapp.data import get_all_collections


def test_get_all_collections_combines_multiple_users(tmp_path, monkeypatch):
    monkeypatch.setattr(users_mod, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(users_mod, "_USERS_DIR", tmp_path / "users")

    users_mod.ensure_user("google:alice@example.com")
    users_mod.set_profile("google:alice@example.com", "Alice", "🐉")
    users_mod.ensure_user("google:bob@example.com")
    users_mod.set_profile("google:bob@example.com", "Bob", "🦊")

    alice_cfg = users_mod.get_user_config("google:alice@example.com")
    with get_conn(alice_cfg.db_path) as conn:
        upsert_cards(conn, [OwnedCard(
            name="Lightning Bolt", set_code="m10", collector_number="146",
            color_group="red", foil=False, quantity=4,
        )])

    bob_cfg = users_mod.get_user_config("google:bob@example.com")
    with get_conn(bob_cfg.db_path) as conn:
        upsert_cards(conn, [OwnedCard(
            name="Counterspell", set_code="mh2", collector_number="269",
            color_group="blue", foil=False, quantity=2,
        )])

    data = get_all_collections()

    people_ids = {p["user_id"] for p in data["people"]}
    assert people_ids == {"google:alice@example.com", "google:bob@example.com"}

    cards_by_name = {c["name"]: c for c in data["cards"]}
    assert cards_by_name["Lightning Bolt"]["owner_user_id"] == "google:alice@example.com"
    assert cards_by_name["Lightning Bolt"]["owner_display_name"] == "Alice"
    assert cards_by_name["Lightning Bolt"]["owner_icon"] == "🐉"
    assert cards_by_name["Counterspell"]["owner_user_id"] == "google:bob@example.com"
    assert cards_by_name["Counterspell"]["owner_display_name"] == "Bob"


def test_get_all_collections_skips_users_with_no_config(tmp_path, monkeypatch):
    """A registry row that get_user_config can't resolve (e.g. no matching data) must not crash the whole request."""
    monkeypatch.setattr(users_mod, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(users_mod, "_USERS_DIR", tmp_path / "users")
    users_mod.ensure_user("google:alice@example.com")

    data = get_all_collections()  # alice has no owned cards yet, should just return empty for her
    assert any(p["user_id"] == "google:alice@example.com" for p in data["people"])
    assert data["cards"] == []
