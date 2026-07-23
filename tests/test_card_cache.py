from unittest.mock import patch

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


def test_resolve_cmc_uses_cache_without_network_call():
    from mtg_manager.card_cache import cache_printings, resolve_cmc

    cache_printings([{"scryfall_id": "abc-123", "name": "Lightning Bolt", "set_code": "lea", "collector_number": "161", "cmc": 1.0}])

    with patch("mtg_manager.card_cache._scryfall_collection_by_id") as mock_fetch:
        result = resolve_cmc(["abc-123"])

    assert result == {"abc-123": 1.0}
    mock_fetch.assert_not_called()


def test_resolve_cmc_fetches_and_caches_misses():
    from mtg_manager.card_cache import get_cached_cmc, resolve_cmc

    fake_cards = [
        {"id": "new-1", "name": "Brainstorm", "set": "ice", "collector_number": "48", "cmc": 1.0},
    ]
    with patch("mtg_manager.card_cache._scryfall_collection_by_id", return_value=fake_cards) as mock_fetch:
        result = resolve_cmc(["new-1"])

    assert result == {"new-1": 1.0}
    mock_fetch.assert_called_once_with(["new-1"])
    # Second call must hit the cache, not the network again.
    with patch("mtg_manager.card_cache._scryfall_collection_by_id") as mock_fetch_2:
        result2 = resolve_cmc(["new-1"])
    assert result2 == {"new-1": 1.0}
    mock_fetch_2.assert_not_called()


def test_resolve_cmc_batches_in_groups_of_75():
    from mtg_manager.card_cache import resolve_cmc

    ids = [f"id-{i}" for i in range(150)]
    with patch("mtg_manager.card_cache._scryfall_collection_by_id", return_value=[]) as mock_fetch, \
         patch("mtg_manager.card_cache.time.sleep"):
        resolve_cmc(ids)

    assert mock_fetch.call_count == 2
    assert len(mock_fetch.call_args_list[0].args[0]) == 75
    assert len(mock_fetch.call_args_list[1].args[0]) == 75


def test_resolve_cmc_ignores_blank_ids_and_deduplicates():
    from mtg_manager.card_cache import resolve_cmc

    with patch("mtg_manager.card_cache._scryfall_collection_by_id", return_value=[]) as mock_fetch:
        resolve_cmc(["dup-1", "", "dup-1", None])

    mock_fetch.assert_called_once_with(["dup-1"])


def test_resolve_cmc_survives_a_failed_batch():
    from mtg_manager.card_cache import resolve_cmc

    with patch("mtg_manager.card_cache._scryfall_collection_by_id", side_effect=RuntimeError("boom")) as mock_fetch:
        result = resolve_cmc(["broken-1", "broken-2"])

    mock_fetch.assert_called_once_with(["broken-1", "broken-2"])
    # A failed batch leaves those ids unresolved rather than raising.
    assert result == {}


def test_resolve_cmc_defaults_missing_cmc_field_to_zero():
    from mtg_manager.card_cache import resolve_cmc

    fake_cards = [{"id": "no-cmc", "name": "Weird Card", "set": "xyz", "collector_number": "1"}]
    with patch("mtg_manager.card_cache._scryfall_collection_by_id", return_value=fake_cards):
        result = resolve_cmc(["no-cmc"])

    assert result == {"no-cmc": 0.0}
