"""PDF 本体のローカルキャッシュ。"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from routers._shared import MAX_PDF_BYTES, PDF_DIR, _valid_id

router = APIRouter(prefix="/api")


def _pdf_path(pid: str):
    if not _valid_id(pid):
        raise HTTPException(400, "invalid id")
    return PDF_DIR / f"{pid}.pdf"


@router.put("/pdf/{pid}")
async def put_pdf(pid: str, request: Request):
    path = _pdf_path(pid)
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty body")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, "pdf too large")
    path.write_bytes(data)
    return {"ok": True, "id": pid, "bytes": len(data)}


@router.get("/pdf/{pid}")
def get_pdf(pid: str):
    path = _pdf_path(pid)
    if not path.exists():
        raise HTTPException(404, "pdf not cached")
    return FileResponse(path, media_type="application/pdf")
