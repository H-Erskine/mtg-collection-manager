"""Shared, lazily-populated disk cache for Scryfall card images."""

import os
import re
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Query
from starlette.responses import FileResponse, Response

IMAGE_CACHE_DIR = Path("~/mtg_data/image_cache").expanduser()

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_-]+$")
_ALLOWED_VERSIONS = {"small", "normal", "large", "png", "art_crop", "border_crop"}

router = APIRouter()


@router.get("/images/{set_code}/{collector_number}")
async def get_card_image(set_code: str, collector_number: str, version: str = Query("normal")):
    safe_cn = collector_number.replace("/", "_")

    if not _SAFE_COMPONENT.match(set_code) or not _SAFE_COMPONENT.match(safe_cn):
        raise HTTPException(status_code=400, detail="Invalid set code or collector number")
    if version not in _ALLOWED_VERSIONS:
        raise HTTPException(status_code=400, detail="Invalid image version")

    ext = "png" if version == "png" else "jpg"
    cache_dir = IMAGE_CACHE_DIR / set_code if version == "normal" else IMAGE_CACHE_DIR / version / set_code
    cache_path = cache_dir / f"{safe_cn}.{ext}"
    if cache_path.exists():
        return FileResponse(
            cache_path,
            media_type=f"image/{ext}" if ext == "png" else "image/jpeg",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    url = (
        f"https://api.scryfall.com/cards/{quote(set_code, safe='')}"
        f"/{quote(collector_number, safe='')}?format=image&version={quote(version, safe='')}"
    )
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "mtg-manager/1.0 (personal collection site)"})
    except httpx.HTTPError:
        raise HTTPException(status_code=404, detail="Card image not found")

    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="Card image not found")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp_path.write_bytes(resp.content)
    os.replace(tmp_path, cache_path)

    return Response(
        content=resp.content,
        media_type=f"image/{ext}" if ext == "png" else "image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
