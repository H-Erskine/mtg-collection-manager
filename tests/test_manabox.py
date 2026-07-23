import pytest

from mtg_manager.manabox import ManaboxImportError, parse_manabox_csv, import_manabox_csv
from mtg_manager.models import OwnedCard

HEADER = "Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID,Purchase price,Misprint,Altered,Condition,Language,Purchase price currency,Added"


def _row(name="Eldritch Evolution", set_code="inr", collector_number="195", foil="normal", quantity="1", scryfall_id="606caf13-c0d3-4a61-9a1a-32f13b6448ab"):
    return f"{name},{set_code},Innistrad Remastered,{collector_number},{foil},rare,{quantity},102565,{scryfall_id},1.85,false,false,near_mint,en,EUR,2026-07-23T11:28:00.035386Z"


def test_parse_valid_csv_returns_rows():
    csv_text = HEADER + "\n" + _row() + "\n"

    rows = parse_manabox_csv(csv_text)

    assert len(rows) == 1
    row = rows[0]
    assert row.name == "Eldritch Evolution"
    assert row.set_code == "inr"
    assert row.collector_number == "195"
    assert row.foil is False
    assert row.quantity == 1
    assert row.scryfall_id == "606caf13-c0d3-4a61-9a1a-32f13b6448ab"


def test_parse_ignores_unused_columns():
    # Purchase price / rarity / condition etc. must not surface on ManaboxRow at all.
    csv_text = HEADER + "\n" + _row() + "\n"

    rows = parse_manabox_csv(csv_text)

    assert not hasattr(rows[0], "purchase_price")
    assert not hasattr(rows[0], "rarity")
    assert not hasattr(rows[0], "condition")


def test_parse_foil_value_sets_foil_true():
    csv_text = HEADER + "\n" + _row(foil="foil") + "\n"

    rows = parse_manabox_csv(csv_text)

    assert rows[0].foil is True


def test_parse_etched_value_sets_foil_true():
    csv_text = HEADER + "\n" + _row(foil="etched") + "\n"

    rows = parse_manabox_csv(csv_text)

    assert rows[0].foil is True


def test_parse_multiple_rows():
    csv_text = HEADER + "\n" + _row(name="Card A") + "\n" + _row(name="Card B", scryfall_id="other-id") + "\n"

    rows = parse_manabox_csv(csv_text)

    assert [r.name for r in rows] == ["Card A", "Card B"]


def test_parse_missing_required_column_raises():
    csv_text = "Name,Quantity\nBrainstorm,1\n"

    with pytest.raises(ManaboxImportError, match="missing required column"):
        parse_manabox_csv(csv_text)


def test_parse_empty_name_raises():
    csv_text = HEADER + "\n" + _row(name="") + "\n"

    with pytest.raises(ManaboxImportError, match="Name is required"):
        parse_manabox_csv(csv_text)


def test_parse_non_integer_quantity_raises():
    csv_text = HEADER + "\n" + _row(quantity="abc") + "\n"

    with pytest.raises(ManaboxImportError, match="Quantity must be an integer"):
        parse_manabox_csv(csv_text)


def test_parse_zero_quantity_raises():
    csv_text = HEADER + "\n" + _row(quantity="0") + "\n"

    with pytest.raises(ManaboxImportError, match="Quantity must be positive"):
        parse_manabox_csv(csv_text)


def test_parse_negative_quantity_raises():
    csv_text = HEADER + "\n" + _row(quantity="-1") + "\n"

    with pytest.raises(ManaboxImportError, match="Quantity must be positive"):
        parse_manabox_csv(csv_text)


def test_parse_no_header_raises():
    with pytest.raises(ManaboxImportError, match="no header row"):
        parse_manabox_csv("")


def test_parse_oversized_file_raises():
    csv_text = HEADER + "\n" + _row() + "\n"
    with pytest.raises(ManaboxImportError, match="exceeds maximum size"):
        parse_manabox_csv(csv_text, max_bytes=10)


def test_parse_too_many_rows_raises():
    csv_text = HEADER + "\n" + "".join(_row(scryfall_id=f"id-{i}") + "\n" for i in range(5))
    with pytest.raises(ManaboxImportError, match="exceeds maximum of"):
        parse_manabox_csv(csv_text, max_rows=3)


def test_import_manabox_csv_builds_owned_cards_with_manabox_color_group():
    csv_text = HEADER + "\n" + _row(name="Eldritch Evolution", set_code="inr", collector_number="195", quantity="2", scryfall_id="scry-1") + "\n"

    def fake_resolve_cmc(scryfall_ids):
        assert scryfall_ids == ["scry-1"]
        return {"scry-1": 3.0}

    cards = import_manabox_csv(csv_text, resolve_cmc_fn=fake_resolve_cmc)

    assert cards == [
        OwnedCard(
            name="Eldritch Evolution",
            quantity=2,
            color_group="manabox",
            set_code="inr",
            collector_number="195",
            foil=False,
            cmc=3.0,
        )
    ]


def test_import_manabox_csv_defaults_cmc_to_zero_when_unresolved():
    csv_text = HEADER + "\n" + _row(scryfall_id="unknown-id") + "\n"

    cards = import_manabox_csv(csv_text, resolve_cmc_fn=lambda ids: {})

    assert cards[0].cmc == 0.0


def test_import_manabox_csv_propagates_validation_errors():
    csv_text = HEADER + "\n" + _row(quantity="0") + "\n"

    with pytest.raises(ManaboxImportError, match="Quantity must be positive"):
        import_manabox_csv(csv_text, resolve_cmc_fn=lambda ids: {})
