"""FastAPI app entry point: uvicorn webapp.main:app"""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

load_dotenv()

from api.users import is_onboarded, log_request, seed_owner_whitelist
from mtg_manager.config import Config
from webapp.admin import router as admin_router
from webapp.auth import router as auth_router
from webapp.config import router as config_router
from webapp.data import get_all_collections, get_all_sale, get_collection, get_decks, get_group_collections, get_group_ownership, get_meta, get_sale, pick_art_card
from webapp.deps import NotAuthenticated, is_admin_user, require_admin, require_user, require_user_or_owner
from webapp.images import router as images_router


class CardNeed(BaseModel):
    name: str
    quantity: int


class GroupCheckIn(BaseModel):
    cards: list[CardNeed]
    group_id: int

app = FastAPI()
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET_KEY", "dev-only-insecure-key"),
)
app.include_router(auth_router)
app.include_router(images_router)
app.include_router(config_router)
app.include_router(admin_router)


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/images/"):
        try:
            user_id = request.session.get("user_id")
            log_request(user_id, request.method, request.url.path, response.status_code)
        except Exception:
            pass
    return response


_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="static")


@app.exception_handler(NotAuthenticated)
async def _handle_not_authenticated(request: Request, exc: NotAuthenticated):
    return RedirectResponse(url="/login", status_code=302)


@app.on_event("startup")
async def _on_startup():
    seed_owner_whitelist()


@app.get("/api/collection")
async def api_collection(cfg: Config = Depends(require_user_or_owner)):
    return get_collection(cfg)


@app.get("/api/decks")
async def api_decks(cfg: Config = Depends(require_user)):
    return get_decks(cfg)


@app.get("/api/meta")
async def api_meta(cfg: Config = Depends(require_user)):
    return get_meta(cfg)


class ArtPickIn(BaseModel):
    names: list[str]


@app.post("/api/meta/art-pick")
async def api_meta_art_pick(body: ArtPickIn, cfg: Config = Depends(require_user)):
    return await pick_art_card(body.names)


@app.get("/api/collection/all")
async def api_collection_all(cfg: Config = Depends(require_admin)):
    return get_all_collections(viewer_is_admin=True)


@app.get("/api/collection/group")
async def api_collection_group(request: Request, cfg: Config = Depends(require_user)):
    return get_group_collections(request.session["user_id"])


@app.post("/api/collection/group-check")
async def api_collection_group_check(request: Request, body: GroupCheckIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    card_needs = [{"name": c.name, "quantity": c.quantity} for c in body.cards]
    return {"ownership": get_group_ownership(user_id, card_needs, body.group_id)}


@app.get("/api/sale")
async def api_sale(cfg: Config = Depends(require_user_or_owner)):
    return get_sale(cfg)


@app.get("/api/sale/all")
async def api_sale_all(request: Request, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    return get_all_sale(viewer_is_admin=is_admin_user(user_id))


@app.get("/api/whoami")
async def api_whoami(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return {"authenticated": False}
    return {"authenticated": True, "email": user_id.split(":", 1)[1]}


@app.get("/app")
async def app_page(request: Request, cfg: Config = Depends(require_user_or_owner)):
    user_id = request.session.get("user_id")
    if user_id and not is_onboarded(user_id):
        return RedirectResponse(url="/onboarding", status_code=302)
    return FileResponse(_STATIC_DIR / "app.html")


@app.get("/config")
async def config_page(request: Request, cfg: Config = Depends(require_user)):
    if not is_onboarded(request.session["user_id"]):
        return RedirectResponse(url="/onboarding", status_code=302)
    return FileResponse(_STATIC_DIR / "config.html")


@app.get("/onboarding")
async def onboarding_page(request: Request, cfg: Config = Depends(require_user)):
    if is_onboarded(request.session["user_id"]):
        return RedirectResponse(url="/app", status_code=302)
    return FileResponse(_STATIC_DIR / "onboarding.html")


@app.get("/admin")
async def admin_page(cfg: Config = Depends(require_admin)):
    return FileResponse(_STATIC_DIR / "admin.html")


@app.get("/admin/users/{user_id}")
async def admin_user_detail_page(user_id: str, cfg: Config = Depends(require_admin)):
    return FileResponse(_STATIC_DIR / "admin-user.html")


@app.get("/")
async def root(request: Request):
    return RedirectResponse(url="/app", status_code=302)
