import logging
import time

import requests

logger = logging.getLogger(__name__)

_NAMED_URL = "https://api.scryfall.com/cards/named"
_HEADERS = {
    "User-Agent": "mtg-manager/1.0",
    "Accept": "application/json",
}
_LEGAL_STATUSES = {"legal", "restricted"}


def _front_face(name: str) -> str:
    """Return the front-face name for double-faced cards.
    'Ral, Monsoon Mage // Ral, Leyline Prodigy' → 'Ral, Monsoon Mage'
    """
    return name.split(" // ")[0].strip()


def fetch_legalities(
    card_names: list[str],
    formats: list[str],
    delay: float = 0.1,
) -> dict[str, dict[str, bool]]:
    """Fetch format legality for each card name from Scryfall.

    Returns {original_card_name: {format: is_legal}}.
    Cards not found on Scryfall are omitted.
    is_legal is True for "legal" or "restricted" status.
    """
    result: dict[str, dict[str, bool]] = {}
    for name in card_names:
        query_name = _front_face(name)
        try:
            resp = requests.get(
                _NAMED_URL,
                params={"exact": query_name},
                headers=_HEADERS,
                timeout=10,
            )
            if resp.status_code == 404:
                logger.warning("Scryfall: card not found: %s", query_name)
                time.sleep(delay)
                continue
            resp.raise_for_status()
            data = resp.json()
            legalities = data.get("legalities", {})
            result[name] = {
                fmt: legalities.get(fmt) in _LEGAL_STATUSES
                for fmt in formats
            }
        except requests.RequestException as exc:
            logger.warning("Scryfall request failed for %s: %s", query_name, exc)
        time.sleep(delay)
    return result
