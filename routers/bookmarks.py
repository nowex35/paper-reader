"""しおり（PDFごとのブックマーク位置）の CRUD。"""

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from routers._shared import BOOKMARKS_DIR, _valid_id

router = APIRouter(prefix="/api")


class BookmarkItem(BaseModel):
    pageIndex: int
    y: float
    t: int | None = None


class BookmarksIn(BaseModel):
    items: list[BookmarkItem] = []


def _bm_path(pid: str):
    if not _valid_id(pid):
        raise HTTPException(400, "invalid id")
    return BOOKMARKS_DIR / f"{pid}.json"


@router.get("/bookmarks/{pid}")
def get_bookmarks(pid: str):
    path = _bm_path(pid)
    if not path.exists():
        return {"items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"items": []}
    items = data.get("items", []) if isinstance(data, dict) else data
    return {"items": items if isinstance(items, list) else []}


@router.put("/bookmarks/{pid}")
def put_bookmarks(pid: str, payload: BookmarksIn):
    path = _bm_path(pid)
    items = [it.model_dump(exclude_none=True) for it in payload.items]
    if items:
        path.write_text(json.dumps({"items": items}, ensure_ascii=False),
                        encoding="utf-8")
    else:
        path.unlink(missing_ok=True)
    return {"ok": True, "count": len(items)}
