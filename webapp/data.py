"""Live (non-exported) per-user collection/deck data for the web app."""

from collections import defaultdict
from datetime import datetime, timezone

from api.users import (
    all_group_member_ids,
    get_profiles_by_ids,
    get_user_config,
    group_owner,
    list_group_members,
    list_profiles,
)
from mtg_manager.config import Config
from mtg_manager.db import get_conn, get_meta_decks, get_owned_quantity
from web.export import get_collection_data, get_decks_data, get_sale_data


def get_collection(cfg: Config) -> dict:
    with get_conn(cfg.db_path) as conn:
        cards = get_collection_data(conn)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cards": cards,
    }


def get_decks(cfg: Config) -> dict:
    with get_conn(cfg.db_path) as conn:
        decks = get_decks_data(conn)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "decks": decks,
    }


def get_sale(cfg: Config) -> dict:
    with get_conn(cfg.db_path) as conn:
        sale = get_sale_data(conn)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **sale,
    }


def get_all_sale(viewer_is_admin: bool = False) -> dict:
    """Combined multi-person for-sale/extras/wants view: every registered user's
    cards in each bucket, tagged with their owner identity. A user whose config
    can't be resolved (or whose DB can't be read) is skipped rather than failing
    the whole request. Private accounts are omitted unless the viewer is an admin.
    """
    people = list_profiles(viewer_is_admin)
    for_sale: list[dict] = []
    extras: list[dict] = []
    wants: list[dict] = []

    for person in people:
        cfg = get_user_config(person["user_id"])
        if cfg is None:
            continue
        try:
            with get_conn(cfg.db_path) as conn:
                person_sale = get_sale_data(conn)
        except Exception:
            continue

        owner_tag = {
            "owner_user_id": person["user_id"],
            "owner_display_name": person["display_name"],
            "owner_icon": person["icon"],
        }
        for_sale.extend({**card, **owner_tag} for card in person_sale["for_sale"])
        extras.extend({**card, **owner_tag} for card in person_sale["extras"])
        wants.extend({**card, **owner_tag} for card in person_sale["wants"])

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "people": people,
        "for_sale": for_sale,
        "extras": extras,
        "wants": wants,
    }


def get_all_collections(viewer_is_admin: bool = False) -> dict:
    """Combined multi-person collection view: every registered user's cards,
    tagged with their owner identity. A user whose config can't be resolved
    (or whose DB can't be read) is skipped rather than failing the whole request.
    Private accounts are omitted unless the viewer is an admin.
    """
    people = list_profiles(viewer_is_admin)
    cards: list[dict] = []

    for person in people:
        cfg = get_user_config(person["user_id"])
        if cfg is None:
            continue
        try:
            with get_conn(cfg.db_path) as conn:
                person_cards = get_collection_data(conn)
        except Exception:
            continue
        for card in person_cards:
            cards.append({
                **card,
                "owner_user_id": person["user_id"],
                "owner_display_name": person["display_name"],
                "owner_icon": person["icon"],
            })

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "people": people,
        "cards": cards,
    }


def get_meta(cfg: Config) -> dict:
    """Live per-request deck-vs-collection comparison, ported from the batch
    logic in web/export_meta.py (which only runs as a static export job).
    No EUR pricing here by design -- meta decklists carry only card names
    (from MTGGoldfish), not Scryfall IDs, so pricing would need its own
    name-keyed cache; deferred to a later task.
    Also unlike the old static exporter, this does not do diacritic-folded
    name matching (e.g. "Lorien Revealed" vs "Lórien Revealed") -- it relies
    solely on get_owned_quantity's double-faced-name handling. Porting the
    old _normalize/normalized_owned machinery is a deliberate scope gap,
    deferred to a later task.
    set_code/collector_number are always None here -- exact owned printing
    isn't looked up (that's a second full-table-scan query per card, and
    doubled this endpoint's DB cost). The frontend falls back to fetching
    art by name from Scryfall's /cards/named endpoint when both are None,
    same as it does for any other card without a known printing."""
    format_results = []
    with get_conn(cfg.db_path) as conn:
        for fmt in cfg.formats:
            decklists = get_meta_decks(conn, fmt)
            if not decklists:
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
                    cards.append({
                        "name": name,
                        "quantity": qty,
                        "owned": owned,
                        "set_code": None,
                        "collector_number": None,
                    })

                cards.sort(key=lambda c: (c["owned"] >= c["quantity"], c["name"]))

                decks.append({
                    "name": dl.name,
                    "url": dl.url,
                    "meta_share": dl.meta_share,
                    "total_slots": total_slots,
                    "owned_slots": owned_slots,
                    "cards": cards,
                })

            if any(d["meta_share"] > 0 for d in decks):
                decks.sort(key=lambda d: -d["meta_share"])
            else:
                decks.sort(key=lambda d: -(d["owned_slots"] / d["total_slots"]) if d["total_slots"] else 0)

            format_results.append({"format": fmt, "decks": decks})

    return {"formats": format_results}


def get_group_ownership(user_id: str, card_needs: list[dict], group_id: int) -> dict[str, list[dict]]:
    """For each {"name","quantity"} need, return which members of the caller's
    chosen group (never the caller themself) own at least 1 copy, and how many.
    Returns empty results if group_id doesn't belong to user_id."""
    if group_owner(group_id) != user_id:
        return {}
    members = list_group_members(group_id)
    result: dict[str, list[dict]] = {}

    for person in members:
        cfg = get_user_config(person["user_id"])
        if cfg is None:
            continue
        try:
            with get_conn(cfg.db_path) as conn:
                for need in card_needs:
                    owned = get_owned_quantity(conn, need["name"])
                    if owned > 0:
                        result.setdefault(need["name"], []).append({
                            "owner_user_id": person["user_id"],
                            "owner_display_name": person["display_name"],
                            "owner_icon": person["icon"],
                            "owned": owned,
                        })
        except Exception:
            continue

    return result


def get_group_collections(user_id: str) -> dict:
    """Combined collection view scoped to the caller plus the union of members
    across all of their groups, instead of every registered user (see
    get_all_collections). Same skip-on-failure resilience: a member whose
    config/DB can't be read is dropped rather than failing the whole request."""
    member_ids = {user_id} | all_group_member_ids(user_id)
    people = get_profiles_by_ids(member_ids)
    cards: list[dict] = []

    for person in people:
        cfg = get_user_config(person["user_id"])
        if cfg is None:
            continue
        try:
            with get_conn(cfg.db_path) as conn:
                person_cards = get_collection_data(conn)
        except Exception:
            continue
        for card in person_cards:
            cards.append({
                **card,
                "owner_user_id": person["user_id"],
                "owner_display_name": person["display_name"],
                "owner_icon": person["icon"],
            })

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "people": people,
        "cards": cards,
    }
