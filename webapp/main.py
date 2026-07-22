"""FastAPI app entry point: uvicorn webapp.main:app"""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

load_dotenv()

from api.users import get_user_config, log_request, seed_owner_whitelist
from mtg_manager.config import Config
from webapp.auth import router as auth_router
from webapp.config import router as config_router
from webapp.data import get_all_collections, get_collection, get_decks
from webapp.deps import NotAuthenticated, require_user
from webapp.images import router as images_router

app = FastAPI()
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET_KEY", "dev-only-insecure-key"),
)
app.include_router(auth_router)
app.include_router(images_router)
app.include_router(config_router)


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/images/"):
        user_id = request.session.get("user_id")
        try:
            log_request(user_id, request.method, request.url.path, response.status_code)
        except Exception:
            pass
    return response


_STATIC_DIR = Path(__file__).parent / "static"


@app.exception_handler(NotAuthenticated)
async def _handle_not_authenticated(request: Request, exc: NotAuthenticated):
    return RedirectResponse(url="/login", status_code=302)


@app.on_event("startup")
async def _on_startup():
    seed_owner_whitelist()


@app.get("/api/collection")
async def api_collection(cfg: Config = Depends(require_user)):
    return get_collection(cfg)


@app.get("/api/decks")
async def api_decks(cfg: Config = Depends(require_user)):
    return get_decks(cfg)


@app.get("/api/collection/all")
async def api_collection_all(cfg: Config = Depends(require_user)):
    return get_all_collections()


@app.get("/api/whoami")
async def api_whoami(request: Request, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    return {"email": user_id.split(":", 1)[1]}


@app.get("/app")
async def app_page(cfg: Config = Depends(require_user)):
    return FileResponse(_STATIC_DIR / "app.html")


@app.get("/config")
async def config_page(cfg: Config = Depends(require_user)):
    return FileResponse(_STATIC_DIR / "config.html")


@app.get("/")
async def root(request: Request):
    user_id = request.session.get("user_id")
    if user_id and get_user_config(user_id) is not None:
        return RedirectResponse(url="/app", status_code=302)
    return RedirectResponse(url="/login", status_code=302)
