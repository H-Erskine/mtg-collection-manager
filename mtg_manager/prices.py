"""
CardMarket price fetching via the Scryfall /cards/collection batch API.

Scryfall sources its EUR prices from CardMarket, so ``prices.eur`` /
``prices.eur_foil`` on a Scryfall card object are CardMarket retail prices.
"""
from __future__ import annotations

import logging
import requests

log = logging.getLogger(__name__)

SCRYFALL_COLLECTION_URL = "https://api.scryfall.com/cards/collection"
BATCH_SIZE = 75  # Scryfall maximum per request


def fetch_cardmarket_prices(rows: list) -> dict[tuple, float]:
    """Batch-fetch CardMarket EUR prices for a list of for-sale card rows.

    *rows* should be ``sqlite3.Row`` objects (or any mapping) with at least
    ``set_code``, ``collector_number``, and ``foil`` keys — i.e. the output
    of ``list_for_sale_cards()``.

    Returns a dict mapping ``(set_code_lower, collector_number, foil_int)``
    to a float price in EUR.  Cards not found on Scryfall are omitted.
    """
    result: dict[tuple, float] = {}

    batches = [rows[i:i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
    for batch in batches:
        identifiers = [
            {"set": row["set_code"].lower(), "collector_number": row["collector_number"]}
            for row in batch
            if row["set_code"] and row["collector_number"]
        ]
        if not identifiers:
            continue
        try:
            resp = requests.post(
                SCRYFALL_COLLECTION_URL,
                json={"identifiers": identifiers},
                timeout=8,
            )
            resp.raise_for_status()
        except Exception as exc:
            log.warning("Scryfall price fetch failed: %s", exc)
            continue
        for card in resp.json().get("data", []):
            prices = card.get("prices", {})
            s = card.get("set", "").lower()
            cn = card.get("collector_number", "")
            if prices.get("eur"):
                result[(s, cn, 0)] = float(prices["eur"])
            if prices.get("eur_foil"):
                result[(s, cn, 1)] = float(prices["eur_foil"])

    return result
