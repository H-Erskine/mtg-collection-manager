"""
Moxfield unofficial API client.

Fetches cards from a Moxfield package or deck via:
  GET https://api2.moxfield.com/v3/decks/all/{public_id}

Cards are returned in data["boards"]["mainboard"]["cards"] (a dict keyed by
internal card ID). Each value has: quantity, isFoil, finish, card.name, card.set, card.cn.
"""

import logging
import re
import threading
import time

import cloudscraper

from .config import MoxfieldPackage
from .models import DeckCard, Decklist, OwnedCard

logger = logging.getLogger(__name__)

# Global lock: all Moxfield HTTP requests serialise through this so concurrent
# Discord users can't burst-hammer the API. The per-request delay still applies
# inside the critical section, capping outbound rate at ~1 req/s globally.
_MOXFIELD_LOCK = threading.Lock()


BASE_URL = "https://api2.moxfield.com/v3/decks/all"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Origin": "https://www.moxfield.com",
    "Referer": "https://www.moxfield.com/",
}

# Boards to import from (excludes maybeboard / tokens / etc.)
IMPORT_BOARDS = {"mainboard", "sideboard", "commanders", "companions"}


def _scraper() -> cloudscraper.CloudScraper:
    return cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows"})


def fetch_package_cards(
    package: MoxfieldPackage,
    delay: float = 1.0,
) -> tuple[list[OwnedCard], str]:
    """Fetch all cards in a Moxfield package.

    Returns ``(cards, package_name)`` where *package_name* is the deck title
    from the Moxfield API.  Callers use the name to detect for-sale packages
    (those whose title starts with ``$``).
    """
    scraper = _scraper()
    url = f"{BASE_URL}/{package.public_id}"
    with _MOXFIELD_LOCK:
        resp = scraper.get(url, headers=HEADERS, timeout=30)
        time.sleep(delay)
    resp.raise_for_status()
    data = resp.json()

    package_name: str = data.get("name", "")
    boards = data.get("boards", {})
    cards: list[OwnedCard] = []

    for board_name, board in boards.items():
        if board_name not in IMPORT_BOARDS:
            continue
        for item in board.get("cards", {}).values():
            card_data = item.get("card", {})
            name = card_data.get("name", "")
            if not name:
                continue
            printings = item.get("printingData") or []
            cmc: float = card_data.get("cmc", 0.0) or 0.0
            if printings:
                # Use per-printing breakdown so different set/foil copies are stored separately
                for p in printings:
                    foil = p.get("finish", "") == "foil" or p.get("isFoil", False)
                    cards.append(
                        OwnedCard(
                            name=name,
                            quantity=p.get("quantity", 1),
                            color_group=package.color_group,
                            set_code=p.get("set", card_data.get("set", "")),
                            collector_number=p.get("cn", card_data.get("cn", "")),
                            foil=foil,
                            cmc=cmc,
                        )
                    )
            else:
                # Fallback for items with no printingData
                foil = item.get("isFoil", False) or item.get("finish", "") == "foil"
                cards.append(
                    OwnedCard(
                        name=name,
                        quantity=item.get("quantity", 1),
                        color_group=package.color_group,
                        set_code=card_data.get("set", ""),
                        collector_number=card_data.get("cn", ""),
                        foil=foil,
                        cmc=cmc,
                    )
                )

    return cards, package_name


# ---------------------------------------------------------------------------
# Deck URL → Decklist (for mtg missing)
# ---------------------------------------------------------------------------

SIDEBOARD_BOARDS = {"sideboard"}
MAINDECK_BOARDS = {"mainboard", "commanders", "companions"}

_PUBLIC_ID_RE = re.compile(r"moxfield\.com/decks/([A-Za-z0-9_\-]+)")


def public_id_from_url(url: str) -> str | None:
    """Extract the public_id from a Moxfield deck URL."""
    m = _PUBLIC_ID_RE.search(url)
    return m.group(1) if m else None


def fetch_moxfield_deck(url: str) -> Decklist:
    """
    Fetch a public Moxfield deck URL and return it as a Decklist.
    URL form: https://www.moxfield.com/decks/{public_id}
    """
    public_id = public_id_from_url(url)
    if not public_id:
        raise ValueError(f"Cannot extract public_id from Moxfield URL: {url}")

    scraper = _scraper()
    api_url = f"{BASE_URL}/{public_id}"
    with _MOXFIELD_LOCK:
        resp = scraper.get(api_url, headers=HEADERS, timeout=30)
        time.sleep(1.0)
    resp.raise_for_status()
    data = resp.json()

    deck_name = data.get("name", public_id)
    boards = data.get("boards", {})
    cards: list[DeckCard] = []

    print(f"[moxfield] deck='{deck_name}' boards={list(boards.keys())}", flush=True)

    for board_name, board in boards.items():
        if board_name not in MAINDECK_BOARDS | SIDEBOARD_BOARDS:
            continue
        is_side = board_name in SIDEBOARD_BOARDS
        board_cards = board.get("cards", {})
        print(f"[moxfield]   {board_name}: {len(board_cards)} cards", flush=True)
        for item in board_cards.values():
            name = item.get("card", {}).get("name", "")
            if name:
                cards.append(DeckCard(
                    name=name,
                    quantity=item.get("quantity", 1),
                    is_sideboard=is_side,
                ))

    print(f"[moxfield]   total parsed: {len(cards)}", flush=True)

    if not cards:
        print(f"[moxfield] ERROR: 0 cards. Top-level keys: {list(data.keys())}", flush=True)
        print(f"[moxfield] Raw boards value: {boards}", flush=True)
        raise ValueError(
            f"No cards found in Moxfield deck '{deck_name}'. "
            "The deck may be private, empty, or the API format may have changed."
        )

    return Decklist(deck_id=public_id, name=deck_name, url=url, cards=cards)
