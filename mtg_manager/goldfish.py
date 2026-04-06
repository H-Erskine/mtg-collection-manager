"""
MTGGoldfish scraper.

Supports:
  - Archetype URL: https://www.mtggoldfish.com/archetype/some-archetype#paper
  - Deck URL:      https://www.mtggoldfish.com/deck/12345#paper

For archetype pages, the representative decklist is embedded as a download link
to /deck/download/{id}. We extract that ID then download the text export.

For direct deck URLs, we extract the ID from the URL path directly.

Download format (plain text):
  4 Lightning Bolt
  4 Goblin Guide
  ...
  <blank line>
  2 Grafdigger's Cage    ← sideboard starts after first blank line
"""

import re
from urllib.parse import urlparse

import cloudscraper
from bs4 import BeautifulSoup

from .models import DeckCard, Decklist


BASE = "https://www.mtggoldfish.com"

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

_DECK_ID_RE = re.compile(r"/deck(?:/download)?/(\d+)")


def _scraper() -> cloudscraper.CloudScraper:
    return cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows"})


def _deck_id_from_url(url: str) -> str | None:
    """Extract numeric deck ID from a /deck/{id} URL."""
    m = _DECK_ID_RE.search(url)
    return m.group(1) if m else None


def _fetch_page(scraper: cloudscraper.CloudScraper, url: str) -> str:
    resp = scraper.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def _resolve_deck_id(scraper: cloudscraper.CloudScraper, url: str) -> tuple[str, str]:
    """
    Return (deck_id, deck_name) for any MTGGoldfish URL.
    For archetype pages, fetches the page to find the embedded deck download link.
    For direct deck URLs, extracts the ID from the path.
    """
    path = urlparse(url).path

    if path.startswith("/deck/"):
        deck_id = _deck_id_from_url(url)
        if not deck_id:
            raise ValueError(f"Cannot extract deck ID from: {url}")
        # Get deck name from page title
        html = _fetch_page(scraper, url)
        soup = BeautifulSoup(html, "lxml")
        title = soup.find("title")
        name = title.get_text(strip=True).split(" MTGGoldfish")[0] if title else deck_id
        return deck_id, name

    elif path.startswith("/archetype/"):
        html = _fetch_page(scraper, url)
        soup = BeautifulSoup(html, "lxml")

        # Find the first /deck/download/{id} link (not the arena/online variants)
        download_link = soup.find("a", href=_DECK_ID_RE)
        if not download_link:
            raise ValueError("Could not find a deck download link on the archetype page.")
        deck_id = _deck_id_from_url(download_link["href"])
        if not deck_id:
            raise ValueError("Could not extract deck ID from download link.")

        # Archetype name from h1 or title
        h1 = soup.find("h1")
        name = h1.get_text(strip=True) if h1 else path.split("/")[-1]
        return deck_id, name

    else:
        raise ValueError(f"Unrecognised MTGGoldfish URL format: {url}")


def _parse_text_export(text: str, deck_id: str, deck_name: str, url: str) -> Decklist:
    """
    Parse the plain-text deck export into a Decklist.
    Format: 'N Card Name' lines; first blank line separates maindeck from sideboard.
    """
    cards: list[DeckCard] = []
    in_sideboard = False

    for line in text.splitlines():
        line = line.strip()
        if not line:
            in_sideboard = True
            continue
        m = re.match(r"^(\d+)\s+(.+)$", line)
        if m:
            cards.append(DeckCard(
                name=m.group(2).strip(),
                quantity=int(m.group(1)),
                is_sideboard=in_sideboard,
            ))

    return Decklist(deck_id=deck_id, name=deck_name, url=url, cards=cards)


def fetch_goldfish_deck(url: str) -> Decklist:
    """Fetch a MTGGoldfish archetype or deck URL and return a Decklist."""
    scraper = _scraper()
    deck_id, deck_name = _resolve_deck_id(scraper, url)
    download_url = f"{BASE}/deck/download/{deck_id}"
    text = _fetch_page(scraper, download_url)
    return _parse_text_export(text, deck_id, deck_name, url)
