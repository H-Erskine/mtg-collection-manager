"""Admin-only routes: user roster, failed logins, activity log, whitelist management."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.users import (
    add_whitelisted_email,
    list_failed_logins,
    list_request_log,
    list_users,
    remove_whitelisted_user,
)
from mtg_manager.config import Config
from webapp.deps import require_admin

router = APIRouter()


class WhitelistIn(BaseModel):
    email: str
    is_admin: bool = False


@router.get("/api/admin/users")
async def get_users(cfg: Config = Depends(require_admin)):
    return {"users": list_users()}


@router.get("/api/admin/failed-logins")
async def get_failed_logins(limit: int = 200, cfg: Config = Depends(require_admin)):
    return {"failed_logins": list_failed_logins(limit=limit)}


@router.get("/api/admin/activity")
async def get_activity(limit: int = 200, cfg: Config = Depends(require_admin)):
    return {"activity": list_request_log(limit=limit)}


@router.get("/api/admin/whitelist")
async def list_whitelist(cfg: Config = Depends(require_admin)):
    from api.users import _registry_conn  # reuse existing connection helper

    with _registry_conn() as conn:
        rows = conn.execute(
            "SELECT email, is_admin, added_at FROM whitelisted_emails ORDER BY added_at"
        ).fetchall()
    return {
        "whitelist": [
            {"email": r["email"], "is_admin": bool(r["is_admin"]), "added_at": r["added_at"]}
            for r in rows
        ]
    }


@router.post("/api/admin/whitelist")
async def add_to_whitelist(body: WhitelistIn, cfg: Config = Depends(require_admin)):
    add_whitelisted_email(body.email, is_admin=body.is_admin)
    return {"ok": True}


@router.delete("/api/admin/whitelist/{email}")
async def remove_from_whitelist(email: str, cfg: Config = Depends(require_admin)):
    try:
        remove_whitelisted_user(email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}
