"""Google OAuth login/callback/logout routes."""

import os

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, RedirectResponse

from api.users import ensure_user, is_whitelisted, log_failed_login

router = APIRouter()

oauth = OAuth()
oauth.register(
    name="google",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
    client_kwargs={"scope": "openid email"},
)


@router.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for("auth_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        log_failed_login("", "oauth_error")
        return RedirectResponse(url="/login", status_code=302)
    userinfo = token.get("userinfo") or {}
    email = (userinfo.get("email") or "").lower()

    if not email or not is_whitelisted(email):
        log_failed_login(email, "not_whitelisted")
        return HTMLResponse(
            "<h1>Not authorized</h1><p>This email is not on the whitelist. "
            "Contact the owner.</p>",
            status_code=403,
        )

    user_id = f"google:{email}"
    ensure_user(user_id)
    request.session["user_id"] = user_id
    return RedirectResponse(url="/app", status_code=302)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)
