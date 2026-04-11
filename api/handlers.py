"""
Business logic for the WhatsApp API layer.

Each handler mirrors a CLI command but returns a plain text string
instead of printing Rich-formatted output to the terminal.
"""
from __future__ import annotations

from collections import defaultdict

from mtg_manager.config import get_git_commit, load_config
from mtg_manager.db import (
    add_card_tag,
    card_count,
    categorise_missing_cards,
    clear_color_group,
    clear_for_sale_color_group,
    delete_built_deck,
    get_available_quantity,
    get_card_allocations,
    get_card_color_group,
    get_card_tags,
    get_cards_over_limit,
    get_conn,
    get_deck,
    get_deck_by_url,
    get_decks_by_name,
    get_owned_quantity,
    get_tags_for_sale_cards,
    insert_built_deck,
    list_built_decks,
    list_for_sale_cards,
    remove_card_tag,
    update_sale_prices,
    upsert_cards,
    upsert_for_sale_cards,
)
from mtg_manager.models import BoxedCard, MissingCard
from mtg_manager.moxfield import fetch_package_cards
from mtg_manager.prices import fetch_cardmarket_prices
from mtg_manager.sources import fetch_decklists, source_name


def _load_cfg():
    return load_config()


def _format_card_tag(foil: bool, set_code: str, basic: bool = False) -> str:
    """Return a display suffix like ' [foil] (MH3)' for card list lines."""
    parts = []
    if foil:
        parts.append(" [foil]")
    if set_code and not basic:
        parts.append(f" ({set_code.upper()})")
    return "".join(parts)


_SALE_PRICE_RE = __import__("re").compile(r"^\$(\d+(?:\.\d+)?)")


def _is_sale_package(package_name: str) -> bool:
    """Return True if this Moxfield package is a for-sale list (name starts with '$')."""
    return package_name.startswith("$")


def _parse_sale_price(package_name: str) -> float:
    """Extract a numeric price from a package name like '$2.50 Rare Singles', or 0.0."""
    m = _SALE_PRICE_RE.match(package_name)
    return float(m.group(1)) if m else 0.0


def _sync_sale_prices(conn, sale_rows: list) -> None:
    """Fetch CardMarket prices for for-sale cards and persist them. Silent on failure."""
    try:
        price_map = fetch_cardmarket_prices(sale_rows)
    except Exception:
        return
    updates = []
    for row in sale_rows:
        key = (row["set_code"].lower(), row["collector_number"], int(row["foil"]))
        if key in price_map:
            updates.append((row["name"], row["set_code"], row["collector_number"], bool(row["foil"]), price_map[key]))
    if updates:
        update_sale_prices(conn, updates)


def _auto_sync(cfg, conn) -> list[str]:
    """Re-fetch all Moxfield packages. Returns list of warning strings."""
    warnings = []
    sale_rows: list = []
    for pkg in cfg.packages:
        try:
            cards, pkg_name = fetch_package_cards(pkg, delay=cfg.moxfield_delay)
            if _is_sale_package(pkg_name):
                clear_for_sale_color_group(conn, pkg.color_group)
                upsert_for_sale_cards(conn, cards, _parse_sale_price(pkg_name))
            else:
                clear_color_group(conn, pkg.color_group)
                upsert_cards(conn, cards)
        except Exception as e:
            warnings.append(f"Sync warning ({pkg.color_group}): {e}")
    # Fetch prices for all sale cards in one pass after upsert
    try:
        sale_rows = list_for_sale_cards(conn)
        if sale_rows:
            _sync_sale_prices(conn, sale_rows)
    except Exception as e:
        warnings.append(f"Price fetch warning: {e}")
    return warnings


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

