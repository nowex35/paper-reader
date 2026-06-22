"""メモ（論文ごとの自分用まとめ）の CRUD。"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from routers._shared import BOOKMARKS_DIR, CONV_DIR, NOTES_DIR, PDF_DIR, _valid_id

router = APIRouter(prefix="/api")

FM_KEYS = ["id", "title", "pdf", "created", "updated"]
SUMMARY_MARK = "<!--paper-reader:summary-->"


class NoteIn(BaseModel):
    title: str = "Untitled"
    body: str = ""
    pdf: str | None = None


def _strip_summary(full: str) -> str:
    idx = (full or "").find(SUMMARY_MARK)
    return (full[:idx] if idx >= 0 else full or "").rstrip()


def _slugify(title: str) -> str:
    s = re.sub(r"\s+", "-", title.strip().lower())
    s = re.sub(r"[^\w\-]", "", s, flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:60] or "note"


def _find(nid: str):
    matches = sorted(NOTES_DIR.glob(f"*-{nid}.md"))
    return matches[0] if matches else None


def _parse(path: Path):
    raw = path.read_text(encoding="utf-8")
    meta, body = {}, raw
    if raw.startswith("---"):
        lines = raw.split("\n")
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            for ln in lines[1:end]:
                if ": " in ln:
                    k, v = ln.split(": ", 1)
                    v = v.strip()
                    if v.startswith('"') and v.endswith('"'):
                        try:
                            v = json.loads(v)
                        except json.JSONDecodeError:
                            v = v[1:-1]
                    meta[k.strip()] = v
                elif ln.endswith(":"):
                    meta[ln[:-1].strip()] = ""
            body = "\n".join(lines[end + 1:]).lstrip("\n")
    return meta, body


def _fm_value(value) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _write(path: Path, meta: dict, body: str):
    fm = "---\n" + "".join(f"{k}: {_fm_value(meta.get(k, ''))}\n" for k in FM_KEYS) + "---\n\n"
    path.write_text(fm + body.rstrip() + "\n", encoding="utf-8")


@router.get("/notes")
def list_notes():
    out = []
    for p in NOTES_DIR.glob("*.md"):
        m, b = _parse(p)
        memo = _strip_summary(b)
        out.append({
            "id": m.get("id") or p.stem.rsplit("-", 1)[-1],
            "title": m.get("title") or p.stem,
            "pdf": m.get("pdf", ""),
            "updated": m.get("updated", ""),
            "snippet": re.sub(r"\s+", " ", memo.strip())[:120],
        })
    out.sort(key=lambda x: x["updated"], reverse=True)
    return out


@router.get("/notes/{nid}")
def get_note(nid: str):
    if not _valid_id(nid):
        raise HTTPException(400, "invalid id")
    p = _find(nid)
    if not p:
        raise HTTPException(404, "note not found")
    m, b = _parse(p)
    memo = _strip_summary(b)
    return {
        "id": nid, "title": m.get("title", ""), "pdf": m.get("pdf", ""),
        "created": m.get("created", ""), "updated": m.get("updated", ""),
        "body": memo, "filename": p.name,
    }


@router.put("/notes/{nid}")
def save_note(nid: str, note: NoteIn):
    if not _valid_id(nid):
        raise HTTPException(400, "invalid id")
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    existing = _find(nid)
    created, pdf = now, (note.pdf or "")
    if existing:
        m, _ = _parse(existing)
        created = m.get("created") or now
        pdf = note.pdf or m.get("pdf", "")
    title = (note.title or "Untitled").strip() or "Untitled"
    new_path = NOTES_DIR / f"{_slugify(title)}-{nid}.md"
    if existing and existing != new_path:
        existing.unlink(missing_ok=True)
    meta = {"id": nid, "title": title, "pdf": pdf, "created": created, "updated": now}
    _write(new_path, meta, (note.body or "").rstrip())
    return {"id": nid, "title": title, "pdf": pdf,
            "created": created, "updated": now, "filename": new_path.name}


@router.delete("/notes/{nid}")
def delete_note(nid: str):
    if not _valid_id(nid):
        raise HTTPException(400, "invalid id")
    p = _find(nid)
    if not p:
        raise HTTPException(404, "note not found")
    p.unlink()
    (PDF_DIR / f"{nid}.pdf").unlink(missing_ok=True)
    (BOOKMARKS_DIR / f"{nid}.json").unlink(missing_ok=True)
    (CONV_DIR / f"{nid}.json").unlink(missing_ok=True)
    return {"ok": True}
