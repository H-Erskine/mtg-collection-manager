import logging
import time

import requests

logger = logging.getLogger(__name__)

_COLLECTION_URL = "https://api.scryfall.com/cards/collection"
_HEADERS = {
    "User-Agent": "mtg-manager/1.0",
    "Accept": "application/json",
}
_LEGAL_STATUSES = {"legal", "restricted"}
_BATCH_SIZE = 75
_BATCH_DELAY = 0.1


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