def handle_sync(color_group: str | None = None) -> str:
    try:
        cfg = _load_cfg()
    except FileNotFoundError as e:
        return f"Error: {e}"

    packages = cfg.packages
    if color_group:
        packages = [p for p in packages if p.color_group.lower() == color_group.lower()]
        if not packages:
            return f"No package found for color group '{color_group}'."

    lines = []
    synced_sale = False
    with get_conn(cfg.db_path) as conn:
        for pkg in packages:
            try:
                cards, pkg_name = fetch_package_cards(pkg, delay=cfg.moxfield_delay)
            except Exception as e:
                lines.append(f"Failed to fetch {pkg.color_group}: {e}")
                continue
            qty = sum(c.quantity for c in cards)
            if _is_sale_package(pkg_name):
                clear_for_sale_color_group(conn, pkg.color_group)
                upsert_for_sale_cards(conn, cards, _parse_sale_price(pkg_name))
                lines.append(f"{pkg.color_group} [for sale]: {qty} cards ({len(cards)} unique)")
                synced_sale = True
            else:
                clear_color_group(conn, pkg.color_group)
                upsert_cards(conn, cards)
                lines.append(f"{pkg.color_group}: {qty} cards ({len(cards)} unique)")

        if synced_sale:
            try:
                sale_rows = list_for_sale_cards(conn)
                price_map = fetch_cardmarket_prices(sale_rows)
                updates = []
                for row in sale_rows:
                    key = (row["set_code"].lower(), row["collector_number"], int(row["foil"]))
                    if key in price_map:
                        updates.append((row["name"], row["set_code"], row["collector_number"], bool(row["foil"]), price_map[key]))
                if updates:
                    update_sale_prices(conn, updates)
                    lines.append(f"CardMarket prices updated: {len(updates)}/{len(sale_rows)} card(s).")
            except Exception as e:
                lines.append(f"Price fetch failed: {e}")

        total = card_count(conn)

    lines.append(f"\nCollection total: {total} cards")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# missing
# ---------------------------------------------------------------------------

