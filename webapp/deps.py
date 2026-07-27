"""FastAPI dependencies for session-based auth."""

from fastapi import HTTPException, Request

from api.users import get_owner_config, get_user_config, is_whitelist_admin
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


def require_user_or_owner(request: Request) -> Config:
    """Like require_user, but anonymous visitors fall back to the site owner's Config.

    Used only for the public-safe views (collection, sale); logged-in users
    still see their own data.
    """
    user_id = request.session.get("user_id")
    if user_id:
        cfg = get_user_config(user_id)
        if cfg is None:
            raise NotAuthenticated()
        return cfg

    cfg = get_owner_config()
    if cfg is None:
        raise NotAuthenticated()
    return cfg


def is_admin_user(user_id: str) -> bool:
    return user_id.startswith("google:") and is_whitelist_admin(user_id.split(":", 1)[1])


def require_admin(request: Request) -> Config:
    cfg = require_user(request)
    user_id = request.session["user_id"]
    if not is_admin_user(user_id):
        raise HTTPException(status_code=403, detail="Admin access required")
    return cfg
