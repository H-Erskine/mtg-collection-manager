"""
Moxfield write API — card tag management via deck/binder endpoints.

Tags are set via:
  PUT https://api2.moxfield.com/v2/decks/{internalId}/cards/{card_id}/tags
  Headers: Authorization, Content-Type, x-moxfield-version, x-deck-version, x-public-deck-id
  Body: {"tags": [...]}  — replaces ALL tags on the card.

We track tags in the local DB (moxfield_tags table) so we can do
read-modify-write without losing tags set externally via the Moxfield UI.
"""

import base64
import json
import logging
import os
import re
import time
from pathlib import Path

import cloudscraper

logger = logging.getLogger(__name__)

_BASE = "https://api2.moxfield.com"
_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Origin": "https://www.moxfield.com",
    "Referer": "https://www.moxfield.com/",
    "x-moxfield-version": "2026.05.21.1",
}

_DEFAULT_ENV_PATH = Path(__file__).parent.parent / ".env"
_STARTUP_URL = "https://api2.moxfield.com/v1/startup/authenticated"


def deck_name_to_tag(deck_name: str) -> str:
    """Convert a deck name to an in-box tag slug. 'Titan kani' → 'in-box-titan-kani'."""
    slug = deck_name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return f"in-box-{slug}"


def _scraper() -> cloudscraper.CloudScraper:
    return cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows"})


def _decode_jwt_exp(token: str) -> int | None:
    """Decode JWT exp claim without verifying signature. Returns Unix timestamp or None."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        # Add padding if needed
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data.get("exp")
    except Exception:
        return None


def _is_token_expired(token: str, buffer_seconds: int = 300) -> bool:
    """Return True if the token is expired or expiring within buffer_seconds."""
    exp = _decode_jwt_exp(token)
    if exp is None:
        return True
    return time.time() >= exp - buffer_seconds


def _update_env_key(env_path: Path, key: str, value: str) -> None:
    """Update a single key in .env, appending if not present."""
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n")


def _fetch_fresh_token(refresh_token_cookie: str) -> tuple[str, str]:
    """Call /v1/startup/authenticated with refresh_token cookie.

    Returns (new_jwt, new_refresh_token). The refresh_token rotates on each use —
    callers must persist the returned refresh_token for the next call.
    """
    scraper = _scraper()
    r = scraper.post(
        _STARTUP_URL,
        json={"ignoreCookie": False, "isAppLogin": False},
        headers={**_HEADERS, "Content-Type": "application/json"},
        cookies={"refresh_token": refresh_token_cookie, "logged_in": "true"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    new_jwt = (data.get("refresh") or {}).get("access_token")
    if not new_jwt:
        logger.error("Moxfield startup response keys: %s", list(data.keys()))
        raise ValueError(f"Could not find token in /v1/startup/authenticated response. Keys: {list(data.keys())}")
    new_refresh = r.cookies.get("refresh_token", "")
    return new_jwt, new_refresh


def get_token(env_path: Path | None = None) -> str | None:
    """Return a valid Moxfield JWT, refreshing automatically if expired.

    Reads MOXFIELD_TOKEN and MOXFIELD_REFRESH_TOKEN from os.environ.
    If the token is expired or expiring within 5 minutes, calls
    /v1/startup/authenticated with the stored refresh_token cookie. The
    refresh_token rotates on each use — both the new JWT and the new
    refresh_token are written back to .env and os.environ automatically.
    Returns None if no token is configured.
    """
    env_path = env_path or _DEFAULT_ENV_PATH
    token = os.environ.get("MOXFIELD_TOKEN")
    if not token:
        return None

    if not _is_token_expired(token):
        return token

    refresh_cookie = os.environ.get("MOXFIELD_REFRESH_TOKEN")
    if not refresh_cookie:
        logger.warning("MOXFIELD_TOKEN is expired and MOXFIELD_REFRESH_TOKEN is not set — cannot auto-refresh")
        return token

    logger.info("MOXFIELD_TOKEN expired, refreshing via /v1/startup/authenticated")
    try:
        new_token, new_refresh = _fetch_fresh_token(refresh_cookie)
    except Exception as e:
        logger.warning("Moxfield token refresh failed: %s", e)
        return token

    _update_env_key(env_path, "MOXFIELD_TOKEN", new_token)
    os.environ["MOXFIELD_TOKEN"] = new_token
    if new_refresh:
        _update_env_key(env_path, "MOXFIELD_REFRESH_TOKEN", new_refresh)
        os.environ["MOXFIELD_REFRESH_TOKEN"] = new_refresh
    logger.info("MOXFIELD_TOKEN refreshed (new refresh_token stored)")
    return new_token


def _write_headers(token: str, deck_version: int, public_id: str) -> dict:
    return {
        **_HEADERS,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-deck-version": str(deck_version),
        "x-public-deck-id": public_id,
    }


def fetch_deck_info(public_id: str, token: str, delay: float = 0.6) -> tuple[str, int]:
    """Fetch binder/deck internal ID and current version.

    Returns (internal_id, version) for use in subsequent write calls.
    """
    scraper = _scraper()
    time.sleep(delay)
    r = scraper.get(
        f"{_BASE}/v3/decks/all/{public_id}",
        headers={**_HEADERS, "Authorization": f"Bearer {token}"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    internal_id: str = data.get("id", "")
    version: int = data.get("version", 0)
    if not internal_id:
        raise ValueError(f"No internal id in Moxfield response for public_id={public_id}")
    return internal_id, version


def set_card_tags(
    deck_internal_id: str,
    card_id: str,
    tags: list[str],
    token: str,
    deck_version: int,
    deck_public_id: str,
    delay: float = 0.6,
) -> bool:
    """PUT the full tag list for a card in a deck/binder. Returns True on success."""
    scraper = _scraper()
    time.sleep(delay)
    r = scraper.put(
        f"{_BASE}/v2/decks/{deck_internal_id}/cards/{card_id}/tags",
        json={"tags": tags},
        headers=_write_headers(token, deck_version, deck_public_id),
        timeout=30,
    )
    if r.status_code == 200:
        return True
    logger.warning(
        "tag PUT failed for card_id=%s deck=%s: %s %s",
        card_id, deck_internal_id, r.status_code, r.text[:120],
    )
    return False