def handle_missing(url: str, sideboard: bool = False, min_variants: int = 1) -> str:
    try:
        cfg = _load_cfg()
    except FileNotFoundError as e:
        return f"Error: {e}"

    try:
        decklists = fetch_decklists(url, delay=cfg.mtgtop8_delay)
    except Exception as e:
        return f"Failed to fetch decklists: {e}"

    if not decklists:
        return "No decklists found at that URL."

    max_needed: dict[str, int] = defaultdict(int)
    variant_count: dict[str, int] = defaultdict(int)
    canonical_name: dict[str, str] = {}
    # per-deck needed map: deck_id -> {lower_name: (name, qty)}
    deck_needed: dict[str, dict[str, tuple[str, int]]] = {}

    for dl in decklists:
        cards = dl.cards if sideboard else dl.maindeck
        deck_needed[dl.deck_id] = {}
        for card in cards:
            key = card.name.lower()
            canonical_name[key] = card.name
            max_needed[key] = max(max_needed[key], card.quantity)
            variant_count[key] += 1
        # always use full 75 (maindeck + sideboard) for have/total counts
        for card in dl.cards:
            key = card.name.lower()
            if key not in deck_needed[dl.deck_id] or card.quantity > deck_needed[dl.deck_id][key][1]:
                deck_needed[dl.deck_id][key] = (card.name, card.quantity)

    keys = [k for k in max_needed if variant_count[k] >= min_variants]

    with get_conn(cfg.db_path) as conn:
        _auto_sync(cfg, conn)
        card_needs = [(canonical_name[k], max_needed[k], variant_count[k]) for k in keys]
        missing_cards, boxed_cards = categorise_missing_cards(conn, card_needs, len(decklists))

        # per-deck have/total
        deck_counts: list[tuple[str, int, int]] = []  # (name, have, total)
        for dl in decklists:
            nm = deck_needed[dl.deck_id]
            total = sum(qty for _, qty in nm.values())
            have = sum(min(get_owned_quantity(conn, name), qty) for _, (name, qty) in nm.items())
            deck_counts.append((dl.name, have, total))

    lines = []
    lines.append(f"{len(decklists)} variant(s):")
    for name, have, total in deck_counts:
        lines.append(f"  {have}/{total}  {name}")
    lines.append("")

    if not missing_cards and not boxed_cards:
        lines.append("You have all the cards and they are available!")
        return "\n".join(lines)

    if missing_cards:
        missing_cards.sort(key=lambda c: (-c.variants, c.name))
        lines.append(f"Missing {len(missing_cards)} card(s) to order:")
        show_variants = len(decklists) > 1
        for c in missing_cards:
            if show_variants:
                lines.append(f"  {c.short}x {c.name}  [{c.variants}/{c.total_variants} variants]")
            else:
                lines.append(f"  {c.short}x {c.name}")

    if boxed_cards:
        lines.append("")
        lines.append("Owned but locked in boxes:")
        for bc in sorted(boxed_cards, key=lambda c: c.name):
            for a in bc.allocations:
                lines.append(f"  {bc.needed}x {bc.name} -> {a.quantity}x in [{a.box_name}] ({a.deck_name})")

    if missing_cards:
        lines.append("")
        lines.append("Order list:")
        for c in missing_cards:
            lines.append(f"{c.short}x {c.name}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def handle_build(url: str, box: str, sideboard: bool = False) -> str:
    try:
        cfg = _load_cfg()
    except FileNotFoundError as e:
        return f"Error: {e}"

    try:
        decklists = fetch_decklists(url, delay=cfg.mtgtop8_delay)
    except Exception as e:
        return f"Failed to fetch decklist: {e}"

    if not decklists:
        return "No decklist found at that URL."

    if len(decklists) > 1:
        return (
            "Compare URLs contain multiple decks — use a single deck URL for build.\n"
            "Tip: click through to a specific deck on MTGTop8 and use that URL."
        )

    dl = decklists[0]

    with get_conn(cfg.db_path) as conn:
        _auto_sync(cfg, conn)

        # Duplicate check: URL is the most reliable identifier across all sources
        existing = get_deck_by_url(conn, url) or get_deck(conn, dl.deck_id)
        if existing:
            return (
                f"Deck '{existing['deck_name']}' is already built and in [{existing['box_name']}].\n"
                f"Send 'unbox {existing['deck_name']}' first to rebuild it."
            )

        cards = dl.cards
        if not cards:
            return f"Deck '{dl.name}' returned no cards — it may be private or empty."

        needed: dict[str, int] = defaultdict(int)
        for card in cards:
            needed[card.name] += card.quantity

        conflict_lines: list[str] = []
        card_entries: list[tuple[str, int, bool]] = []  # (name, qty, is_proxy)

        for name, qty in sorted(needed.items()):
            available = get_available_quantity(conn, name)
            allocs = get_card_allocations(conn, name)
            if allocs:
                for a in allocs:
                    conflict_lines.append(f"  {name}: {a.quantity}x in [{a.box_name}] ({a.deck_name})")
            is_proxy = available < qty
            card_entries.append((name, qty, is_proxy))

        # Build pick list before insert so it's always available
        pick: dict[str, list[str]] = defaultdict(list)
        proxy_lines: list[str] = []
        for name, qty, is_proxy in card_entries:
            if is_proxy:
                proxy_lines.append(f"  {qty}x {name}")
            else:
                group = get_card_color_group(conn, name)
                pick[group].append(f"  {qty}x {name}")

        insert_built_deck(
            conn,
            deck_id=dl.deck_id,
            deck_name=dl.name,
            deck_url=url,
            box_name=box,
            cards=card_entries,
        )

    proxy_count = sum(1 for _, _, p in card_entries if p)
    lines = [f"Built: {dl.name}", f"Box:   {box}"]
    if proxy_count:
        lines.append(f"Proxies: {proxy_count} card(s) not owned — marked in box, not deducted from collection")
    lines.append("")

    if conflict_lines:
        lines.append("Warning — some cards were in other boxes:")
        lines.extend(conflict_lines)
        lines.append("")

    lines.append("Pick list:")
    for group in sorted(pick):
        lines.append(f"[{group}]")
        lines.extend(pick[group])

    if proxy_lines:
        lines.append("[Proxies — print or substitute]")
        lines.extend(proxy_lines)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# boxes
# ---------------------------------------------------------------------------

def handle_boxes() -> str:
    try:
        cfg = _load_cfg()
    except FileNotFoundError as e:
        return f"Error: {e}"

    with get_conn(cfg.db_path) as conn:
        _auto_sync(cfg, conn)
        decks = list_built_decks(conn)

    if not decks:
        return "No decks built yet. Send 'build <url> box <name>' to add one."

    lines = []
    current_box = None
    for row in decks:
        if row["box_name"] != current_box:
            current_box = row["box_name"]
            lines.append(f"\n[{current_box}]")
        source = source_name(row["deck_url"])
        lines.append(f"  {row['deck_name']}  [{source}]")
        lines.append(f"    {row['deck_url']}")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# unbox
# ---------------------------------------------------------------------------

def handle_unbox(deck_name: str) -> str:
    try:
        cfg = _load_cfg()
    except FileNotFoundError as e:
        return f"Error: {e}"

    with get_conn(cfg.db_path) as conn:
        rows = get_decks_by_name(conn, deck_name)
        if not rows:
            return f"No built deck found named '{deck_name}'.\nSend 'boxes' to see built decks."
        if len(rows) > 1:
            lines = [f"Multiple decks named '{deck_name}' found:"]
            for r in rows:
                lines.append(f"  [{r['box_name']}] {r['deck_name']}  (id: {r['deck_id']})")
            return "\n".join(lines)

        row = rows[0]
        name = row["deck_name"]
        box = row["box_name"]
        delete_built_deck(conn, row["deck_id"])

    return f"Unboxed: {name} (was in [{box}])\nCards returned to available pool."


# ---------------------------------------------------------------------------
# forsale
# ---------------------------------------------------------------------------

def handle_forsale(min_price: float = 0.0, show_price: bool = True) -> str:
    try:
        cfg = _load_cfg()
    except FileNotFoundError as e:
        return f"Error: {e}"

    with get_conn(cfg.db_path) as conn:
        rows = list_for_sale_cards(conn)
        tag_map = get_tags_for_sale_cards(conn)

    if not rows:
        return "No cards listed for sale.\nSync a Moxfield package whose name starts with '$<price>' to populate the sale list."

    if min_price:
        rows = [r for r in rows if r["price"] >= min_price]
        if not rows:
            return f"No cards for sale at €{min_price:.2f} or above."

    # Build display rows
    table_rows = []
    for row in rows:
        qty_label = f"{row['quantity']}x " if row["quantity"] > 1 else ""
        tags = tag_map.get((row["name"].lower(), row["set_code"].lower(), row["foil"]), [])
        price_str = f"€{row['price']:.2f}" if row["price"] else "—"
        table_rows.append((
            f"{qty_label}{row['name']}",
            row["set_code"].upper() if row["set_code"] else "",
            "Foil" if row["foil"] else "Non-Foil",
            ", ".join(tags),
            price_str,
        ))

    # Dynamic column widths
    col_name  = max(len(r[0]) for r in table_rows)
    col_set   = max(len(r[1]) for r in table_rows)
    col_fin   = 8  # "Non-Foil"
    col_tags  = max((len(r[3]) for r in table_rows), default=0)
    col_price = max(len(r[4]) for r in table_rows)

    header = f"{'Card':<{col_name}}  {'Set':<{col_set}}  {'Finish':<{col_fin}}"
    if col_tags:
        header += f"  {'Tags':<{col_tags}}"
    if show_price:
        header += f"  {'Price':>{col_price}}"
    sep = "-" * len(header)

    lines = [header, sep]
    for name, set_code, finish, tags, price_str in table_rows:
        line = f"{name:<{col_name}}  {set_code:<{col_set}}  {finish:<{col_fin}}"
        if col_tags:
            line += f"  {tags:<{col_tags}}"
        if show_price:
            line += f"  {price_str:>{col_price}}"
        lines.append(line)

    total = sum(row["quantity"] for row in rows)
    lines.append(f"\n{total} card(s) for sale.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# tag / untag
# ---------------------------------------------------------------------------

def handle_tag(name: str, tag: str, set_code: str = "", foil: bool = False) -> str:
    """Add a tag to a card and return a confirmation string."""
    try:
        cfg = _load_cfg()
    except FileNotFoundError as e:
        return f"Error: {e}"

    with get_conn(cfg.db_path) as conn:
        add_card_tag(conn, name, set_code, foil, tag)
        tags = get_card_tags(conn, name, set_code, foil if set_code else None)

    descriptor = name
    if set_code:
        descriptor += f" ({set_code.upper()})"
    if foil:
        descriptor += " [foil]"
    return f"Tagged: {descriptor}\nTags: {', '.join(tags)}"


def handle_untag(name: str, tag: str, set_code: str = "", foil: bool = False) -> str:
    """Remove a tag from a card and return a confirmation string."""
    try:
        cfg = _load_cfg()
    except FileNotFoundError as e:
        return f"Error: {e}"

    with get_conn(cfg.db_path) as conn:
        removed = remove_card_tag(conn, name, set_code, foil, tag)
        tags = get_card_tags(conn, name, set_code, foil if set_code else None)

    if not removed:
        return f"Tag '{tag}' not found on {name}" + (f" ({set_code.upper()})" if set_code else "") + "."
    remaining = f"Remaining tags: {', '.join(tags)}" if tags else "No tags remaining."
    descriptor = name + (f" ({set_code.upper()})" if set_code else "")
    return f"Removed tag '{tag}' from {descriptor}.\n{remaining}"


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------

def handle_version() -> str:
    commit = get_git_commit()
    if not commit:
        return "Could not determine version (git unavailable)."
    return f"mtg-manager  commit {commit[:7]}  ({commit})"


# ---------------------------------------------------------------------------
# help
# ---------------------------------------------------------------------------

HELP_TEXT = """\
MTG Manager commands:

/sync [color_group]
  Fetch Moxfield packages and update your collection.
  Packages whose Moxfield name starts with $<price> are treated as for-sale
  stock and stored separately (not added to your collection).
  Optionally sync only one color group (e.g. White).

/forsale
  List all cards marked for sale, grouped by price.
  Formatted for easy copy-paste to Discord or WhatsApp.

/missing <url> [min_variants] [sideboard]
  Show cards you need to order for a MTGTop8 deck or compare URL.
  Use min_variants to filter to cards in N+ variants.

/build <url> <box_name> [sideboard]
  Mark a deck as built and allocate its cards to a named box.

/boxes
  List all built decks grouped by box.

/unbox <deck_name>
  Remove a built deck and return its cards to the available pool.
  Deck names are shown in /boxes.

/extras [limit]
  List cards you own more than limit copies of (default 4).
  Useful for identifying trade/sell stock.

/search <query>
  Search your collection for cards matching a name.

/stats
  Show collection stats: total cards, unique cards, breakdown by color group.

/card <name>
  Look up a card on Scryfall. Shows mana cost, type, oracle text, and price.
  Supports fuzzy name matching."""


def handle_help() -> str:
    return HELP_TEXT


# ---------------------------------------------------------------------------
# extras
# ---------------------------------------------------------------------------

def handle_extras(limit: int = 4, basic: bool = False) -> str:
    try:
        cfg = _load_cfg()
    except FileNotFoundError as e:
        return f"Error: {e}"

    with get_conn(cfg.db_path) as conn:
        _auto_sync(cfg, conn)
        rows = get_cards_over_limit(conn, limit)

    if not rows:
        return f"No cards with more than {limit} copies."

    # Aggregate by (name, set_code, foil) — multiple collector numbers can exist per set
    aggregated: dict[str, dict[tuple, dict]] = defaultdict(dict)
    for row in rows:
        name = row["name"]
        key = (row["set_code"], row["foil"])
        if key not in aggregated[name]:
            aggregated[name][key] = {"set_code": row["set_code"], "foil": row["foil"],
                                     "quantity": 0, "color_group": row["color_group"]}
        aggregated[name][key]["quantity"] += row["quantity"]

    # Sort each card's versions by quantity DESC so we fill playset from the largest first
    by_name: dict[str, list] = {
        name: sorted(versions.values(), key=lambda v: v["quantity"], reverse=True)
        for name, versions in aggregated.items()
    }

    excess_lines = []
    for name, versions in sorted(by_name.items()):
        remaining = limit
        for v in versions:
            allocated = min(v["quantity"], remaining)
            remaining -= allocated
            spare = v["quantity"] - allocated
            if spare > 0:
                excess_lines.append(f"  {spare}x {name}{_format_card_tag(v['foil'], v['set_code'], basic)}")

    if not excess_lines:
        return f"No spare cards beyond {limit} copies."

    lines = [f"Spare cards beyond a playset of {limit} ({len(by_name)} card(s) with extras):\n"]
    lines.extend(excess_lines)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def handle_search(query: str) -> str:
    try:
        cfg = _load_cfg()
    except FileNotFoundError as e:
        return f"Error: {e}"

    query_lower = f"%{query.lower()}%"
    with get_conn(cfg.db_path) as conn:
        rows = conn.execute(
            """
            SELECT name, set_code, color_group, foil, quantity
            FROM owned_cards
            WHERE LOWER(name) LIKE ?
            ORDER BY name, quantity DESC, foil
            """,
            (query_lower,),
        ).fetchall()

    if not rows:
        return f'No cards found matching "{query}".'

    by_name: dict[str, list] = defaultdict(list)
    for row in rows:
        by_name[row["name"]].append(row)

    lines = [f'Results for "{query}":']
    for name, versions in by_name.items():
        total = sum(v["quantity"] for v in versions)
        lines.append(f"\n  {name}  ({total} total)")
        for v in versions:
            lines.append(f"    {v['quantity']}x{_format_card_tag(v['foil'], v['set_code'])}  [{v['color_group']}]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

def handle_stats() -> str:
    try:
        cfg = _load_cfg()
    except FileNotFoundError as e:
        return f"Error: {e}"

    with get_conn(cfg.db_path) as conn:
        total = conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) AS t FROM owned_cards"
        ).fetchone()["t"]

        unique = conn.execute(
            "SELECT COUNT(DISTINCT name) AS u FROM owned_cards"
        ).fetchone()["u"]

        by_group = conn.execute(
            """
            SELECT color_group, SUM(quantity) AS total
            FROM owned_cards
            GROUP BY color_group
            ORDER BY total DESC
            """
        ).fetchall()

        built_count = conn.execute(
            "SELECT COUNT(*) AS c FROM built_decks"
        ).fetchone()["c"]

    lines = [
        f"Collection stats:",
        f"  Total cards:   {total}",
        f"  Unique cards:  {unique}",
        f"  Built decks:   {built_count}",
        "",
        "By color group:",
    ]
    for row in by_group:
        group = row["color_group"] or "Unknown"
        lines.append(f"  {group}: {row['total']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# card (Scryfall lookup)
# ---------------------------------------------------------------------------

import requests as _requests


def fetch_card_data(name: str) -> dict | str:
    """
    Fetch a card from Scryfall by fuzzy name.

    Returns a dict with card data on success, or an error string on failure.
    """
    try:
        resp = _requests.get(
            "https://api.scryfall.com/cards/named",
            params={"fuzzy": name},
            timeout=10,
        )
    except Exception as e:
        return f"Failed to reach Scryfall: {e}"

    if resp.status_code == 404:
        data = resp.json()
        return f"Card not found: {data.get('details', name)}"

    if not resp.ok:
        return f"Scryfall error {resp.status_code}"

    c = resp.json()

    oracle = c.get("oracle_text", "")
    if not oracle and "card_faces" in c:
        oracle = "\n//\n".join(
            face.get("oracle_text", "") for face in c["card_faces"]
        )

    prices = c.get("prices", {})

    # Image: prefer the front face for double-faced cards
    image_uris = c.get("image_uris") or (
        c["card_faces"][0].get("image_uris") if "card_faces" in c else {}
    )

    return {
        "name": c["name"],
        "mana_cost": c.get("mana_cost", "") or (
            c["card_faces"][0].get("mana_cost", "") if "card_faces" in c else ""
        ),
        "type_line": c.get("type_line", ""),
        "oracle_text": oracle,
        "colors": c.get("colors", []),
        "price_usd": prices.get("usd"),
        "price_usd_foil": prices.get("usd_foil"),
        "scryfall_uri": c.get("scryfall_uri", ""),
        "image_uri": (image_uris or {}).get("normal", ""),
    }


def handle_card(name: str) -> str:
    data = fetch_card_data(name)
    if isinstance(data, str):
        return data

    lines = [f"**{data['name']}**"]
    if data["mana_cost"]:
        lines.append(f"Mana: {data['mana_cost']}")
    lines.append(f"Type: {data['type_line']}")
    if data["oracle_text"]:
        lines.append(f"\n{data['oracle_text']}")
    if data["price_usd"] or data["price_usd_foil"]:
        price_str = f"${data['price_usd']}" if data["price_usd"] else "—"
        foil_str = f"${data['price_usd_foil']} foil" if data["price_usd_foil"] else ""
        lines.append(f"\nPrice: {price_str}" + (f"  |  {foil_str}" if foil_str else ""))
    lines.append(f"\n{data['scryfall_uri']}")
    return "\n".join(lines)
