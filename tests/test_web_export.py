import json
from pathlib import Path

import pytest

from mtg_manager.config import Config, load_config
from mtg_manager.db import (
    get_conn, insert_built_deck, upsert_cards,
    upsert_wants_cards, clear_wants_cards, list_wants_cards,
    upsert_for_sale_cards,
)
from mtg_manager.models import OwnedCard
from web.export import export_static


def _cfg(tmp_path, web_static_dir=None):
    return Config(
        packages=[],
        moxfield_delay=0.0,
        mtgtop8_delay=0.0,
        mtgtop8_cache_ttl=0,
        db_path=tmp_path / "test.db",
        web_static_dir=web_static_dir,
    )


def test_config_web_static_dir_optional(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text(
        "[moxfield]\npackages = []\nrequest_delay_seconds = 1.0\n"
        "[mtgtop8]\nrequest_delay_seconds = 1.5\ncache_ttl_hours = 24\n"
        "[database]\npath = '/tmp/test.db'\n"
    )
    cfg = load_config(toml)
    assert cfg.web_static_dir is None


def test_config_web_static_dir_loaded(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text(
        "[moxfield]\npackages = []\nrequest_delay_seconds = 1.0\n"
        "[mtgtop8]\nrequest_delay_seconds = 1.5\ncache_ttl_hours = 24\n"
        "[database]\npath = '/tmp/test.db'\n"
        "[web]\nstatic_dir = '/var/www/mtg'\n"
    )
    cfg = load_config(toml)
    assert cfg.web_static_dir == Path("/var/www/mtg")


def test_export_no_op_when_web_static_dir_none(tmp_path):
    cfg = _cfg(tmp_path, web_static_dir=None)
    export_static(cfg)
    assert not (tmp_path / "collection.json").exists()
    assert not (tmp_path / "decks.json").exists()


def test_export_creates_json_files(tmp_path):
    cfg = _cfg(tmp_path, web_static_dir=tmp_path / "static")
    export_static(cfg)
    assert (cfg.web_static_dir / "collection.json").exists()
    assert (cfg.web_static_dir / "decks.json").exists()


def test_collection_json_contents(tmp_path):
    cfg = _cfg(tmp_path, web_static_dir=tmp_path / "static")
    with get_conn(cfg.db_path) as conn:
        upsert_cards(conn, [OwnedCard(
            name="Lightning Bolt",
            set_code="m10",
            collector_number="146",
            color_group="red",
            foil=False,
            quantity=4,
        )])

    export_static(cfg)

    data = json.loads((cfg.web_static_dir / "collection.json").read_text(encoding="utf-8"))
    assert "updated_at" in data
    assert len(data["cards"]) == 1
    card = data["cards"][0]
    assert card["name"] == "Lightning Bolt"
    assert card["set_code"] == "m10"
    assert card["collector_number"] == "146"
    assert card["foil"] is False
    assert card["quantity"] == 4
    assert card["color_group"] == "red"


def test_decks_json_includes_printing(tmp_path):
    cfg = _cfg(tmp_path, web_static_dir=tmp_path / "static")
    with get_conn(cfg.db_path) as conn:
        upsert_cards(conn, [OwnedCard(
            name="Lightning Bolt",
            set_code="m10",
            collector_number="146",
            color_group="red",
            foil=False,
            quantity=4,
        )])
        insert_built_deck(
            conn,
            deck_id="deck1",
            deck_name="Burn",
            deck_url="https://example.com/burn",
            box_name="Red Box",
            cards=[("Lightning Bolt", 4, False)],
        )

    export_static(cfg)

    data = json.loads((cfg.web_static_dir / "decks.json").read_text(encoding="utf-8"))
    assert len(data["decks"]) == 1
    deck = data["decks"][0]
    assert deck["deck_name"] == "Burn"
    assert deck["box_name"] == "Red Box"
    assert deck["deck_url"] == "https://example.com/burn"
    card = deck["cards"][0]
    assert card["name"] == "Lightning Bolt"
    assert card["quantity"] == 4
    assert card["is_proxy"] is False
    assert card["set_code"] == "m10"
    assert card["collector_number"] == "146"


def test_decks_json_null_printing_for_proxy(tmp_path):
    """Pure proxy cards not in owned_cards get null set_code/collector_number."""
    cfg = _cfg(tmp_path, web_static_dir=tmp_path / "static")
    with get_conn(cfg.db_path) as conn:
        insert_built_deck(
            conn,
            deck_id="deck1",
            deck_name="Burn",
            deck_url="https://example.com/burn",
            box_name="Red Box",
            cards=[("Lightning Bolt", 4, True)],
        )

    export_static(cfg)

    data = json.loads((cfg.web_static_dir / "decks.json").read_text(encoding="utf-8"))
    card = data["decks"][0]["cards"][0]
    assert card["set_code"] is None
    assert card["collector_number"] is None
    assert card["is_proxy"] is True


def test_dfc_name_resolution(tmp_path):
    """'Front // Back' in owned_cards must resolve when allocated_cards has only 'Front'."""
    cfg = _cfg(tmp_path, web_static_dir=tmp_path / "static")
    with get_conn(cfg.db_path) as conn:
        upsert_cards(conn, [OwnedCard(
            name="Ral, Monsoon Mage // Ral, Leyline Prodigy",
            set_code="dsk",
            collector_number="213",
            color_group="blue",
            foil=False,
            quantity=1,
        )])
        insert_built_deck(
            conn,
            deck_id="deck1",
            deck_name="Control",
            deck_url="https://example.com/control",
            box_name="Blue Box",
            cards=[("Ral, Monsoon Mage", 1, False)],
        )

    export_static(cfg)

    data = json.loads((cfg.web_static_dir / "decks.json").read_text(encoding="utf-8"))
    card = data["decks"][0]["cards"][0]
    assert card["set_code"] == "dsk"
    assert card["collector_number"] == "213"


def test_dfc_back_face_resolution(tmp_path):
    """'Front // Back' in owned_cards must also resolve when allocated_cards has 'Back'."""
    cfg = _cfg(tmp_path, web_static_dir=tmp_path / "static")
    with get_conn(cfg.db_path) as conn:
        upsert_cards(conn, [OwnedCard(
            name="Ral, Monsoon Mage // Ral, Leyline Prodigy",
            set_code="dsk",
            collector_number="213",
            color_group="blue",
            foil=False,
            quantity=1,
        )])
        insert_built_deck(
            conn,
            deck_id="deck1",
            deck_name="Control",
            deck_url="https://example.com/control",
            box_name="Blue Box",
            cards=[("Ral, Leyline Prodigy", 1, False)],
        )

    export_static(cfg)

    data = json.loads((cfg.web_static_dir / "decks.json").read_text(encoding="utf-8"))
    card = data["decks"][0]["cards"][0]
    assert card["set_code"] == "dsk"
    assert card["collector_number"] == "213"


def test_decks_ordered_by_box_then_name(tmp_path):
    cfg = _cfg(tmp_path, web_static_dir=tmp_path / "static")
    with get_conn(cfg.db_path) as conn:
        insert_built_deck(conn, "d2", "Merfolk", "https://x.com", "Blue Box", [])
        insert_built_deck(conn, "d1", "Burn",    "https://x.com", "Red Box",  [])
        insert_built_deck(conn, "d3", "Goblins", "https://x.com", "Red Box",  [])

    export_static(cfg)

    data = json.loads((cfg.web_static_dir / "decks.json").read_text(encoding="utf-8"))
    names = [d["deck_name"] for d in data["decks"]]
    assert names == ["Merfolk", "Burn", "Goblins"]


def test_wants_cards_round_trip(tmp_path):
    db = tmp_path / "test.db"
    card = OwnedCard(
        name="Force of Will",
        set_code="all",
        collector_number="62",
        color_group="Blue",
        foil=False,
        quantity=1,
    )
    with get_conn(db) as conn:
        upsert_wants_cards(conn, [card])
        rows = list_wants_cards(conn)
    assert len(rows) == 1
    assert rows[0]["name"] == "Force of Will"
    assert rows[0]["color_group"] == "Blue"
    assert rows[0]["foil"] == 0
    assert rows[0]["quantity"] == 1


def test_clear_wants_cards(tmp_path):
    db = tmp_path / "test.db"
    card = OwnedCard(
        name="Force of Will", set_code="all", collector_number="62",
        color_group="Blue", foil=False, quantity=1,
    )
    with get_conn(db) as conn:
        upsert_wants_cards(conn, [card])
        clear_wants_cards(conn)
        rows = list_wants_cards(conn)
    assert rows == []


def test_sale_json_includes_wants(tmp_path):
    cfg = _cfg(tmp_path, web_static_dir=tmp_path / "static")
    card = OwnedCard(
        name="Force of Will", set_code="all", collector_number="62",
        color_group="Blue", foil=False, quantity=1,
    )
    with get_conn(cfg.db_path) as conn:
        upsert_wants_cards(conn, [card])

    export_static(cfg)

    data = json.loads((cfg.web_static_dir / "sale.json").read_text(encoding="utf-8"))
    assert "wants" in data
    assert len(data["wants"]) == 1
    w = data["wants"][0]
    assert w["name"] == "Force of Will"
    assert w["set_code"] == "all"
    assert w["collector_number"] == "62"
    assert w["foil"] is False
    assert w["quantity"] == 1
    assert w["color_group"] == "Blue"


def test_sale_json_wants_empty_when_no_package(tmp_path):
    cfg = _cfg(tmp_path, web_static_dir=tmp_path / "static")
    export_static(cfg)
    data = json.loads((cfg.web_static_dir / "sale.json").read_text(encoding="utf-8"))
    assert data["wants"] == []


def test_sale_json_for_sale_has_color_group(tmp_path):
    cfg = _cfg(tmp_path, web_static_dir=tmp_path / "static")
    card = OwnedCard(
        name="Lightning Bolt", set_code="m10", collector_number="146",
        color_group="Red", foil=False, quantity=1,
    )
    with get_conn(cfg.db_path) as conn:
        upsert_for_sale_cards(conn, [card], price=2.0)

    export_static(cfg)

    data = json.loads((cfg.web_static_dir / "sale.json").read_text(encoding="utf-8"))
    assert len(data["for_sale"]) == 1
    assert data["for_sale"][0]["color_group"] == "Red"


def test_sale_json_extras_has_color_group(tmp_path):
    cfg = _cfg(tmp_path, web_static_dir=tmp_path / "static")
    cards = [
        OwnedCard(
            name="Lightning Bolt", set_code="m10", collector_number="146",
            color_group="Red", foil=False, quantity=8,
        )
    ]
    with get_conn(cfg.db_path) as conn:
        upsert_cards(conn, cards)

    export_static(cfg)

    data = json.loads((cfg.web_static_dir / "sale.json").read_text(encoding="utf-8"))
    assert len(data["extras"]) == 1
    assert data["extras"][0]["color_group"] == "Red"
