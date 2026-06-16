"""
Business logic for the Discord bot layer.

Each handler accepts a pre-resolved ``cfg: Config`` (and ``is_owner: bool``
where auto-sync behaviour differs) instead of loading the config itself.
The bot resolves both from the user registry before calling handlers.

The CLI (mtg_manager/cli.py) is a fully independent implementation and
does not call these handlers.
"""
from __future__ import annotations

import logging
from collections import defaultdict

log = logging.getLogger(__name__)

from mtg_manager.config import Config
from mtg_manager.db import (
    add_card_tag,
    add_moxfield_tag,
    card_count,
    categorise_missing_cards,
    clear_all_for_sale_cards,
    clear_color_group,
    clear_for_sale_color_group,
    clear_wants_cards,
    delete_built_deck,
    get_all_owned_names,
    get_card_binder_color_group,
    get_card_moxfield_tags,
    get_cards_by_moxfield_tag,
    get_deck_return_list,
    get_available_quantity,
    get_card_allocations,
    get_card_cmc,
    get_card_color_group,
    get_card_set_code,
    get_card_tags,
    get_cards_by_tag,
    get_cards_over_limit,
    get_conn,
    get_deck,
    get_deck_by_url,
    get_decks_by_name,
    get_illegal_owned_cards,
    get_names_missing_legality,
    get_for_sale_names,
    get_owned_by_names,
    get_owned_by_printings,
    get_owned_quantity,
    get_tagged_names,
    get_tags_for_sale_cards,
    insert_built_deck,
    list_built_decks,
    list_for_sale_cards,
    remove_all_cards_with_tag,
    remove_card_tag,
    remove_moxfield_tag,
    update_sale_prices,
    upsert_cards,
    upsert_for_sale_cards,
    upsert_wants_cards,
    upsert_legalities,
)
from mtg_manager.models import BoxedCard, MissingCard
from mtg_manager.moxfield import fetch_moxfield_card_ids, fetch_package_cards
from mtg_manager.moxfield_write import deck_name_to_tag, fetch_deck_info, get_token, set_card_tags
from mtg_manager.prices import fetch_cardmarket_prices
from mtg_manager.scryfall import fetch_legalities, search_scryfall_by_query
from mtg_manager.sources import fetch_decklists, source_name


def _format_card_tag(foil: bool, set_code: str, basic: bool = False) -> str:
    parts = []
    if foil:
        parts.append(" [foil]")
    if set_code and not basic:
        parts.append(f" ({set_code.upper()})")
    return "".join(parts)


_SALE_PRICE_RE = __import__("re").compile(r"^\$(\d+(?:\.\d+)?)")


def _is_sale_package(package_name: str) -> bool:
    return package_name.startswith("$")


def _is_wants_package(package_name: str) -> bool:
    return package_name.strip() == "Wants"


def _parse_sale_price(package_name: str) -> float:
    m = _SALE_PRICE_RE.match(package_name)
    return float(m.group(1)) if m else 0.0


def _sync_sale_prices(conn, sale_rows: list) -> None:
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


def _auto_sync(cfg: Config, conn) -> list[str]:
    """Silently re-fetch all Moxfield packages. Only touches owned_cards/for_sale_cards/wants_cards, never box tables. Returns list of warning strings."""
    warnings = []
    sale_rows: list = []
    for pkg in cfg.packages:
        try:
            cards, pkg_name = fetch_package_cards(pkg, delay=cfg.moxfield_delay)
            if _is_sale_package(pkg_name):
                clear_color_group(conn, pkg.color_group)
                clear_all_for_sale_cards(conn)
                upsert_for_sale_cards(conn, cards, _parse_sale_price(pkg_name))
            elif _is_wants_package(pkg_name):
                # Only one "Wants" package per user — safe to clear the whole table
                clear_wants_cards(conn)
                upsert_wants_cards(conn, cards)
            else:
                clear_color_group(conn, pkg.color_group)
                upsert_cards(conn, cards)
        except Exception as e:
            warnings.append(f"Sync warning ({pkg.color_group}): {e}")
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

