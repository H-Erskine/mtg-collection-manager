"""
URL router — dispatches a deck URL to the right scraper and returns Decklist(s).

Supported sources:
  MTGTop8:    https://www.mtgtop8.com/event?...  or  /compare?...
  Moxfield:   https://www.moxfield.com/decks/{id}
  MTGGoldfish: https://www.mtggoldfish.com/archetype/...  or  /deck/{id}
"""

from .goldfish import fetch_goldfish_deck
from .models import Decklist
from .moxfield import fetch_moxfield_deck
from .mtgtop8 import fetch_decklists as fetch_mtgtop8


def fetch_decklists(url: str, delay: float = 1.5) -> list[Decklist]:
    """
    Given a URL from any supported source, return one or more Decklists.
    Raises ValueError for unrecognised URLs.
    """
    host = _host(url)

    if "mtgtop8.com" in host:
        return fetch_mtgtop8(url, delay=delay)

    if "moxfield.com" in host:
        return [fetch_moxfield_deck(url)]

    if "mtggoldfish.com" in host:
        return [fetch_goldfish_deck(url)]

    raise ValueError(
        f"Unsupported URL: {url}\n"
        "Supported sources: mtgtop8.com, moxfield.com, mtggoldfish.com"
    )


def _host(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc.lower()
