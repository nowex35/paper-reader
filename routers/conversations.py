"""会話履歴（解説・質問カード）の CRUD。"""

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from routers._shared import CONV_DIR, _valid_id

router = APIRouter(prefix="/api")


class ConvItem(BaseModel):
    type: str  # "explain" | "ask"
    src: str
    body: str


class ConvIn(BaseModel):
    items: list[ConvItem] = []


def _conv_path(pid: str):
    if not _valid_id(pid):
        raise HTTPException(400, "invalid id")
    return CONV_DIR / f"{pid}.json"


@router.get("/conversations/{pid}")
def get_conversations(pid: str):
    path = _conv_path(pid)
    if not path.exists():
        return {"items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"items": []}
    items = data.get("items", []) if isinstance(data, dict) else []
    return {"items": items if isinstance(items, list) else []}


@router.put("/conversations/{pid}")
def put_conversations(pid: str, payload: ConvIn):
    path = _conv_path(pid)
    items = [it.model_dump() for it in payload.items]
    if items:
        path.write_text(json.dumps({"items": items}, ensure_ascii=False),
                        encoding="utf-8")
    else:
        path.unlink(missing_ok=True)
    return {"ok": True, "count": len(items)}
