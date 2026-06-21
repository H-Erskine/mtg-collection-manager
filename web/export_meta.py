"""Fetch top N meta decklists from MTGGoldfish and write meta.json.

Run directly to generate the file:
    python -m web.export_meta
    python -m web.export_meta --format modern --format standard --count 30
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

from mtg_manager.config import load_config
from mtg_manager.db import get_conn, get_owned_quantity
from mtg_manager.goldfish import fetch_meta_decklists


def export_meta_static(
    cfg,
    formats: list[str],
    count: int = 30,
) -> None:
    """Fetch meta decklists and write meta.json to cfg.web_static_dir."""
    if cfg.web_static_dir is None:
        print("web_static_dir not set in config — skipping meta export.", file=sys.stderr)
        return

    out_dir = cfg.web_static_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    with get_conn(cfg.db_path) as conn:
        # Build name → best printing lookup for card images
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
                back_lower = r["name"].split(" // ")[1].strip().lower()
                if front_lower not in printing_map:
                    printing_map[front_lower] = (r["set_code"], r["collector_number"])
                if back_lower not in printing_map:
                    printing_map[back_lower] = (r["set_code"], r["collector_number"])

        format_results = []
        for fmt in formats:
            print(f"Fetching {fmt} meta ({count} decks)…")
            try:
                decklists = fetch_meta_decklists(fmt, limit=count, delay=cfg.mtgtop8_delay)
            except Exception as e:
                print(f"[warn] Failed to fetch {fmt} meta: {e}", file=sys.stderr)
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
                    owned_slots += min(owned, qty)
                    printing = printing_map.get(name.lower())
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

    parser = argparse.ArgumentParser(description="Fetch meta decklists and write meta.json")
    parser.add_argument(
        "--format", "-f", dest="formats", action="append",
        default=[], metavar="FORMAT",
        help="Format to fetch (repeatable, default: modern standard)",
    )
    parser.add_argument("--count", "-n", type=int, default=30, help="Decks per format")
    args = parser.parse_args()

    cfg = load_config()
    export_meta_static(cfg, formats=args.formats or ["modern", "standard"], count=args.count)
