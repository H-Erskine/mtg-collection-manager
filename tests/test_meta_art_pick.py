import httpx
import pytest
from fastapi.testclient import TestClient

from webapp.data import pick_art_card


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json


@pytest.mark.asyncio
async def test_pick_art_card_prefers_highest_cmc_creature(monkeypatch):
    fake_collection_response = {
        "data": [
            {"name": "Mountain", "type_line": "Basic Land — Mountain", "cmc": 0},
            {"name": "Goblin Guide", "type_line": "Creature — Goblin", "cmc": 1, "set": "zen", "collector_number": "143"},
            {"name": "Wrenn and Six", "type_line": "Legendary Planeswalker — Wrenn", "cmc": 2, "set": "mh1", "collector_number": "212"},
        ],
        "not_found": [],
    }

    async def fake_post(self, url, json):
        return _FakeResponse(fake_collection_response)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = await pick_art_card(["Mountain", "Goblin Guide", "Wrenn and Six"])
    assert result == {"name": "Wrenn and Six", "set_code": "mh1", "collector_number": "212"}


@pytest.mark.asyncio
async def test_pick_art_card_returns_none_when_no_candidates(monkeypatch):
    fake_collection_response = {
        "data": [{"name": "Mountain", "type_line": "Basic Land — Mountain", "cmc": 0}],
        "not_found": [],
    }

    async def fake_post(self, url, json):
        return _FakeResponse(fake_collection_response)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = await pick_art_card(["Mountain"])
    assert result == {"name": None, "set_code": None, "collector_number": None}
