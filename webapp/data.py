"""Live (non-exported) per-user collection/deck data for the web app."""

from datetime import datetime, timezone

from api.users import get_user_config, list_group_members, list_profiles, _display_profile
from mtg_manager.config import Config
from mtg_manager.db import get_conn, get_owned_quantity
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


def get_all_sale() -> dict:
    """Combined multi-person for-sale/extras/wants view: every registered user's
    cards in each bucket, tagged with their owner identity. A user whose config
    can't be resolved (or whose DB can't be read) is skipped rather than failing
    the whole request.
    """
    people = list_profiles()
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


def get_all_collections() -> dict:
    """Combined multi-person collection view: every registered user's cards,
    tagged with their owner identity. A user whose config can't be resolved
    (or whose DB can't be read) is skipped rather than failing the whole request.
    """
    people = list_profiles()
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


def get_group_ownership(user_id: str, card_needs: list[dict]) -> dict[str, list[dict]]:
    """For each {"name","quantity"} need, return which of the caller's group
    members (never the caller themself) own at least 1 copy, and how many."""
    members = list_group_members(user_id)
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
    """Combined collection view scoped to the caller plus their personal group,
    instead of every registered user (see get_all_collections). Same
    skip-on-failure resilience: a member whose config/DB can't be read is
    dropped rather than failing the whole request."""
    member_ids = {user_id} | {m["user_id"] for m in list_group_members(user_id)}
    people = [_display_profile(p) for p in list_profiles() if p["user_id"] in member_ids]
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
