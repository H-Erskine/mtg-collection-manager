import json

from mtg_manager.config import Config
from mtg_manager.db import get_conn, get_meta_decks, replace_meta_decks, upsert_cards
from mtg_manager.models import DeckCard, Decklist, OwnedCard
from web.export_meta import export_meta_static


def _cfg(tmp_path, web_static_dir=None):
    return Config(
        packages=[],
        moxfield_delay=0.0,
        mtgtop8_delay=0.0,
        mtgtop8_cache_ttl=0,
        db_path=tmp_path / "test.db",
        web_static_dir=web_static_dir,
    )


def _decklist(deck_id="1", name="Mono Red", meta_share=12.5):
    return Decklist(
        deck_id=deck_id,
        name=name,
        url=f"https://www.mtggoldfish.com/deck/{deck_id}",
        meta_share=meta_share,
        cards=[
            DeckCard(name="Lightning Bolt", quantity=4, is_sideboard=False),
            DeckCard(name="Goblin Guide", quantity=4, is_sideboard=False),
            DeckCard(name="Pyroblast", quantity=2, is_sideboard=True),
        ],
    )


def test_replace_and_get_meta_decks_round_trips(tmp_path):
    cfg = _cfg(tmp_path)
    with get_conn(cfg.db_path) as conn:
        replace_meta_decks(conn, "modern", [_decklist()])
        decklists = get_meta_decks(conn, "modern")

    assert len(decklists) == 1
    dl = decklists[0]
    assert dl.name == "Mono Red"
    assert dl.meta_share == 12.5
    assert {c.name for c in dl.cards} == {"Lightning Bolt", "Goblin Guide", "Pyroblast"}
    assert dl.sideboard[0].name == "Pyroblast"


def test_replace_meta_decks_clears_previous_set(tmp_path):
    cfg = _cfg(tmp_path)
    with get_conn(cfg.db_path) as conn:
        replace_meta_decks(conn, "modern", [_decklist(deck_id="1", name="Mono Red")])
        replace_meta_decks(conn, "modern", [_decklist(deck_id="2", name="Burn")])
        decklists = get_meta_decks(conn, "modern")

    assert [dl.name for dl in decklists] == ["Burn"]


def test_replace_meta_decks_is_scoped_per_format(tmp_path):
    cfg = _cfg(tmp_path)
    with get_conn(cfg.db_path) as conn:
        replace_meta_decks(conn, "modern", [_decklist(deck_id="1", name="Mono Red")])
        replace_meta_decks(conn, "standard", [_decklist(deck_id="2", name="Mono White")])

        assert [dl.name for dl in get_meta_decks(conn, "modern")] == ["Mono Red"]
        assert [dl.name for dl in get_meta_decks(conn, "standard")] == ["Mono White"]


def test_get_meta_decks_orders_by_meta_share_desc(tmp_path):
    cfg = _cfg(tmp_path)
    with get_conn(cfg.db_path) as conn:
        replace_meta_decks(conn, "modern", [
            _decklist(deck_id="1", name="Low Share", meta_share=2.0),
            _decklist(deck_id="2", name="High Share", meta_share=20.0),
        ])
        decklists = get_meta_decks(conn, "modern")

    assert [dl.name for dl in decklists] == ["High Share", "Low Share"]


def test_export_meta_static_writes_meta_json_from_saved_decks(tmp_path):
    cfg = _cfg(tmp_path, web_static_dir=tmp_path / "static")
    with get_conn(cfg.db_path) as conn:
        upsert_cards(conn, [
            OwnedCard(name="Lightning Bolt", quantity=4, color_group="Red",
                      set_code="lea", collector_number="161"),
        ])
        replace_meta_decks(conn, "modern", [_decklist()])

    # Pre-populate the price cache so the export doesn't hit Scryfall over the network.
    cfg.web_static_dir.mkdir(parents=True, exist_ok=True)
    (cfg.web_static_dir / "prices_cache.json").write_text(
        json.dumps({"goblin guide": 1.5}), encoding="utf-8"
    )

    export_meta_static(cfg, ["modern"])

    data = json.loads((cfg.web_static_dir / "meta.json").read_text(encoding="utf-8"))
    fmt = data["formats"][0]
    assert fmt["format"] == "modern"
    deck = fmt["decks"][0]
    assert deck["name"] == "Mono Red"

    bolt = next(c for c in deck["cards"] if c["name"] == "Lightning Bolt")
    assert bolt["owned"] == 4
    goblin_guide = next(c for c in deck["cards"] if c["name"] == "Goblin Guide")
    assert goblin_guide["owned"] == 0
    assert goblin_guide["eur_price"] == 1.5


def test_export_meta_static_no_op_when_web_static_dir_none(tmp_path):
    cfg = _cfg(tmp_path, web_static_dir=None)
    export_meta_static(cfg, ["modern"])  # should not raise
