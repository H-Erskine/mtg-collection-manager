"""Tests for api.handlers sync semantics across collection/sale/wants sections."""
from unittest.mock import patch

from mtg_manager.config import Config, MoxfieldPackage, SalePackage
from mtg_manager.db import get_conn, list_for_sale_cards, list_wants_cards, get_owned_quantity
from mtg_manager.models import OwnedCard


def _cfg(tmp_path, **package_lists) -> Config:
    return Config(
        packages=package_lists.get("packages", []),
        sale_packages=package_lists.get("sale_packages", []),
        wants_packages=package_lists.get("wants_packages", []),
        deck_packages=package_lists.get("deck_packages", []),
        moxfield_delay=0.0,
        mtgtop8_delay=0.0,
        mtgtop8_cache_ttl=24,
        db_path=tmp_path / "collection.db",
    )


def _card(name="Lightning Bolt", qty=4, color_group="Red") -> OwnedCard:
    return OwnedCard(
        name=name, quantity=qty, color_group=color_group,
        set_code="m10", collector_number="146", foil=False, cmc=1.0,
    )


def test_multiple_sale_packages_do_not_wipe_each_other(tmp_path):
    from api.handlers import handle_sync

    pkg_a = SalePackage(color_group="Binder A", public_id="a1", price=5.0)
    pkg_b = SalePackage(color_group="Binder B", public_id="b1", price=10.0)
    cfg = _cfg(tmp_path, sale_packages=[pkg_a, pkg_b])

    def fake_fetch(pkg, delay=1.0):
        if pkg.public_id == "a1":
            return [_card("Lightning Bolt")], "$5"
        return [_card("Brainstorm", color_group="Blue")], "$10"

    with patch("api.handlers.fetch_package_cards", side_effect=fake_fetch):
        handle_sync(cfg)

    with get_conn(cfg.db_path) as conn:
        rows = list_for_sale_cards(conn)

    names = {r["name"] for r in rows}
    assert names == {"Lightning Bolt", "Brainstorm"}


def test_sale_cards_also_populate_collection(tmp_path):
    from api.handlers import handle_sync

    pkg = SalePackage(color_group="Binder A", public_id="a1", price=5.0)
    cfg = _cfg(tmp_path, sale_packages=[pkg])

    with patch("api.handlers.fetch_package_cards", return_value=([_card("Lightning Bolt")], "$5")):
        handle_sync(cfg)

    with get_conn(cfg.db_path) as conn:
        owned = get_owned_quantity(conn, "Lightning Bolt")
        for_sale = list_for_sale_cards(conn)

    assert owned == 4
    assert for_sale[0]["price"] == 5.0


def test_multiple_wants_packages_do_not_wipe_each_other(tmp_path):
    from api.handlers import handle_sync

    pkg_a = MoxfieldPackage(color_group="Wants A", public_id="wa")
    pkg_b = MoxfieldPackage(color_group="Wants B", public_id="wb")
    cfg = _cfg(tmp_path, wants_packages=[pkg_a, pkg_b])

    def fake_fetch(pkg, delay=1.0):
        if pkg.public_id == "wa":
            return [_card("Lightning Bolt")], "Wants A"
        return [_card("Brainstorm", color_group="Blue")], "Wants B"

    with patch("api.handlers.fetch_package_cards", side_effect=fake_fetch):
        handle_sync(cfg)

    with get_conn(cfg.db_path) as conn:
        rows = list_wants_cards(conn)

    names = {r["name"] for r in rows}
    assert names == {"Lightning Bolt", "Brainstorm"}


def test_wants_cards_do_not_populate_collection(tmp_path):
    from api.handlers import handle_sync

    pkg = MoxfieldPackage(color_group="Wants", public_id="w1")
    cfg = _cfg(tmp_path, wants_packages=[pkg])

    with patch("api.handlers.fetch_package_cards", return_value=([_card("Lightning Bolt")], "Wants")):
        handle_sync(cfg)

    with get_conn(cfg.db_path) as conn:
        owned = get_owned_quantity(conn, "Lightning Bolt")

    assert owned == 0
