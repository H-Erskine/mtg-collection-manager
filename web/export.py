import json
import os
from datetime import datetime, timezone
from pathlib import Path

from mtg_manager.config import Config
from mtg_manager.db import get_conn


def export_static(cfg: Config) -> None:
    """Write collection.json and decks.json to cfg.web_static_dir. No-op if unset."""
    if cfg.web_static_dir is None:
        return

    out_dir = cfg.web_static_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    with get_conn(cfg.db_path) as conn:
        updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        collection_data = {"updated_at": updated_at, "cards": _get_collection(conn)}
        decks_data = {"updated_at": updated_at, "decks": _get_decks(conn)}

    _write_json(out_dir / "collection.json", collection_data)
    _write_json(out_dir / "decks.json", decks_data)


def _get_collection(conn) -> list[dict]:
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


def _get_decks(conn) -> list[dict]:
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


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
