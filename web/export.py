import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from mtg_manager.config import Config
from mtg_manager.db import get_cards_over_limit, get_conn, list_wants_cards

_SCRYFALL_HEADERS = {"User-Agent": "mtg-manager/1.0 (personal collection site)"}


def export_static(cfg: Config) -> None:
    """Write collection.json, decks.json, and sale.json to cfg.web_static_dir. No-op if unset."""
    if cfg.web_static_dir is None:
        return

    out_dir = cfg.web_static_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    with get_conn(cfg.db_path) as conn:
        updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        collection_cards = get_collection_data(conn)
        decks_data = {"updated_at": updated_at, "decks": get_decks_data(conn)}
        sale_data = get_sale_data(conn)

    collection_data = {"updated_at": updated_at, "cards": collection_cards}
    _write_json(out_dir / "collection.json", collection_data)
    _write_json(out_dir / "decks.json", decks_data)
    _write_json(out_dir / "sale.json", {"updated_at": updated_at, **sale_data})

    # Collect unique printings from all data sources for image caching
    printings: set[tuple[str, str]] = set()
    for c in collection_cards:
        if c["set_code"] and c["collector_number"]:
            printings.add((c["set_code"], c["collector_number"]))
    for c in sale_data["for_sale"]:
        if c["set_code"] and c["collector_number"]:
            printings.add((c["set_code"], c["collector_number"]))
    for c in sale_data["extras"]:
        if c["set_code"] and c["collector_number"]:
            printings.add((c["set_code"], c["collector_number"]))
    for c in sale_data["wants"]:
        if c["set_code"] and c["collector_number"]:
            printings.add((c["set_code"], c["collector_number"]))

    if printings:
        t = threading.Thread(
            target=_download_images_bg,
            args=(out_dir, list(printings)),
            daemon=True,
        )
        t.start()


def get_collection_data(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT name, set_code, collector_number, foil, quantity, color_group "
        "FROM owned_cards ORDER BY color_group, name"
    ).fetchall()
    return [
        {
            "name": r["name"],
            "set_code": r["set_code"],
            "collector_number": r["collector_number"],
            "foil": bool(r["foil"]),
            "quantity": r["quantity"],
            "color_group": r["color_group"],
        }
        for r in rows
    ]


def get_decks_data(conn) -> list[dict]:
    # Build printing lookup: lower_name -> (set_code, collector_number)
    # Ordered by quantity DESC so the highest-qty printing wins.
    owned_rows = conn.execute(
        "SELECT name, set_code, collector_number FROM owned_cards ORDER BY quantity DESC"
    ).fetchall()
    printing_map: dict[str, tuple[str, str]] = {}
    for r in owned_rows:
        full_lower = r["name"].lower()
        if full_lower not in printing_map:
            printing_map[full_lower] = (r["set_code"], r["collector_number"])
        if " // " in r["name"]:
            front_lower = r["name"].split(" // ")[0].strip().lower()
            back_lower  = r["name"].split(" // ")[1].strip().lower()
            if front_lower not in printing_map:
                printing_map[front_lower] = (r["set_code"], r["collector_number"])
            if back_lower not in printing_map:
                printing_map[back_lower] = (r["set_code"], r["collector_number"])

    deck_rows = conn.execute(
        "SELECT deck_id, deck_name, deck_url, box_name, built_at "
        "FROM built_decks ORDER BY box_name, deck_name"
    ).fetchall()

    decks = []
    for deck in deck_rows:
        card_rows = conn.execute(
            "SELECT card_name, quantity, is_proxy FROM allocated_cards "
            "WHERE deck_id = ? ORDER BY card_name",
            (deck["deck_id"],),
        ).fetchall()

        cards = []
        for c in card_rows:
            printing = printing_map.get(c["card_name"].lower())
            cards.append({
                "name": c["card_name"],
                "quantity": c["quantity"],
                "is_proxy": bool(c["is_proxy"]),
                "set_code": printing[0] if printing else None,
                "collector_number": printing[1] if printing else None,
            })

        decks.append({
            "deck_id": deck["deck_id"],
            "deck_name": deck["deck_name"],
            "deck_url": deck["deck_url"],
            "box_name": deck["box_name"],
            "built_at": deck["built_at"],
            "cards": cards,
        })

    return decks


def get_sale_data(conn) -> dict:
    sale_rows = conn.execute(
        "SELECT name, set_code, collector_number, foil, quantity, price, color_group "
        "FROM for_sale_cards ORDER BY price DESC, name"
    ).fetchall()

    extra_rows = get_cards_over_limit(conn, limit=4)
    wants_rows = list_wants_cards(conn)

    return {
        "for_sale": [
            {
                "name": r["name"],
                "set_code": r["set_code"],
                "collector_number": r["collector_number"],
                "foil": bool(r["foil"]),
                "quantity": r["quantity"],
                "price": r["price"],
                "color_group": r["color_group"],
            }
            for r in sale_rows
        ],
        "extras": [
            {
                "name": r["name"],
                "set_code": r["set_code"],
                "collector_number": r["collector_number"],
                "foil": bool(r["foil"]),
                "quantity": r["quantity"],
                "color_group": r["color_group"],
            }
            for r in extra_rows
        ],
        "wants": [
            {
                "name": r["name"],
                "set_code": r["set_code"],
                "collector_number": r["collector_number"],
                "foil": bool(r["foil"]),
                "quantity": r["quantity"],
                "color_group": r["color_group"],
                "any_version": bool(r["any_version"]),
            }
            for r in wants_rows
        ],
    }


def _download_images_bg(out_dir: Path, printings: list[tuple[str, str]]) -> None:
    """Download Scryfall card images to out_dir/images/. Skips already-cached files."""
    img_dir = out_dir / "images"
    img_dir.mkdir(exist_ok=True)

    for set_code, collector_number in printings:
        set_dir = img_dir / set_code
        set_dir.mkdir(exist_ok=True)
        # Replace / in collector_number to avoid accidental subdirectory creation
        safe_cn = collector_number.replace("/", "_")
        dest = set_dir / f"{safe_cn}.jpg"
        if dest.exists():
            continue
        from urllib.parse import quote as _quote
        url = (
            f"https://api.scryfall.com/cards/{_quote(set_code, safe='')}"
            f"/{_quote(collector_number, safe='')}?format=image&version=normal"
        )
        try:
            resp = requests.get(url, headers=_SCRYFALL_HEADERS, timeout=15)
            if resp.status_code == 200:
                dest.write_bytes(resp.content)
        except Exception:
            pass
        time.sleep(0.11)  # Scryfall asks for ≤10 req/s


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
