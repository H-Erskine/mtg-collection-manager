"""FastAPI dependencies for session-based auth."""

from fastapi import HTTPException, Request

from api.users import get_user_config, is_whitelist_admin
from mtg_manager.config import Config


class NotAuthenticated(Exception):
    """Raised when a request has no valid session; caught by a handler in webapp/main.py."""


def require_user(request: Request) -> Config:
    user_id = request.session.get("user_id")
    if not user_id:
        raise NotAuthenticated()

    cfg = get_user_config(user_id)
    if cfg is None:
        raise NotAuthenticated()

    return cfg


def require_admin(request: Request) -> Config:
    cfg = require_user(request)
    user_id = request.session["user_id"]
    is_admin = user_id.startswith("google:") and is_whitelist_admin(user_id.split(":", 1)[1])
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return cfg
