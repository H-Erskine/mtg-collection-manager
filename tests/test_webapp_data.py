from mtg_manager.config import Config
from mtg_manager.db import get_conn, upsert_cards, upsert_for_sale_cards
from mtg_manager.models import OwnedCard
from webapp.data import get_collection, get_decks, get_sale


def _cfg(tmp_path) -> Config:
    return Config(
        packages=[],
        moxfield_delay=0.0,
        mtgtop8_delay=0.0,
        mtgtop8_cache_ttl=0,
        db_path=tmp_path / "test.db",
    )


def test_get_collection_returns_live_data(tmp_path):
    cfg = _cfg(tmp_path)
    with get_conn(cfg.db_path) as conn:
        upsert_cards(conn, [OwnedCard(
            name="Lightning Bolt",
            set_code="m10",
            collector_number="146",
            color_group="red",
            foil=False,
            quantity=4,
        )])

    data = get_collection(cfg)
    assert "updated_at" in data
    assert len(data["cards"]) == 1
    assert data["cards"][0]["name"] == "Lightning Bolt"


def test_get_collection_empty_db(tmp_path):
    cfg = _cfg(tmp_path)
    with get_conn(cfg.db_path):
        pass  # just create the schema, no cards
    data = get_collection(cfg)
    assert data["cards"] == []


def test_get_decks_empty_db(tmp_path):
    cfg = _cfg(tmp_path)
    with get_conn(cfg.db_path):
        pass
    data = get_decks(cfg)
    assert data["decks"] == []


def test_get_sale_returns_live_data(tmp_path):
    cfg = _cfg(tmp_path)
    with get_conn(cfg.db_path) as conn:
        upsert_for_sale_cards(conn, [OwnedCard(
            name="Lightning Bolt",
            set_code="m10",
            collector_number="146",
            color_group="red",
            foil=False,
            quantity=1,
        )], price=2.0)

    data = get_sale(cfg)
    assert "updated_at" in data
    assert len(data["for_sale"]) == 1
    assert data["for_sale"][0]["name"] == "Lightning Bolt"
    assert data["extras"] == []
    assert data["wants"] == []


def test_get_sale_empty_db(tmp_path):
    cfg = _cfg(tmp_path)
    with get_conn(cfg.db_path):
        pass
    data = get_sale(cfg)
    assert data["for_sale"] == []
    assert data["extras"] == []
    assert data["wants"] == []