def handle_sync(cfg: Config, is_owner: bool = False, color_group: str | None = None) -> str:
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
                clear_color_group(conn, pkg.color_group)
                clear_all_for_sale_cards(conn)
                upsert_for_sale_cards(conn, cards, _parse_sale_price(pkg_name))
                lines.append(f"{pkg.color_group} [for sale]: {qty} cards ({len(cards)} unique)")
                synced_sale = True
            elif _is_wants_package(pkg_name):
                clear_wants_cards(conn)
                upsert_wants_cards(conn, cards)
                lines.append(f"{pkg.color_group} [wants]: {qty} cards ({len(cards)} unique)")
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

        if cfg.formats:
            names = get_names_missing_legality(conn, cfg.formats)
            if names:
                try:
                    legality_map = fetch_legalities(names, cfg.formats)
                    upsert_legalities(conn, legality_map)
                    lines.append(f"Legality: {len(legality_map)}/{len(names)} card(s) updated.")
                except Exception as e:
                    lines.append(f"Legality fetch failed: {e}")

        total = card_count(conn)

    lines.append(f"\nCollection total: {total} cards")

    try:
        from web.export import export_static
        export_static(cfg)
    except Exception as e:
        lines.append(f"Web export failed: {e}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# missing
# ---------------------------------------------------------------------------

def handle_missing(url: str, cfg: Config, is_owner: bool = False, sideboard: bool = True, min_variants: int = 1) -> str:
    try:
        decklists = fetch_decklists(url, delay=cfg.mtgtop8_delay)
    except Exception as e:
        return f"Failed to fetch decklists: {e}"

    if not decklists:
        return "No decklists found at that URL."

    max_needed: dict[str, int] = defaultdict(int)
    variant_count: dict[str, int] = defaultdict(int)
    canonical_name: dict[str, str] = {}
    deck_needed: dict[str, dict[str, tuple[str, int]]] = {}

    for dl in decklists:
        cards = dl.cards if sideboard else dl.maindeck
        deck_needed[dl.deck_id] = {}
        for card in cards:
            key = card.name.lower()
            canonical_name[key] = card.name
            max_needed[key] = max(max_needed[key], card.quantity)
            variant_count[key] += 1
            if key not in deck_needed[dl.deck_id] or card.quantity > deck_needed[dl.deck_id][key][1]:
                deck_needed[dl.deck_id][key] = (card.name, card.quantity)

    keys = [k for k in max_needed if variant_count[k] >= min_variants]

    with get_conn(cfg.db_path) as conn:
        if is_owner:
            _auto_sync(cfg, conn)
        card_needs = [(canonical_name[k], max_needed[k], variant_count[k]) for k in keys]
        missing_cards, boxed_cards, available_cards = categorise_missing_cards(conn, card_needs, len(decklists))

        deck_counts: list[tuple[str, int, int]] = []
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
        if available_cards:
            lines.append("")
            lines.append(f"Owned ({len(available_cards)} card(s)):")
            for c in sorted(available_cards, key=lambda c: c.name):
                lines.append(f"  {c.needed}x {c.name}  (have {c.owned})")
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

    if available_cards:
        lines.append("")
        lines.append(f"Already owned ({len(available_cards)} card(s)):")
        for c in sorted(available_cards, key=lambda c: c.name):
            lines.append(f"  {c.needed}x {c.name}  (have {c.owned})")

    if missing_cards:
        lines.append("")
        lines.append("Order list:")
        for c in missing_cards:
            lines.append(f"{c.short}x {c.name}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# proxy — stock vs flex analysis
# ---------------------------------------------------------------------------

import re as _re_proxy


def handle_proxy(urls_str: str, cfg: Config, is_owner: bool = False, threshold: int = 75, sideboard: bool = False) -> str:
    urls = [u for u in _re_proxy.split(r"[\s,]+", urls_str.strip()) if u]
    if not urls:
        return "Error: no URLs provided."

    all_decklists = []
    for url in urls:
        try:
            dls = fetch_decklists(url, delay=cfg.mtgtop8_delay)
            all_decklists.extend(dls)
        except Exception as e:
            return f"Failed to fetch {url}: {e}"

    if not all_decklists:
        return "No decklists found at the provided URLs."

    total = len(all_decklists)

    list_count: dict[str, int] = defaultdict(int)
    modal_qty: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    canonical: dict[str, str] = {}

    for dl in all_decklists:
        cards = dl.cards if sideboard else dl.maindeck
        deck_totals: dict[str, int] = defaultdict(int)
        for card in cards:
            key = card.name.lower()
            canonical[key] = card.name
            deck_totals[key] += card.quantity
        for key, qty in deck_totals.items():
            list_count[key] += 1
            modal_qty[key][qty] += 1

    def _modal(key: str) -> int:
        return max(modal_qty[key], key=lambda q: (modal_qty[key][q], q))

    threshold_count = max(1, round(total * threshold / 100))

    stock_keys = sorted(
        [k for k, n in list_count.items() if n >= threshold_count],
        key=lambda k: (-list_count[k], -_modal(k), k),
    )
    flex_keys = sorted(
        [k for k, n in list_count.items() if n < threshold_count],
        key=lambda k: (-list_count[k], -_modal(k), k),
    )

    lines = [
        f"Proxy analysis: {total} list(s)  |  stock >= {threshold}% ({threshold_count}/{total} lists)",
        "",
    ]

    for dl in all_decklists:
        lines.append(f"  {dl.name}")
    lines.append("")

    if stock_keys:
        lines.append(f"Stock — {len(stock_keys)} card(s) (core, in {threshold_count}+ lists):")
        for k in stock_keys:
            qty = _modal(k)
            n = list_count[k]
            all_qtys = modal_qty[k]
            if len(all_qtys) > 1:
                qty_range = f"{min(all_qtys)}–{max(all_qtys)}x"
                lines.append(f"  {qty}x {canonical[k]}  [{n}/{total}]  (varies {qty_range})")
            else:
                lines.append(f"  {qty}x {canonical[k]}  [{n}/{total}]")
    else:
        lines.append("No cards meet the stock threshold.")

    lines.append("")

    if flex_keys:
        lines.append(f"Flex slots — {len(flex_keys)} card(s) (appear in <{threshold_count} lists):")
        for k in flex_keys:
            qty = _modal(k)
            n = list_count[k]
            lines.append(f"  {qty}x {canonical[k]}  [{n}/{total}]")
    else:
        lines.append("No flex slots — all cards are stock.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def handle_build(url: str, box: str, cfg: Config, is_owner: bool = False, sideboard: bool = False) -> str:
    from urllib.parse import urlparse as _urlparse
    if _urlparse(url).scheme not in ("http", "https"):
        return "Invalid deck URL — only http/https links are accepted."
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
        if is_owner:
            _auto_sync(cfg, conn)

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
        card_entries: list[tuple[str, int, bool]] = []

        for name, qty in sorted(needed.items()):
            available = get_available_quantity(conn, name)
            allocs = get_card_allocations(conn, name)
            if allocs:
                for a in allocs:
                    conflict_lines.append(f"  {name}: {a.quantity}x in [{a.box_name}] ({a.deck_name})")
            is_proxy = available < qty
            card_entries.append((name, qty, is_proxy))

        pick: dict[str, list[str]] = defaultdict(list)
        cmc_entries: list[tuple[float, str]] = []
        proxy_lines: list[str] = []
        sort_mode = cfg.pick_list_sort
        for name, qty, is_proxy in card_entries:
            if is_proxy:
                proxy_lines.append(f"  {qty}x {name}")
            elif sort_mode == "set":
                group = get_card_set_code(conn, name)
                pick[group].append(f"  {qty}x {name}")
            elif sort_mode == "alphabetical":
                pick[""].append(f"  {qty}x {name}")
            elif sort_mode == "cmc":
                cmc = get_card_cmc(conn, name)
                cmc_entries.append((cmc, f"  {qty}x {name}"))
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

    # Moxfield tagging — only for Moxfield deck URLs
    _BASIC_LANDS = {"forest", "island", "mountain", "plains", "swamp", "wastes"}
    tag_summary: list[str] = []
    token = get_token()
    if token and "moxfield.com/decks/" in url:
        tag_name = deck_name_to_tag(dl.name)
        try:
            card_ids = fetch_moxfield_card_ids(url)
        except Exception as e:
            card_ids = {}
            tag_summary.append(f"Warning: could not fetch card IDs for Moxfield tagging: {e}")
        if card_ids:
            tagged = failed = skipped = 0
            binder_info_cache: dict[str, tuple[str, int]] = {}
            with get_conn(cfg.db_path) as conn:
                for name, qty, is_proxy in card_entries:
                    if name.lower() in _BASIC_LANDS:
                        continue
                    card_id = card_ids.get(name.lower())
                    if not card_id:
                        skipped += 1
                        continue
                    color_group = get_card_binder_color_group(conn, name)
                    if not color_group:
                        skipped += 1
                        continue
                    pkg = next((p for p in cfg.packages if p.color_group.lower() == color_group.lower()), None)
                    if not pkg:
                        skipped += 1
                        continue
                    if pkg.public_id not in binder_info_cache:
                        try:
                            binder_info_cache[pkg.public_id] = fetch_deck_info(pkg.public_id, token)
                        except Exception:
                            skipped += 1
                            continue
                    binder_internal_id, binder_version = binder_info_cache[pkg.public_id]
                    existing = get_card_moxfield_tags(conn, card_id, pkg.public_id)
                    if tag_name not in existing:
                        if set_card_tags(binder_internal_id, card_id, existing + [tag_name], token, binder_version, pkg.public_id):
                            add_moxfield_tag(conn, card_id, pkg.public_id, tag_name)
                            tagged += 1
                        else:
                            failed += 1
            if tagged:
                tag_summary.append(f"Tagged {tagged} card(s) with '{tag_name}' in Moxfield")
            if failed:
                tag_summary.append(f"Warning: {failed} card(s) failed to tag in Moxfield (token may be expired)")
            if skipped and not tagged and not failed:
                tag_summary.append(f"Warning: Moxfield tagging skipped — token expired or binder fetch failed")
    lines = [f"Built: {dl.name}", f"Box:   {box}"]
    if proxy_count:
        lines.append(f"Proxies: {proxy_count} card(s) not owned — marked in box, not deducted from collection")
    lines.append("")

    if conflict_lines:
        lines.append("Warning — some cards were in other boxes:")
        lines.extend(conflict_lines)
        lines.append("")

    lines.append("Pick list:")
    if cfg.pick_list_sort == "alphabetical":
        for card in sorted(pick[""]):
            lines.append(card)
    elif cfg.pick_list_sort == "cmc":
        for _, card in sorted(cmc_entries, key=lambda x: (x[0], x[1])):
            lines.append(card)
    else:
        for group in sorted(pick):
            lines.append(f"[{group}]")
            for card in sorted(pick[group]):
                lines.append(card)

    if proxy_lines:
        lines.append("[Proxies — print or substitute]")
        lines.extend(proxy_lines)

    if tag_summary:
        lines.append("")
        lines.extend(tag_summary)

    try:
        from web.export import export_static
        export_static(cfg)
    except Exception as e:
        lines.append(f"Web export failed: {e}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# boxes
# ---------------------------------------------------------------------------

def handle_boxes(cfg: Config, is_owner: bool = False) -> str:
    with get_conn(cfg.db_path) as conn:
        if is_owner:
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

def handle_unbox(deck_name: str, cfg: Config, is_owner: bool = False) -> str:
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
        deck_id = row["deck_id"]
        return_cards = get_deck_return_list(conn, deck_id)
        token = get_token()
        tag_name = deck_name_to_tag(name)
        cards_to_untag = get_cards_by_moxfield_tag(conn, tag_name) if token else []
        delete_built_deck(conn, deck_id)

    untag_summary: list[str] = []
    if token and cards_to_untag:
        untagged = failed = 0
        binder_info_cache: dict[str, tuple[str, int]] = {}
        with get_conn(cfg.db_path) as conn:
            for card_id, binder_public_id in cards_to_untag:
                if binder_public_id not in binder_info_cache:
                    try:
                        binder_info_cache[binder_public_id] = fetch_deck_info(binder_public_id, token)
                    except Exception:
                        failed += 1
                        continue
                binder_internal_id, binder_version = binder_info_cache[binder_public_id]
                remaining = [t for t in get_card_moxfield_tags(conn, card_id, binder_public_id) if t.lower() != tag_name.lower()]
                if set_card_tags(binder_internal_id, card_id, remaining, token, binder_version, binder_public_id):
                    remove_moxfield_tag(conn, card_id, binder_public_id, tag_name)
                    untagged += 1
                else:
                    failed += 1
        if untagged:
            untag_summary.append(f"Removed tag '{tag_name}' from {untagged} card(s) in Moxfield")
        if failed:
            untag_summary.append(f"Warning: {failed} card(s) failed to untag in Moxfield")

    lines = [f"Unboxed: {name} (was in [{box}])", "", "Return to collection:"]
    sort_mode = cfg.pick_list_sort

    owned = [(r["card_name"], r["quantity"], r["is_proxy"], r["color_group"], r["cmc"])
             for r in return_cards if not r["is_proxy"]]
    proxies = [(r["card_name"], r["quantity"]) for r in return_cards if r["is_proxy"]]

    if sort_mode == "cmc":
        for card_name, qty, _, color_group, cmc in sorted(owned, key=lambda x: (x[4], x[0])):
            loc = f"[{color_group}] " if color_group else ""
            lines.append(f"  {qty}x {card_name}  {loc}cmc {int(cmc) if cmc == int(cmc) else cmc}")
    elif sort_mode == "alphabetical":
        for card_name, qty, _, color_group, cmc in sorted(owned, key=lambda x: x[0]):
            loc = f"[{color_group}] " if color_group else ""
            lines.append(f"  {qty}x {card_name}  {loc}cmc {int(cmc) if cmc == int(cmc) else cmc}")
    else:
        groups: dict[str, list[tuple]] = defaultdict(list)
        for card_name, qty, _, color_group, cmc in owned:
            groups[color_group or "?"].append((card_name, qty, cmc))
        sort_key = (lambda x: (x[2], x[0])) if sort_mode == "set" else (lambda x: x[0])
        for group in sorted(groups):
            lines.append(f"[{group}]")
            for card_name, qty, cmc in sorted(groups[group], key=sort_key):
                lines.append(f"  {qty}x {card_name}  cmc {int(cmc) if cmc == int(cmc) else cmc}")

    if proxies:
        lines.append("[Proxies — discard]")
        for card_name, qty in sorted(proxies):
            lines.append(f"  {qty}x {card_name}")

    if untag_summary:
        lines.append("")
        lines.extend(untag_summary)

    try:
        from web.export import export_static
        export_static(cfg)
    except Exception as e:
        lines.append(f"Web export failed: {e}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# forsale
# ---------------------------------------------------------------------------

def handle_forsale(cfg: Config, is_owner: bool = False, min_price: float = 0.0, show_price: bool = True, fmt: str | None = None) -> str:
    legal_formats = [fmt] if fmt else None
    with get_conn(cfg.db_path) as conn:
        rows = list_for_sale_cards(conn, legal_formats=legal_formats)
        tag_map = get_tags_for_sale_cards(conn)

    if not rows:
        return "No cards listed for sale.\nSync a Moxfield package whose name starts with '$<price>' to populate the sale list."

    if min_price:
        rows = [r for r in rows if r["price"] >= min_price]
        if not rows:
            return f"No cards for sale at €{min_price:.2f} or above."

    lines: list[str] = []
    for row in rows:
        qty_label = f"{row['quantity']}x " if row["quantity"] > 1 else ""
        set_label = f" ({row['set_code'].upper()})" if row["set_code"] else ""
        finish = "Foil" if row["foil"] else "Non-Foil"
        tags = tag_map.get((row["name"].lower(), row["set_code"].lower(), row["foil"]), [])

        parts = [f"{qty_label}{row['name']}{set_label}", finish]
        if tags:
            parts.append(", ".join(tags))
        if show_price:
            parts.append(f"€{row['price']:.2f}" if row["price"] else "—")
        lines.append(" | ".join(parts))

    total = sum(row["quantity"] for row in rows)
    lines.append(f"\n{total} card(s) for sale.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# forsale_csv
# ---------------------------------------------------------------------------

def handle_forsale_csv(cfg: Config, is_owner: bool = False) -> bytes | str:
    """Return a CSV of for-sale cards grouped by set (price desc within set).

    Returns bytes on success, or an error string if there are no cards.
    """
    import csv
    import io

    with get_conn(cfg.db_path) as conn:
        rows = list_for_sale_cards(conn)
        tag_map = get_tags_for_sale_cards(conn)

    if not rows:
        return "No cards listed for sale — nothing to export."

    sorted_rows = sorted(rows, key=lambda r: (r["set_code"].upper(), -(r["price"] or 0.0)))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Set", "Card", "Finish", "Tags", "Quantity", "Price (EUR)"])
    for row in sorted_rows:
        finish = "Foil" if row["foil"] else "Non-Foil"
        tags = tag_map.get((row["name"].lower(), row["set_code"].lower(), row["foil"]), [])
        price_str = f"{row['price']:.2f}" if row["price"] else ""
        writer.writerow([
            row["set_code"].upper(),
            row["name"],
            finish,
            ", ".join(tags),
            row["quantity"],
            price_str,
        ])

    return buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# tag / untag
# ---------------------------------------------------------------------------

def handle_tag(name: str, tag: str, cfg: Config, is_owner: bool = False, set_code: str = "", foil: bool = False) -> str:
    with get_conn(cfg.db_path) as conn:
        add_card_tag(conn, name, set_code, foil, tag)
        tags = get_card_tags(conn, name, set_code, foil if set_code else None)

    descriptor = name
    if set_code:
        descriptor += f" ({set_code.upper()})"
    if foil:
        descriptor += " [foil]"
    return f"Tagged: {descriptor}\nTags: {', '.join(tags)}"


def handle_untag(name: str, tag: str, cfg: Config, is_owner: bool = False, set_code: str = "", foil: bool = False) -> str:
    with get_conn(cfg.db_path) as conn:
        removed = remove_card_tag(conn, name, set_code, foil, tag)
        tags = get_card_tags(conn, name, set_code, foil if set_code else None)

    if not removed:
        return f"Tag '{tag}' not found on {name}" + (f" ({set_code.upper()})" if set_code else "") + "."
    remaining = f"Remaining tags: {', '.join(tags)}" if tags else "No tags remaining."
    descriptor = name + (f" ({set_code.upper()})" if set_code else "")
    return f"Removed tag '{tag}' from {descriptor}.\n{remaining}"


def handle_tagged(tag: str, cfg: Config, is_owner: bool = False) -> str:
    with get_conn(cfg.db_path) as conn:
        rows = get_cards_by_tag(conn, tag)

    if not rows:
        return f"No cards tagged '{tag}'."

    lines = [f"Cards tagged '{tag}':"]
    for row in rows:
        line = row["name"]
        if row["set_code"]:
            line += f" ({row['set_code'].upper()})"
        if row["foil"]:
            line += " [foil]"
        lines.append(line)
    return "\n".join(lines)


def handle_cleartag(tag: str, cfg: Config, is_owner: bool = False) -> str:
    with get_conn(cfg.db_path) as conn:
        count = remove_all_cards_with_tag(conn, tag)

    if count == 0:
        return f"No cards were tagged '{tag}'."
    noun = "card" if count == 1 else "cards"
    return f"Cleared tag '{tag}' from {count} {noun}."


# ---------------------------------------------------------------------------
# version / help
# ---------------------------------------------------------------------------

from mtg_manager.config import get_git_commit


def handle_version() -> str:
    commit = get_git_commit()
    if not commit:
        return "Could not determine version (git unavailable)."
    return f"mtg-manager  commit {commit[:7]}  ({commit})"


HELP_TEXT = """\
MTG Manager commands:

/sync [color_group]
  Fetch Moxfield packages and update your collection.
  Packages whose Moxfield name starts with $<price> are treated as for-sale
  stock and stored separately (not added to your collection).
  Optionally sync only one color group (e.g. White).
  Non-owner users: rate-limited to once per 60 minutes.

/forsale
  List all cards marked for sale, grouped by price.

/forsale_csv
  Download a CSV of your for-sale list grouped by set, for CardMarket listing.

/missing <url> [min_variants] [sideboard]
  Show cards you need to order for a MTGTop8 deck or compare URL.

/build <url> <box_name> [sideboard]
  Mark a deck as built and allocate its cards to a named box.

/boxes
  List all built decks grouped by box.

/unbox <deck_name>
  Remove a built deck and return its cards to the available pool.

/extras [limit]
  List cards you own more than limit copies of (default 4).

/search <query>
  Search your collection for cards matching a name.

/stats
  Show collection stats: total cards, unique cards, breakdown by color group.

/card <name>
  Look up a card on Scryfall. Shows mana cost, type, oracle text, and price.

/setup
  Register yourself to use the bot (first-time setup).

/addpackage <color_group> <public_id>
  Add a Moxfield package to your account.

/removepackage <color_group>
  Remove a Moxfield package from your account.

/listpackages
  Show your registered Moxfield packages.

/setsort <mode>
  Set your pick list sort order (colour, alphabetical, set, cmc).

/setformats <formats>
  Set comma-separated tracked formats (e.g. modern,legacy)."""


def handle_help() -> str:
    return HELP_TEXT


# ---------------------------------------------------------------------------
# extras
# ---------------------------------------------------------------------------

def handle_extras(cfg: Config, is_owner: bool = False, limit: int = 4, basic: bool = False, fmt: str | None = None) -> str:
    legal_formats = [fmt] if fmt else None
    with get_conn(cfg.db_path) as conn:
        if is_owner:
            _auto_sync(cfg, conn)
        rows = get_cards_over_limit(conn, limit, legal_formats=legal_formats)

    if not rows:
        return f"No cards with more than {limit} copies."

    try:
        price_map = fetch_cardmarket_prices(rows)
    except Exception as exc:
        log.warning("fetch_cardmarket_prices failed in handle_extras: %s", exc)
        price_map = {}

    aggregated: dict[str, dict[tuple, dict]] = defaultdict(dict)
    for row in rows:
        name = row["name"]
        key = (row["set_code"], row["collector_number"], row["foil"])
        if key not in aggregated[name]:
            aggregated[name][key] = {
                "set_code": row["set_code"],
                "collector_number": row["collector_number"],
                "foil": row["foil"],
                "quantity": 0,
                "color_group": row["color_group"],
            }
        aggregated[name][key]["quantity"] += row["quantity"]

    by_name: dict[str, list] = {
        name: sorted(versions.values(), key=lambda v: v["quantity"], reverse=True)
        for name, versions in aggregated.items()
    }

    spare_entries: list[tuple[float, str]] = []
    for name, versions in sorted(by_name.items()):
        remaining = limit
        for v in versions:
            allocated = min(v["quantity"], remaining)
            remaining -= allocated
            spare = v["quantity"] - allocated
            if spare > 0:
                pk = (v["set_code"].lower(), v["collector_number"], int(v["foil"]))
                price = price_map.get(pk, 0.0)
                price_str = f" €{price:.2f}" if price else ""
                tag = _format_card_tag(v["foil"], v["set_code"], basic)
                spare_entries.append((price, f"  {spare}x {name}{tag}{price_str}"))

    if not spare_entries:
        return f"No spare cards beyond {limit} copies."

    spare_entries.sort(key=lambda x: -x[0])
    lines = [f"Spare cards beyond a playset of {limit} ({len(by_name)} card(s) with extras):\n"]
    lines.extend(line for _, line in spare_entries)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# illegal
# ---------------------------------------------------------------------------

def handle_illegal(cfg: Config, is_owner: bool = False, fmt: str | None = None) -> str:
    active_formats = [fmt] if fmt else cfg.formats
    if not active_formats:
        return (
            "No formats configured.\n"
            "Add [formats] tracked = [\"modern\", \"standard\"] to config.toml, "
            "or pass a format argument."
        )

    with get_conn(cfg.db_path) as conn:
        rows = get_illegal_owned_cards(conn, active_formats)

    if not rows:
        return f"All owned cards are legal in: {', '.join(active_formats)}"

    lines = [f"Cards illegal in all of: {', '.join(active_formats)}\n"]
    for row in rows:
        foil = " [foil]" if row["foil"] else ""
        set_label = f" ({row['set_code'].upper()})" if row["set_code"] else ""
        lines.append(f"  {row['quantity']}x {row['name']}{set_label}{foil}")
    lines.append(f"\n{len(rows)} card(s) total.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def handle_search(query: str, cfg: Config, is_owner: bool = False) -> str:
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
# scryfall (collection cross-reference)
# ---------------------------------------------------------------------------

def handle_scryfall(
    url: str,
    cfg: Config,
    is_owner: bool = False,
    alt_printings: bool = False,
) -> str:
    from mtg_manager.scryfall import search_scryfall

    try:
        cards = search_scryfall(url)
    except Exception as e:
        return f"Error fetching Scryfall results: {e}"

    if not cards:
        return "No cards found for that Scryfall search."

    printings = [(c["set"], c["collector_number"]) for c in cards]
    artist_by_printing: dict[tuple[str, str], str] = {
        (c["set"].lower(), c["collector_number"]): c.get("artist", "Unknown")
        for c in cards
    }
    # card name → list of artist printings (for alt_printings display)
    name_to_artist_prints: dict[str, list[dict]] = {}
    for c in cards:
        name_to_artist_prints.setdefault(c["name"], []).append({
            "artist": c.get("artist", "Unknown"),
            "set": c["set"].upper(),
            "collector_number": c["collector_number"],
        })

    with get_conn(cfg.db_path) as conn:
        for_sale = get_for_sale_names(conn)
        tagged = get_tagged_names(conn)
        owned_rows = [
            r for r in get_owned_by_printings(conn, printings)
            if r["name"].lower() not in for_sale
        ]
        alt_rows: list = []
        if alt_printings:
            exact_names = {r["name"].lower() for r in owned_rows}
            alt_names = [n for n in name_to_artist_prints if n.lower() not in exact_names]
            if alt_names:
                alt_rows = get_owned_by_names(conn, alt_names)

    # by_artist: artist → list of (is_tagged, is_alt, name, display_line)
    # sort: tagged+exact → tagged+alt → untagged+exact → untagged+alt
    by_artist: dict[str, list[tuple[bool, bool, str, str]]] = {}

    for row in owned_rows:
        artist = artist_by_printing.get((row["set_code"].lower(), row["collector_number"]), "Unknown")
        foil_label = " [foil]" if row["foil"] else ""
        is_tagged = row["name"].lower() in tagged
        line = f"{row['quantity']}x {row['name']} - {row['set_code'].upper()}{foil_label}"
        by_artist.setdefault(artist, []).append((is_tagged, False, row["name"], line))

    for row in alt_rows:
        name = row["name"]
        artist_prints = name_to_artist_prints.get(name, [])
        by_ap: dict[str, list[str]] = {}
        for ap in artist_prints:
            by_ap.setdefault(ap["artist"], []).append(f"{ap['set']} #{ap['collector_number']}")
        for artist, ver_list in by_ap.items():
            foil_label = " [foil]" if row["foil"] else ""
            is_tagged = name.lower() in tagged
            vers = ", ".join(ver_list)
            line = (
                f"{row['quantity']}x {name} - {row['set_code'].upper()}{foil_label}"
                f"  [diff. printing — artist ver: {vers}]"
            )
            by_artist.setdefault(artist, []).append((is_tagged, True, name, line))

    if not by_artist:
        suffix = " (including other printings of matching cards)" if alt_printings else ""
        return f"Found {len(cards)} Scryfall result(s) but none{suffix} are in your collection."

    summary = f"Scryfall results: {len(cards)} card(s)  |  Owned: {len(owned_rows)} exact printing(s)"
    if alt_printings and alt_rows:
        summary += f"  |  Alt printings: {len(alt_rows)}"
    lines = [summary, ""]

    for artist in sorted(by_artist):
        lines.append(artist)
        for is_tagged, _is_alt, _name, line in sorted(by_artist[artist], key=lambda x: (not x[0], x[1], x[2])):
            prefix = "★ " if is_tagged else "  "
            lines.append(f"{prefix}{line}")
        lines.append("")

    total_qty = sum(r["quantity"] for r in owned_rows) + sum(r["quantity"] for r in alt_rows)
    total_prints = len(owned_rows) + len(alt_rows)
    lines.append(f"Total: {total_qty} cop{'y' if total_qty == 1 else 'ies'} across {total_prints} printing(s)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# check — playset completeness via Scryfall query
# ---------------------------------------------------------------------------

def handle_check(query: str, cfg: Config, is_owner: bool = False) -> str:
    try:
        card_names = search_scryfall_by_query(query)
    except Exception as e:
        return f"Error fetching Scryfall results: {e}"

    if not card_names:
        return f"No cards found for query '{query}'."

    card_needs = [(name, 4, 1) for name in card_names]

    with get_conn(cfg.db_path) as conn:
        if is_owner:
            _auto_sync(cfg, conn)
        missing_cards, boxed_cards, available_cards = categorise_missing_cards(conn, card_needs, 1)

    lines = [f"{len(card_names)} card(s) found for '{query}'.\n"]

    if not missing_cards and not boxed_cards:
        lines.append("You have a full playset of all cards!")
        for c in sorted(available_cards, key=lambda c: c.name):
            lines.append(f"  4x {c.name}  (have {c.owned})")
        return "\n".join(lines)

    if missing_cards:
        lines.append(f"Missing {len(missing_cards)} card(s):")
        for c in sorted(missing_cards, key=lambda c: c.name):
            lines.append(f"  {c.short}x {c.name}  (have {c.owned})")

    if boxed_cards:
        lines.append("\nOwned but locked in boxes:")
        for bc in sorted(boxed_cards, key=lambda c: c.name):
            for a in bc.allocations:
                lines.append(f"  {bc.needed}x {bc.name} -> {a.quantity}x in [{a.box_name}] ({a.deck_name})")

    if available_cards:
        lines.append(f"\nFull playset ({len(available_cards)} card(s)):")
        for c in sorted(available_cards, key=lambda c: c.name):
            lines.append(f"  4x {c.name}  (have {c.owned})")

    if missing_cards:
        lines.append("\nOrder list:")
        for c in sorted(missing_cards, key=lambda c: c.name):
            lines.append(f"{c.short}x {c.name}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

def handle_stats(cfg: Config, is_owner: bool = False) -> str:
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
        "Collection stats:",
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
# meta — top N meta decks vs collection
# ---------------------------------------------------------------------------

def handle_meta(format_name: str, count: int, cfg: Config, is_owner: bool = False) -> tuple[str, list]:
    """Returns (text, deck_results) where deck_results is [(dl, dm, total, owned), ...]."""
    from mtg_manager.goldfish import fetch_meta_decklists

    try:
        decklists = fetch_meta_decklists(format_name, limit=count, delay=cfg.mtgtop8_delay)
    except Exception as e:
        return f"Failed to fetch meta: {e}", []

    if not decklists:
        return f"No meta decks found for '{format_name}'.", []

    with get_conn(cfg.db_path) as conn:
        if is_owner:
            _auto_sync(cfg, conn)

        deck_results = []
        for dl in decklists:
            card_totals: dict[str, int] = defaultdict(int)
            for card in dl.cards:  # includes sideboard
                card_totals[card.name] += card.quantity

            total_slots = sum(card_totals.values())
            card_needs_list = [(name, qty, 1) for name, qty in card_totals.items()]
            dm, db, av = categorise_missing_cards(conn, card_needs_list, 1)

            owned_slots = (
                sum(mc.owned for mc in dm)
                + sum(bc.needed for bc in db)
                + sum(ac.needed for ac in av)
            )
            deck_results.append((dl, dm, total_slots, owned_slots))

    deck_results.sort(key=lambda x: -(x[3] / x[2] if x[2] else 0))

    buildable = sum(1 for _, dm, _, _ in deck_results if not dm)
    lines = [
        f"Top {len(decklists)} {format_name.capitalize()} meta — last 30 days (MTGGoldfish)",
        f"Buildable: {buildable}/{len(decklists)}",
        "",
    ]

    for dl, dm, total, owned in deck_results:
        pct = round(owned / total * 100) if total else 0
        to_buy = sum(mc.short for mc in dm)
        if not dm:
            icon = "✅"
            suffix = ""
        elif pct >= 80:
            icon = "🟡"
            suffix = f"  — {to_buy} to buy"
        else:
            icon = "🔴"
            suffix = f"  — {to_buy} to buy"
        lines.append(f"{icon} {dl.name}: {owned}/{total} ({pct}%){suffix}  [link](<{dl.url}>)")

    # max shortage per card across all decks — buying this many covers any single deck
    agg_short: dict[str, int] = {}
    agg_decks: dict[str, int] = defaultdict(int)
    canonical: dict[str, str] = {}

    for dl, dm, total, owned in deck_results:
        for mc in dm:
            key = mc.name.lower()
            canonical[key] = mc.name
            agg_short[key] = max(agg_short.get(key, 0), mc.short)
            agg_decks[key] += 1

    if agg_short:
        top = sorted(agg_short.keys(), key=lambda k: (-agg_decks[k], -agg_short[k], k))[:15]
        lines.append("")
        lines.append(f"Most needed (across {len(decklists)} decks):")
        for k in top:
            lines.append(f"  {agg_short[k]}x {canonical[k]}  ({agg_decks[k]}/{len(decklists)} decks)")

    return "\n".join(lines), deck_results


# ---------------------------------------------------------------------------
# card (Scryfall lookup) — no cfg needed
# ---------------------------------------------------------------------------

import requests as _requests


def fetch_card_data(name: str) -> dict | str:
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
