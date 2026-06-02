import logging
import time
from urllib.parse import parse_qs, urlencode, urlparse

import requests

logger = logging.getLogger(__name__)

_COLLECTION_URL = "https://api.scryfall.com/cards/collection"
_SEARCH_URL = "https://api.scryfall.com/cards/search"
_HEADERS = {
    "User-Agent": "mtg-manager/1.0",
    "Accept": "application/json",
}
_LEGAL_STATUSES = {"legal", "restricted"}
_BATCH_SIZE = 75
_BATCH_DELAY = 0.1
_SEARCH_PARAMS = {"q", "unique", "order", "dir"}


def _front_face(name: str) -> str:
    return name.split(" // ")[0].strip()


def fetch_legalities(
    card_names: list[str],
    formats: list[str],
) -> dict[str, dict[str, bool]]:
    """Fetch format legality for each card name from Scryfall using the collection endpoint.

    Returns {original_card_name: {format: is_legal}}.
    Cards not found on Scryfall are omitted.
    is_legal is True for "legal" or "restricted" status.
    """
    # Map front-face query name → original name (may differ for DFCs)
    front_to_original: dict[str, str] = {_front_face(n): n for n in card_names}
    query_names = list(front_to_original.keys())

    result: dict[str, dict[str, bool]] = {}

    for i in range(0, len(query_names), _BATCH_SIZE):
        batch = query_names[i : i + _BATCH_SIZE]
        identifiers = [{"name": n} for n in batch]
        try:
            resp = requests.post(
                _COLLECTION_URL,
                json={"identifiers": identifiers},
                headers=_HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            for card in data.get("data", []):
                front = card.get("name", "").split(" // ")[0].strip()
                original = front_to_original.get(front)
                if original is None:
                    continue
                legalities = card.get("legalities", {})
                result[original] = {
                    fmt: legalities.get(fmt) in _LEGAL_STATUSES
                    for fmt in formats
                }
            for entry in data.get("not_found", []):
                logger.warning("Scryfall: card not found: %s", entry.get("name"))
        except requests.RequestException as exc:
            logger.warning("Scryfall batch request failed (batch %d): %s", i // _BATCH_SIZE, exc)
        time.sleep(_BATCH_DELAY)

    return result


def search_scryfall_by_query(query: str) -> list[str]:
    """Search Scryfall by query string and return unique card names.

    Uses unique=cards so each card name appears once regardless of printing.
    """
    next_url: str | None = _SEARCH_URL + "?" + urlencode({"q": query, "unique": "cards"})
    names: list[str] = []

    while next_url:
        resp = requests.get(next_url, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for card in data.get("data", []):
            names.append(card["name"])
        next_url = data.get("next_page") if data.get("has_more") else None
        if next_url:
            time.sleep(_BATCH_DELAY)

    return names


def search_scryfall(url: str) -> list[dict]:
    """Fetch all cards matching a Scryfall search URL (web or API).

    Handles pagination automatically. Returns a flat list of card objects.
    """
    parsed = urlparse(url)
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    api_params = {k: v for k, v in params.items() if k in _SEARCH_PARAMS}

    if not api_params.get("q"):
        raise ValueError("No search query (q=) found in URL")

    next_url: str | None = _SEARCH_URL + "?" + urlencode(api_params)
    all_cards: list[dict] = []

    while next_url:
        resp = requests.get(next_url, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        all_cards.extend(data.get("data", []))
        next_url = data.get("next_page") if data.get("has_more") else None
        if next_url:
            time.sleep(_BATCH_DELAY)

    return all_cards
