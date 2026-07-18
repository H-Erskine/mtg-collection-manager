"""Build meta.json from the meta decklists saved by scripts/refresh_meta.py.

Decklists themselves are refreshed nightly from MTGGoldfish (see
scripts/refresh_meta.py); this export only compares those saved lists
against the current collection, so it's cheap enough to run on every sync.

Run directly to generate the file:
    python -m web.export_meta
    python -m web.export_meta --format modern --format standard
"""
import json
import os
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

from mtg_manager.config import load_config
from mtg_manager.db import get_conn, get_meta_decks, get_owned_quantity


def _normalize(name: str) -> str:
    """Lowercase and strip diacritics so 'Lorien' matches 'Lórien'."""
    return unicodedata.normalize("NFD", name.lower()).encode("ascii", "ignore").decode("ascii")


def _scryfall_post(identifiers: list[dict]) -> dict:
    """POST to /cards/collection and return the parsed JSON response.
    On HTTP errors, attaches the response body to the exception message.
    """
    payload = json.dumps({"identifiers": identifiers}).encode()
    req = urllib.request.Request(
        "https://api.scryfall.com/cards/collection",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "mtg-manager/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body[:300]}") from e


def _scryfall_named_price(name: str) -> float | None:
    """Fetch EUR price via /cards/named?fuzzy=, falling back to front face for // names.

    Used for cards the collection endpoint couldn't match — typically split cards
    or DFCs where the Goldfish name format differs from Scryfall's canonical form.
    """
    queries = [name]
    if " // " in name:
        queries.append(name.split(" // ")[0].strip())
    for query in queries:
        url = f"https://api.scryfall.com/cards/named?fuzzy={urllib.parse.quote(query)}"
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "mtg-manager/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                card = json.loads(resp.read())
            eur_str = (card.get("prices") or {}).get("eur")
            if eur_str:
                return float(eur_str)
        except Exception:
            pass
        time.sleep(0.1)
    return None


def _fetch_scryfall_prices(card_names: list[str]) -> dict[str, float | None]:
    """Fetch EUR prices from Scryfall for a list of card names.

    Returns normalized_name → EUR price (None if unavailable or unpriced).
    Uses /cards/collection with up to 75 identifiers per batch. If a batch
    fails, waits 2s then retries each card individually to isolate bad names.
    """
    result: dict[str, float | None] = {}
    batch_size = 75
    for i in range(0, len(card_names), batch_size):
        batch = card_names[i:i + batch_size]
        batch_num = i // batch_size + 1
        identifiers = [{"name": n} for n in batch]
        try:
            data = _scryfall_post(identifiers)
            for card in data.get("data", []):
                norm = _normalize(card["name"])
                eur_str = (card.get("prices") or {}).get("eur")
                result[norm] = float(eur_str) if eur_str else None
            # Retry cards the collection endpoint couldn't match (common for // names)
            for ident in data.get("not_found", []):
                orig = ident.get("name", "")
                if not orig:
                    continue
                print(f"[info] Collection not_found: {orig!r} — trying fuzzy named lookup", file=sys.stderr)
                time.sleep(0.15)
                price = _scryfall_named_price(orig)
                result[_normalize(orig)] = price
                if price is None:
                    print(f"[warn] No price found for: {orig!r}", file=sys.stderr)
        except Exception as e:
            print(f"[warn] Scryfall batch {batch_num} failed: {e}", file=sys.stderr)
            print(f"[warn] Batch {batch_num} cards: {batch}", file=sys.stderr)
            print(f"[info] Waiting 2s before per-card retry…", file=sys.stderr)
            time.sleep(2.0)
            for name in batch:
                try:
                    data = _scryfall_post([{"name": name}])
                    for card in data.get("data", []):
                        norm = _normalize(card["name"])
                        eur_str = (card.get("prices") or {}).get("eur")
                        result[norm] = float(eur_str) if eur_str else None
                except Exception as card_err:
                    print(f"[warn] Scryfall price failed for {name!r}: {card_err}", file=sys.stderr)
                time.sleep(0.15)
        if i + batch_size < len(card_names):
            time.sleep(0.15)
    return result


