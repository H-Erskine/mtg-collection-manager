"""Self-service config routes: Moxfield packages, formats, sort order."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.handlers import handle_sync
from api.users import (
    add_package,
    is_whitelist_admin,
    list_packages,
    mark_synced,
    minutes_since_last_sync,
    remove_package,
    set_formats,
    set_sort,
)
from mtg_manager.config import Config
from webapp.deps import require_user

router = APIRouter()

SYNC_THROTTLE_MINUTES = 60


class PackageIn(BaseModel):
    color_group: str
    public_id: str


class SortIn(BaseModel):
    sort_mode: str


class FormatsIn(BaseModel):
    formats: list[str]


def _is_admin(user_id: str) -> bool:
    return user_id.startswith("google:") and is_whitelist_admin(user_id.split(":", 1)[1])


@router.get("/api/config")
async def get_config(request: Request, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    pkgs = list_packages(user_id)
    return {
        "packages": [{"color_group": cg, "public_id": pid} for cg, pid in pkgs],
        "formats": cfg.formats,
        "pick_list_sort": cfg.pick_list_sort,
        "minutes_since_last_sync": minutes_since_last_sync(user_id),
        "is_admin": _is_admin(user_id),
    }


@router.post("/api/config/packages")
async def add_config_package(request: Request, body: PackageIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    add_package(user_id, body.color_group, body.public_id)
    return {"ok": True}


@router.delete("/api/config/packages/{color_group}")
async def remove_config_package(request: Request, color_group: str, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    remove_package(user_id, color_group)
    return {"ok": True}


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


@router.post("/api/config/sync")
def sync_now(request: Request, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    mins = minutes_since_last_sync(user_id)
    if mins is not None and mins < SYNC_THROTTLE_MINUTES:
        remaining = int(SYNC_THROTTLE_MINUTES - mins)
        raise HTTPException(
            status_code=429,
            detail=f"Already synced {int(mins)} min ago. Try again in {remaining} min.",
        )

    message = handle_sync(cfg, is_owner=False)
    mark_synced(user_id)
    return {"message": message}
