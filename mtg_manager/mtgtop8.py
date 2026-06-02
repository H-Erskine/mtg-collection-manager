"""
MTGTop8 scraper.

Supports:
  - Compare URL:  https://www.mtgtop8.com/compare?l=_id1_id2_id3_...
  - Single deck:  https://www.mtgtop8.com/event?e=EVENT_ID&d=DECK_ID

Cards are rendered in <div class="deck_line"> elements, not tables.
Each div's text is "N Card Name" and sideboard is preceded by a
<div class="O14"> containing "SIDEBOARD".
"""

import re
import time
from urllib.parse import urlparse, parse_qs

import cloudscraper
from bs4 import BeautifulSoup

from .models import DeckCard, Decklist


BASE = "https://www.mtgtop8.com"

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}


def _scraper() -> cloudscraper.CloudScraper:
    s = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows"})
    # Visit homepage to get session, then select English to avoid lang redirect
    s.get(f"{BASE}/", headers=HEADERS, timeout=15)
    s.post(f"{BASE}/", data={"lang": "EN"}, headers=HEADERS, timeout=15)
    return s


def parse_deck_ids(url: str) -> list[str]:
    """Extract deck IDs from a compare URL's `l` parameter, e.g. `_id1_id2_id3`."""
    qs = parse_qs(urlparse(url).query)
    if "l" not in qs:
        return []
    raw = qs["l"][0]
    return [i for i in raw.split("_") if i]


def _fetch_html(scraper: cloudscraper.CloudScraper, url: str, delay: float = 0.0) -> str:
    if delay:
        time.sleep(delay)
    resp = scraper.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def _parse_deck_lines(soup: BeautifulSoup, deck_id: str, url: str) -> Decklist:
    """
    Parse card divs from a MTGTop8 deck page.

    Structure:
      <div class="deck_line hover_tr" onclick="AffCard('set123','Card+Name','','');">
        4 <span class=L14>Card Name</span>
      </div>

    Sideboard is preceded by a section header div containing "SIDEBOARD".
    """
    # Deck name from page title
    title_tag = soup.find("title")
    name = title_tag.get_text(strip=True).split(" @ ")[0] if title_tag else deck_id

    decklist = Decklist(deck_id=deck_id, name=name, url=url)
    in_sideboard = False

    for div in soup.find_all("div"):
        classes = div.get("class", [])
        text = div.get_text(strip=True)

        # Sideboard section marker: <div class="O14">15 SIDEBOARD</div> or similar
        if "O14" in classes and "SIDEBOARD" in text.upper():
            in_sideboard = True
            continue

        if "deck_line" in classes:
            # Card name is in a child <span class="L14">, qty is the preceding text node
            name_span = div.find("span")
            if name_span:
                card_name = name_span.get_text(strip=True)
                # MTGTop8 uses " / " for split cards; normalise to standard " // "
                card_name = re.sub(r"\s*/\s*", " // ", card_name)
                # Quantity is the text before the span
                raw = div.get_text(strip=True)
                m = re.match(r"^(\d+)", raw)
                qty = int(m.group(1)) if m else 1
                if card_name:
                    decklist.cards.append(DeckCard(name=card_name, quantity=qty, is_sideboard=in_sideboard))

    return decklist


# ---------------------------------------------------------------------------
# Single deck page scraping
# ---------------------------------------------------------------------------

def scrape_deck(scraper: cloudscraper.CloudScraper, url: str, delay: float = 1.5) -> Decklist:
    """Scrape a single MTGTop8 deck page."""
    qs = parse_qs(urlparse(url).query)
    deck_id = qs.get("d", ["unknown"])[0]
    html = _fetch_html(scraper, url, delay=delay)
    soup = BeautifulSoup(html, "lxml")
    return _parse_deck_lines(soup, deck_id, url)


# ---------------------------------------------------------------------------
# Compare page scraping — fetch each deck individually
# ---------------------------------------------------------------------------

def scrape_compare(url: str, delay: float = 1.5) -> list[Decklist]:
    """
    Parse deck IDs from a compare URL and scrape each deck page individually.
    Returns one Decklist per deck.
    """
    ids = parse_deck_ids(url)
    if not ids:
        return []

    # We need the event ID too — get it from the first deck page
    # The compare URL doesn't contain e=, so scrape the first deck to find it,
    # or just fetch each deck using the compare URL's context.
    # MTGTop8 deck URLs need e= (event id) AND d= (deck id).
    # The compare URL doesn't expose e=, so we scrape the compare page to find event links.
    scraper = _scraper()
    html = _fetch_html(scraper, url, delay=0)
    soup = BeautifulSoup(html, "lxml")

    # Extract deck URLs from links on the compare page
    deck_urls: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        qs = parse_qs(urlparse(href).query)
        d = qs.get("d", [""])[0]
        e = qs.get("e", [""])[0]
        if d and e and d in ids:
            full = href if href.startswith("http") else f"{BASE}/{href.lstrip('/')}"
            deck_urls[d] = full

    decklists: list[Decklist] = []
    for deck_id in ids:
        deck_url = deck_urls.get(deck_id)
        if not deck_url:
            # Fallback stub with no cards if we can't find the event link
            decklists.append(Decklist(deck_id=deck_id, name=deck_id, url=url))
            continue
        dl = scrape_deck(scraper, deck_url, delay=delay)
        decklists.append(dl)

    return decklists


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------

def fetch_decklists(url: str, delay: float = 1.5) -> list[Decklist]:
    """Given either a compare URL or a single deck URL, return all Decklists."""
    if "compare" in url:
        return scrape_compare(url, delay=delay)
    else:
        scraper = _scraper()
        return [scrape_deck(scraper, url, delay=delay)]
