"""Live (non-exported) per-user collection/deck data for the web app."""

from datetime import datetime, timezone

from api.users import get_user_config, list_profiles
from mtg_manager.config import Config
from mtg_manager.db import get_conn
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
