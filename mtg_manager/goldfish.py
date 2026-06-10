"""
MTGGoldfish scraper.

Supports:
  - Archetype URL: https://www.mtggoldfish.com/archetype/some-archetype#paper
  - Deck URL:      https://www.mtggoldfish.com/deck/12345#paper

MTGGoldfish's /deck/download/ endpoint is protected by Cloudflare WAF.
Instead we parse the visual deck view (/deck/visual/{id}), which is accessible
and embeds the full card list including sideboard in the HTML.

Visual deck structure:
  - Maindeck: one <div class="deck-visual-pile"> per unique card type, containing
    N <img> tags (one per copy) with alt="Card Name".
  - Sideboard: a single <div class="deck-visual-pile"> whose first child is
    <h3 class="deck-visual-sideboard-label">Sideboard</h3>, followed by one
    <a><img alt="Card Name"></a> per sideboard copy.
"""

import re
import time
from collections import defaultdict
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
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
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
        return deck_id, deck_id

    elif path.startswith("/archetype/"):
        html = _fetch_page(scraper, url)
        soup = BeautifulSoup(html, "lxml")

        download_link = soup.find("a", href=_DECK_ID_RE)
        if not download_link:
            raise ValueError("Could not find a deck download link on the archetype page.")
        deck_id = _deck_id_from_url(download_link["href"])
        if not deck_id:
            raise ValueError("Could not extract deck ID from download link.")

        h1 = soup.find("h1", class_="title")
        if h1:
            # Strip the author <span> so we get only the archetype name
            author = h1.find("span", class_="author")
            if author:
                author.decompose()
            name = h1.get_text(strip=True)
        else:
            name = path.split("/")[-1]
        return deck_id, name

    else:
        raise ValueError(f"Unrecognised MTGGoldfish URL format: {url}")


def _parse_visual_page(html: str, deck_id: str, deck_name: str, url: str) -> Decklist:
    """
    Parse the /deck/visual/{id} page into a Decklist.

    Maindeck piles: <div class="deck-visual-pile"> with one <img alt="Name"> per copy.
    Sideboard pile: <div class="deck-visual-pile"> containing <h3>Sideboard</h3>
                    followed by individual <a><img alt="Name"></a> per card copy.

    Container piles (those that wrap other piles) are skipped so cards are only
    counted from the leaf-level piles, preventing double-counting.
    """
    soup = BeautifulSoup(html, "lxml")

    # Extract deck name from title if we only have the raw ID
    if deck_name == deck_id:
        title_tag = soup.find("title")
        if title_tag:
            raw = title_tag.get_text(strip=True)
            raw = re.sub(r"\s+Visual Deck View$", "", raw)
            raw = re.sub(r"\s+by\s+\S+$", "", raw)
            deck_name = raw or deck_id

    maindeck_counts: dict[str, int] = defaultdict(int)
    sideboard_counts: dict[str, int] = defaultdict(int)

    for pile in soup.find_all("div", class_="deck-visual-pile"):
        sb_label = pile.find("h3", class_="deck-visual-sideboard-label")
        if sb_label:
            # Sideboard pile: each <a><img alt="Name"></a> is one card copy
            for a in pile.find_all("a"):
                img = a.find("img", alt=True)
                if img:
                    sideboard_counts[img["alt"]] += 1
        else:
            # Skip container piles that wrap other piles — only count leaf piles
            if pile.find("div", class_="deck-visual-pile"):
                continue
            # Count each img individually by its own card name (one img = one copy)
            for img in pile.find_all("img", alt=True):
                maindeck_counts[img["alt"]] += 1

    cards: list[DeckCard] = (
        [DeckCard(name=n, quantity=q, is_sideboard=False) for n, q in maindeck_counts.items()]
        + [DeckCard(name=n, quantity=q, is_sideboard=True) for n, q in sideboard_counts.items()]
    )
    return Decklist(deck_id=deck_id, name=deck_name, url=url, cards=cards)


def fetch_goldfish_deck(url: str) -> Decklist:
    """Fetch a MTGGoldfish archetype or deck URL and return a Decklist."""
    scraper = _scraper()
    deck_id, deck_name = _resolve_deck_id(scraper, url)
    visual_url = f"{BASE}/deck/visual/{deck_id}"
    html = _fetch_page(scraper, visual_url)
    return _parse_visual_page(html, deck_id, deck_name, url)


def fetch_meta_decklists(
    format_name: str,
    limit: int = 15,
    delay: float = 1.0,
    date_range: str = "week",
) -> list[Decklist]:
    """
    Fetch the top `limit` meta decklists for `format_name` from MTGGoldfish.

    Scrapes /metagame/{format}/full to extract archetype URLs in meta-share order,
    then fetches each archetype's representative decklist (maindeck + sideboard).

    date_range: MTGGoldfish filter — "week" (7 days), "month", "two_weeks", or ""
                for all-time. Defaults to "week" for the most current data.
    """
    scraper = _scraper()
    qs = f"?date_range={date_range}" if date_range else ""
    meta_url = f"{BASE}/metagame/{format_name.lower()}/full{qs}"
    html = _fetch_page(scraper, meta_url)
    soup = BeautifulSoup(html, "lxml")

    # Collect /archetype/ links in order, deduplicating by path (strips #fragment)
    seen: set[str] = set()
    archetype_paths: list[str] = []
    for a in soup.find_all("a", href=True):
        path = urlparse(a["href"]).path
        if path.startswith("/archetype/") and path not in seen:
            seen.add(path)
            archetype_paths.append(path)
        if len(archetype_paths) >= limit:
            break

    if not archetype_paths:
        raise ValueError(
            f"No archetypes found for '{format_name}' on MTGGoldfish. "
            "Check the format name (e.g. modern, standard, pioneer)."
        )

    decklists: list[Decklist] = []
    for path in archetype_paths:
        url = f"{BASE}{path}"
        try:
            time.sleep(delay)
            deck_id, deck_name = _resolve_deck_id(scraper, url)
            visual_url = f"{BASE}/deck/visual/{deck_id}"
            page_html = _fetch_page(scraper, visual_url)
            dl = _parse_visual_page(page_html, deck_id, deck_name, url)
            decklists.append(dl)
        except Exception:
            continue

    return decklists
