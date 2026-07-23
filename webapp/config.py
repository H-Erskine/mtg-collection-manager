"""Self-service config routes: Moxfield packages, formats, sort order."""

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from api.handlers import handle_sync
from api.users import (
    add_package,
    is_owner,
    is_whitelist_admin,
    list_packages,
    list_profiles,
    mark_auto_synced,
    mark_onboarded,
    mark_synced,
    minutes_since_last_sync,
    remove_package,
    seconds_since_last_auto_sync,
    set_formats,
    set_profile,
    set_sort,
)
from api.users import get_user_config
from mtg_manager.config import Config
from mtg_manager.db import clear_color_group, get_conn, upsert_cards
from mtg_manager.manabox import ManaboxImportError, import_manabox_csv
from mtg_manager.moxfield import public_id_from_url
from webapp.deps import require_user

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


def _is_admin(user_id: str) -> bool:
    return user_id.startswith("google:") and is_whitelist_admin(user_id.split(":", 1)[1])


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
    profile = next((p for p in list_profiles() if p["user_id"] == user_id), {"display_name": "", "icon": ""})
    return {
        "packages": [{"color_group": cg, "public_id": pid} for cg, pid in pkgs],
        "formats": cfg.formats,
        "pick_list_sort": cfg.pick_list_sort,
        "minutes_since_last_sync": minutes_since_last_sync(user_id),
        "is_admin": _is_admin(user_id),
        "display_name": profile["display_name"],
        "icon": profile["icon"],
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
