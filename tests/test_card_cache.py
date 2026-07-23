import pytest


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    import mtg_manager.card_cache as cc
    monkeypatch.setattr(cc, "_CACHE_PATH", tmp_path / "cards_cache.sqlite")
    yield


def test_get_cached_cmc_empty_when_nothing_cached():
    from mtg_manager.card_cache import get_cached_cmc

    result = get_cached_cmc(["abc-123", "def-456"])

    assert result == {}


def test_cache_printings_then_get_cached_cmc_returns_them():
    from mtg_manager.card_cache import cache_printings, get_cached_cmc

    cache_printings([
        {"scryfall_id": "abc-123", "name": "Lightning Bolt", "set_code": "lea", "collector_number": "161", "cmc": 1.0},
        {"scryfall_id": "def-456", "name": "Counterspell", "set_code": "lea", "collector_number": "54", "cmc": 2.0},
    ])

    result = get_cached_cmc(["abc-123", "def-456", "not-cached"])

    assert result == {"abc-123": 1.0, "def-456": 2.0}


def test_cache_printings_upserts_on_conflict():
    from mtg_manager.card_cache import cache_printings, get_cached_cmc

    cache_printings([{"scryfall_id": "abc-123", "name": "Old Name", "set_code": "lea", "collector_number": "161", "cmc": 1.0}])
    cache_printings([{"scryfall_id": "abc-123", "name": "Old Name", "set_code": "lea", "collector_number": "161", "cmc": 99.0}])

    result = get_cached_cmc(["abc-123"])

    assert result == {"abc-123": 99.0}


def test_get_cached_cmc_empty_list_input_returns_empty_dict():
    from mtg_manager.card_cache import get_cached_cmc

    assert get_cached_cmc([]) == {}


def test_cache_printings_empty_list_is_a_noop():
    from mtg_manager.card_cache import cache_printings

    cache_printings([])  # must not raise