def export_meta_static(
    cfg,
    formats: list[str],
) -> None:
    """Compare saved meta decklists against the current collection and write meta.json.

    Reads decklists from the meta_decks/meta_deck_cards tables (kept fresh by
    scripts/refresh_meta.py) rather than fetching from MTGGoldfish, so this is
    safe to call on every sync.
    """
    if cfg.web_static_dir is None:
        print("web_static_dir not set in config — skipping meta export.", file=sys.stderr)
        return

    out_dir = cfg.web_static_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    with get_conn(cfg.db_path) as conn:
        # Build name → best printing lookup for card images.
        # Keys are both lowercased and diacritic-normalized so that names from
        # MTGGoldfish (e.g. "Lorien Revealed") match DB entries ("Lórien Revealed").
        owned_rows = conn.execute(
            "SELECT name, set_code, collector_number, SUM(quantity) as total"
            " FROM owned_cards GROUP BY name, set_code, collector_number"
            " ORDER BY total DESC"
        ).fetchall()
        printing_map: dict[str, tuple[str, str]] = {}
        normalized_owned: dict[str, int] = {}
        for r in owned_rows:
            printing = (r["set_code"], r["collector_number"])
            for key in (r["name"].lower(), _normalize(r["name"])):
                if key not in printing_map:
                    printing_map[key] = printing
            normalized_owned[_normalize(r["name"])] = (
                normalized_owned.get(_normalize(r["name"]), 0) + r["total"]
            )
            if " // " in r["name"]:
                front, back = r["name"].split(" // ", 1)
                for part in (front.strip(), back.strip()):
                    for key in (part.lower(), _normalize(part)):
                        if key not in printing_map:
                            printing_map[key] = printing
                    normalized_owned[_normalize(part)] = (
                        normalized_owned.get(_normalize(part), 0) + r["total"]
                    )

        format_results = []
        for fmt in formats:
            decklists = get_meta_decks(conn, fmt)
            if not decklists:
                print(f"[warn] No saved meta decklists for '{fmt}' — run scripts.refresh_meta first.", file=sys.stderr)
                continue

            decks = []
            for dl in decklists:
                card_totals: dict[str, int] = defaultdict(int)
                for card in dl.cards:
                    card_totals[card.name] += card.quantity

                total_slots = sum(card_totals.values())
                owned_slots = 0
                cards = []
                for name, qty in card_totals.items():
                    owned = get_owned_quantity(conn, name)
                    if owned == 0:
                        owned = normalized_owned.get(_normalize(name), 0)
                    owned_slots += min(owned, qty)
                    printing = printing_map.get(name.lower()) or printing_map.get(_normalize(name))
                    cards.append({
                        "name": name,
                        "quantity": qty,
                        "owned": owned,
                        "set_code": printing[0] if printing else None,
                        "collector_number": printing[1] if printing else None,
                    })

                # Missing cards first, then alphabetical
                cards.sort(key=lambda c: (c["owned"] >= c["quantity"], c["name"]))

                decks.append({
                    "name": dl.name,
                    "url": dl.url,
                    "meta_share": dl.meta_share,
                    "total_slots": total_slots,
                    "owned_slots": owned_slots,
                    "cards": cards,
                })

            # Sort by meta share descending; fall back to owned% if shares unavailable
            if any(d["meta_share"] > 0 for d in decks):
                decks.sort(key=lambda d: -d["meta_share"])
            else:
                decks.sort(key=lambda d: -(d["owned_slots"] / d["total_slots"]) if d["total_slots"] else 0)
            format_results.append({"format": fmt, "decks": decks})
            print(f"  → {len(decks)} decks fetched")

    # Fetch EUR prices for missing cards (cached between runs)
    cache_path = out_dir / "prices_cache.json"
    try:
        price_cache: dict[str, float | None] = json.loads(cache_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        price_cache = {}

    missing_names = list({
        c["name"]
        for fmt in format_results
        for deck in fmt["decks"]
        for c in deck["cards"]
        if c["owned"] < c["quantity"]
    })
    uncached = [n for n in missing_names if _normalize(n) not in price_cache]
    if uncached:
        print(f"Fetching EUR prices for {len(uncached)} new missing card(s) from Scryfall…")
        new_prices = _fetch_scryfall_prices(uncached)
        price_cache.update(new_prices)
        tmp_cache = cache_path.with_suffix(".tmp")
        tmp_cache.write_text(json.dumps(price_cache, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_cache, cache_path)
    elif missing_names:
        print(f"EUR prices: all {len(missing_names)} missing card(s) already cached.")

    for fmt in format_results:
        for deck in fmt["decks"]:
            for c in deck["cards"]:
                if c["owned"] < c["quantity"]:
                    c["eur_price"] = price_cache.get(_normalize(c["name"]))

    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "formats": format_results,
    }

    dest = out_dir / "meta.json"
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, dest)
    total = sum(len(f["decks"]) for f in format_results)
    print(f"Wrote {dest} ({total} decks across {len(format_results)} format(s))")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build meta.json from saved meta decklists")
    parser.add_argument(
        "--format", "-f", dest="formats", action="append",
        default=[], metavar="FORMAT",
        help="Format to export (repeatable, default: config [formats].tracked)",
    )
    args = parser.parse_args()

    cfg = load_config()
    export_meta_static(cfg, formats=args.formats or cfg.formats or ["modern", "standard"])
