"""FastAPI dependencies for session-based auth."""

from fastapi import Request

from api.users import get_user_config
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
