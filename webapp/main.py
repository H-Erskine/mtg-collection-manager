"""FastAPI app entry point: uvicorn webapp.main:app"""

import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

load_dotenv()

from api.users import seed_owner_whitelist
from mtg_manager.config import Config
from webapp.auth import router as auth_router
from webapp.data import get_collection, get_decks
from webapp.deps import NotAuthenticated, require_user

app = FastAPI()
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET_KEY", "dev-only-insecure-key"),
)
app.include_router(auth_router)


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
