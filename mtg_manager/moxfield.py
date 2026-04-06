"""
Moxfield unofficial API client.

Fetches cards from a Moxfield package via:
  GET https://api2.moxfield.com/v3/decks/all/{public_id}

Cards are returned in data["boards"]["mainboard"]["cards"] (a dict keyed by
internal card ID). Each value has: quantity, isFoil, finish, card.name, card.set, card.cn.
"""

import cloudscraper

from .config import MoxfieldPackage
from .models import OwnedCard


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
) -> list[OwnedCard]:
    """Fetch all cards in a Moxfield package and return as OwnedCard list."""
    scraper = _scraper()
    url = f"{BASE_URL}/{package.public_id}"
    resp = scraper.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

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
            foil = item.get("isFoil", False) or item.get("finish", "") == "foil"
            cards.append(
                OwnedCard(
                    name=name,
                    quantity=item.get("quantity", 1),
                    color_group=package.color_group,
                    set_code=card_data.get("set", ""),
                    collector_number=card_data.get("cn", ""),
                    foil=foil,
                )
            )

    return cards
