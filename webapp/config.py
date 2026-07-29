"""Self-service config routes: Moxfield packages, formats, sort order."""

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from api.handlers import handle_sync
from api.users import (
    add_group_member,
    add_package,
    create_group,
    delete_group,
    get_cardmarket_url,
    get_profiles_by_ids,
    is_owner,
    is_private,
    list_groups,
    list_packages,
    list_profiles,
    mark_auto_synced,
    mark_onboarded,
    mark_synced,
    minutes_since_last_sync,
    remove_group_member,
    remove_package,
    rename_group,
    seconds_since_last_auto_sync,
    set_cardmarket_url,
    set_formats,
    set_privacy,
    set_profile,
    set_sort,
    _display_profile,
)
from api.users import get_user_config
from mtg_manager.config import Config
from mtg_manager.db import clear_color_group, get_conn, upsert_cards
from mtg_manager.manabox import ManaboxImportError, import_manabox_csv
from mtg_manager.moxfield import public_id_from_url
from webapp.deps import is_admin_user, require_user

router = APIRouter()

SYNC_THROTTLE_MINUTES = 60
AUTO_SYNC_THROTTLE_SECONDS = 60


class PackageIn(BaseModel):
    color_group: str
    public_id: str


class SortIn(BaseModel):
    sort_mode: str


class FormatsIn(BaseModel):
    formats: list[str]


class ProfileIn(BaseModel):
    display_name: str
    icon: str


class PrivacyIn(BaseModel):
    is_private: bool


class CardmarketIn(BaseModel):
    cardmarket_url: str


class GroupIn(BaseModel):
    name: str


class GroupMemberIn(BaseModel):
    member_user_id: str


def _trigger_auto_sync(user_id: str) -> dict:
    """Fire an auto-sync for this user's Moxfield packages, throttled to once per
    AUTO_SYNC_THROTTLE_SECONDS. Reloads Config fresh so a just-added/removed
    package is reflected (the cfg passed into the route was fetched before the
    mutation)."""
    seconds = seconds_since_last_auto_sync(user_id)
    if seconds is not None and seconds < AUTO_SYNC_THROTTLE_SECONDS:
        return {"synced": False, "retry_after_seconds": int(AUTO_SYNC_THROTTLE_SECONDS - seconds) or 1}

    fresh_cfg = get_user_config(user_id)
    if fresh_cfg is None:
        return {"synced": False, "retry_after_seconds": 0}

    message = handle_sync(fresh_cfg, is_owner=is_owner(user_id))
    mark_auto_synced(user_id)
    mark_synced(user_id)
    return {"synced": True, "message": message}


@router.get("/api/config")
async def get_config(request: Request, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    pkgs = list_packages(user_id)
    own = get_profiles_by_ids({user_id})
    profile = own[0] if own else {"display_name": user_id, "icon": "🂠"}
    return {
        "packages": [{"color_group": cg, "public_id": pid} for cg, pid in pkgs],
        "formats": cfg.formats,
        "pick_list_sort": cfg.pick_list_sort,
        "minutes_since_last_sync": minutes_since_last_sync(user_id),
        "is_admin": is_admin_user(user_id),
        "display_name": profile["display_name"],
        "icon": profile["icon"],
        "is_private": is_private(user_id),
        "cardmarket_url": get_cardmarket_url(user_id),
        "groups": list_groups(user_id),
    }


@router.post("/api/config/packages")
def add_config_package(request: Request, body: PackageIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    public_id = public_id_from_url(body.public_id) or body.public_id.strip()
    add_package(user_id, body.color_group, public_id)
    return {"ok": True, "auto_sync": _trigger_auto_sync(user_id)}


@router.delete("/api/config/packages/{color_group}")
def remove_config_package(request: Request, color_group: str, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    remove_package(user_id, color_group)
    return {"ok": True, "auto_sync": _trigger_auto_sync(user_id)}


@router.post("/api/config/manabox")
async def upload_manabox_csv(request: Request, file: UploadFile = File(...), cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded text")

    try:
        cards = await run_in_threadpool(import_manabox_csv, text)
    except ManaboxImportError as e:
        raise HTTPException(status_code=400, detail=str(e))

    with get_conn(cfg.db_path) as conn:
        clear_color_group(conn, "manabox")
        upsert_cards(conn, cards)

    mark_auto_synced(user_id)
    mark_synced(user_id)
    return {"ok": True, "imported": len(cards)}


@router.post("/api/config/sort")
async def set_config_sort(request: Request, body: SortIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    try:
        set_sort(user_id, body.sort_mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.post("/api/config/formats")
async def set_config_formats(request: Request, body: FormatsIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    set_formats(user_id, body.formats)
    return {"ok": True}


@router.post("/api/config/profile")
async def set_config_profile(request: Request, body: ProfileIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    set_profile(user_id, body.display_name, body.icon)
    return {"ok": True}


@router.post("/api/config/privacy")
async def set_config_privacy(request: Request, body: PrivacyIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    set_privacy(user_id, body.is_private)
    return {"ok": True}


@router.post("/api/config/cardmarket")
async def set_config_cardmarket(request: Request, body: CardmarketIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    set_cardmarket_url(user_id, body.cardmarket_url)
    return {"ok": True}


@router.post("/api/config/sync")
def sync_now(request: Request, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    owner = is_owner(user_id)

    if not cfg.packages:
        raise HTTPException(
            status_code=400,
            detail="No Moxfield packages added yet. Add at least one package before syncing.",
        )

    if not owner:
        mins = minutes_since_last_sync(user_id)
        if mins is not None and mins < SYNC_THROTTLE_MINUTES:
            remaining = int(SYNC_THROTTLE_MINUTES - mins)
            raise HTTPException(
                status_code=429,
                detail=f"Already synced {int(mins)} min ago. Try again in {remaining} min.",
            )

    message = handle_sync(cfg, is_owner=owner)
    mark_synced(user_id)
    return {"message": message}


@router.post("/api/onboarding/complete")
async def complete_onboarding(request: Request, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    mark_onboarded(user_id)
    return {"ok": True}


@router.get("/api/users/directory")
async def users_directory(request: Request, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    people = [
        p for p in list_profiles(viewer_is_admin=is_admin_user(user_id))
        if p["user_id"] != user_id
    ]
    return {"people": [_display_profile(p) for p in people]}


@router.post("/api/config/groups")
async def create_config_group(request: Request, body: GroupIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    try:
        group_id = create_group(user_id, body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "id": group_id}


@router.patch("/api/config/groups/{group_id}")
async def rename_config_group(request: Request, group_id: int, body: GroupIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    try:
        ok = rename_group(user_id, group_id, body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Group not found.")
    return {"ok": True}


@router.delete("/api/config/groups/{group_id}")
async def delete_config_group(request: Request, group_id: int, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    if not delete_group(user_id, group_id):
        raise HTTPException(status_code=404, detail="Group not found.")
    return {"ok": True}


@router.post("/api/config/groups/{group_id}/members")
async def add_config_group_member(request: Request, group_id: int, body: GroupMemberIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    if not add_group_member(user_id, group_id, body.member_user_id):
        raise HTTPException(status_code=404, detail="Group not found.")
    return {"ok": True}


@router.delete("/api/config/groups/{group_id}/members/{member_user_id}")
async def remove_config_group_member(request: Request, group_id: int, member_user_id: str, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    remove_group_member(user_id, group_id, member_user_id)
    return {"ok": True}
