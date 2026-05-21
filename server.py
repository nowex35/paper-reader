"""Paper Reader — PDFを選択するとその場で日本語訳＋解説が出るローカルリーダー。

起動:  uvicorn server:app --reload  (詳細は README.md)
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(Path(__file__).parent / ".env")

# 解説はローカルLLM（Ollama）で生成。クラウド送信なし・APIキー不要。
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:4b-instruct").strip()

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
NOTES_DIR = BASE_DIR / "notes"
NOTES_DIR.mkdir(exist_ok=True)
# PDF 本体のローカルキャッシュ（内容ハッシュ=id で保存）。
# 容量が大きく著作物なので git 管理外（.gitignore）。localhost のみ。
PDF_DIR = BASE_DIR / "pdfs"
PDF_DIR.mkdir(exist_ok=True)
MAX_PDF_BYTES = 200 * 1024 * 1024  # 200MB 上限（暴走防止）

SYSTEM_INSTRUCTION = """あなたは英語の学術論文を読む日本人研究者を助けるアシスタントです。
ユーザーは論文ビューワで分からない箇所を選択しています。
渡された「選択箇所」について、簡潔で正確な日本語の解説を Markdown で出力してください。
出力は必ず次の3つの見出し構成にし、冗長な前置きは書かないこと。

## 日本語訳
選択箇所の自然で正確な日本語訳。専門用語は初出時に原語を括弧で併記する。

## 用語・記号の解説
選択箇所に出てくる専門用語・略語・数式記号を箇条書きで簡潔に説明する。無ければ「特になし」。

## この箇所の主旨
論文の文脈でこの箇所が何を述べ・主張しているかを2〜3文で補足する。
"""

SETUP_GUIDE = f"""> ⚠️ **ローカルLLM（Ollama）が見つかりません**
>
> このアプリは解説をあなたのPC内（Ollama）で生成します。初回のみ準備が必要です。
>
> 1. インストール: `brew install ollama`（または https://ollama.com/download ）
> 2. 起動: `ollama serve`（別ターミナル。アプリ化後は自動起動にします）
> 3. モデル取得: `ollama pull {OLLAMA_MODEL}`
>
> 準備ができたら、もう一度テキストを選択してください。
"""


def ollama_status() -> dict:
    """Ollama の起動有無と既定モデルの取得有無を返す。"""
    try:
        r = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=2.0)
        r.raise_for_status()
        models = [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:  # noqa: BLE001
        return {"running": False, "model_present": False,
                "model": OLLAMA_MODEL, "models": []}
    base = OLLAMA_MODEL.split(":")[0]
    present = OLLAMA_MODEL in models or any(
        m == OLLAMA_MODEL or m.split(":")[0] == base for m in models
    )
    return {"running": True, "model_present": present,
            "model": OLLAMA_MODEL, "models": models}


def stream_ollama(text: str, context: str | None):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": build_prompt(text, context)},
        ],
        "stream": True,
        "options": {"temperature": 0.3},
    }
    timeout = httpx.Timeout(600.0, connect=5.0)
    with httpx.Client(timeout=timeout) as c:
        with c.stream("POST", f"{OLLAMA_HOST}/api/chat", json=payload) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                piece = (obj.get("message") or {}).get("content")
                if piece:
                    yield piece
                if obj.get("done"):
                    break


app = FastAPI(title="Paper Reader")


@app.middleware("http")
async def no_cache(request, call_next):
    """localhost専用の開発ツール。常に最新を配信し、キャッシュ食い違い事故を防ぐ。"""
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store"
    return resp


class ExplainRequest(BaseModel):
    text: str
    context: str | None = None


_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def _sanitize(s: str | None) -> str:
    """JSON 経由で混入し得る孤立サロゲート（数式の斜体等を pdf.js 抽出時に
    片割れだけ拾った結果）を除去。残すと UTF-8 エンコードで落ちる。"""
    return _SURROGATE_RE.sub("", s or "")


def build_prompt(text: str, context: str | None) -> str:
    parts = []
    text = _sanitize(text)
    context = _sanitize(context)
    if context and context.strip():
        parts.append("【参考: 同じページの周辺テキスト（訳出は不要、文脈把握用）】\n" + context.strip())
    parts.append("【選択箇所】\n" + text.strip())
    return "\n\n".join(parts)


@app.get("/api/health")
def health():
    return {"ok": True, **ollama_status()}


@app.get("/api/llm-status")
def llm_status():
    return ollama_status()


@app.post("/api/explain")
def explain(req: ExplainRequest):
    text = (req.text or "").strip()
    if not text:
        return JSONResponse({"error": "選択テキストが空です"}, status_code=400)

    def gen():
        st = ollama_status()
        if not st["running"]:
            yield SETUP_GUIDE
            return
        if not st["model_present"]:
            yield (
                f"> ⚠️ モデル **{OLLAMA_MODEL}** が未取得です。\n>\n"
                f"> ターミナルで `ollama pull {OLLAMA_MODEL}` を実行してから、"
                "もう一度選択してください。\n"
            )
            return
        try:
            yield from stream_ollama(text, req.context)
        except httpx.HTTPStatusError as e:  # noqa: BLE001
            yield f"\n\n> ⚠️ Ollama エラー (HTTP {e.response.status_code})。モデル名や `ollama serve` を確認してください。"
        except Exception as e:  # noqa: BLE001
            yield f"\n\n> ⚠️ エラー: {type(e).__name__}: {e}"

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


# ---------- メモ（論文ごとの自分用まとめ） ----------
# 論文1本 = notes/<slug>-<id>.md（frontmatter付きMarkdown）。
# id は PDF 内容の SHA-256 先頭16桁（フロントで算出）。

ID_RE = re.compile(r"[0-9a-fA-F]{6,64}")
FM_KEYS = ["id", "title", "pdf", "created", "updated"]

# summary タブ = 落合フォーマット6項目。メモ本文とは別フィールドとして
# 同じ .md 内の専用セクションに保存する（人が読めて、機械でも再パースできる）。
# 6項目は落合陽一「先端技術とメディア表現1 #FTMA15」が出典（本ツールの考案ではない）:
# https://www.slideshare.net/Ochyai/1-ftma15
SUMMARY_FIELDS = [
    ("what", "どんなもの？"),
    ("prior", "先行研究と比べてどこがすごい？"),
    ("method", "技術や手法のキモはどこ？"),
    ("verify", "どうやって有効だと検証した？"),
    ("discuss", "議論はある？"),
    ("next", "次に読むべき論文は？"),
]
SUMMARY_KEYS = [k for k, _ in SUMMARY_FIELDS]
SUMMARY_MARK = "<!--paper-reader:summary-->"
_Q_RE = re.compile(r"^###\s.*<!--q:(\w+)-->\s*$")


class NoteIn(BaseModel):
    title: str = "Untitled"
    body: str = ""
    pdf: str | None = None
    summary: dict[str, str] | None = None


def _empty_summary() -> dict:
    return {k: "" for k in SUMMARY_KEYS}


def _split_body(full: str) -> tuple[str, dict]:
    """保存済み body を「メモ本文」と「summary(6項目)」に分離する。"""
    summary = _empty_summary()
    idx = (full or "").find(SUMMARY_MARK)
    if idx < 0:
        return (full or "").rstrip(), summary
    memo = full[:idx].rstrip()
    cur, buf = None, []
    for line in full[idx + len(SUMMARY_MARK):].split("\n"):
        m = _Q_RE.match(line)
        if m:
            if cur in summary:
                summary[cur] = "\n".join(buf).strip()
            cur, buf = m.group(1), []
        elif cur:
            buf.append(line)
    if cur in summary:
        summary[cur] = "\n".join(buf).strip()
    return memo, summary


def _compose_body(memo: str, summary: dict | None) -> str:
    """メモ本文と summary を結合し、保存用の body 文字列を作る。"""
    memo = (memo or "").rstrip()
    summary = summary or {}
    if not any((summary.get(k) or "").strip() for k in SUMMARY_KEYS):
        return memo  # summary 未記入なら従来どおりメモのみ
    parts = [memo, "", SUMMARY_MARK, "", "## 📝 落合まとめ", ""]
    for key, q in SUMMARY_FIELDS:
        parts.append(f"### {q} <!--q:{key}-->")
        parts.append((summary.get(key) or "").strip())
        parts.append("")
    return "\n".join(parts).rstrip()


def _valid_id(nid: str) -> bool:
    return bool(ID_RE.fullmatch(nid or ""))


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
                    meta[k.strip()] = v.strip()
                elif ln.endswith(":"):
                    meta[ln[:-1].strip()] = ""
            body = "\n".join(lines[end + 1:]).lstrip("\n")
    return meta, body


def _write(path: Path, meta: dict, body: str):
    fm = "---\n" + "".join(f"{k}: {meta.get(k, '')}\n" for k in FM_KEYS) + "---\n\n"
    path.write_text(fm + body.rstrip() + "\n", encoding="utf-8")


@app.get("/api/notes")
def list_notes():
    out = []
    for p in NOTES_DIR.glob("*.md"):
        m, b = _parse(p)
        memo, _ = _split_body(b)  # 一覧スニペットに summary を混ぜない
        out.append({
            "id": m.get("id") or p.stem.rsplit("-", 1)[-1],
            "title": m.get("title") or p.stem,
            "pdf": m.get("pdf", ""),
            "updated": m.get("updated", ""),
            "snippet": re.sub(r"\s+", " ", memo.strip())[:120],
        })
    out.sort(key=lambda x: x["updated"], reverse=True)
    return out


@app.get("/api/notes/{nid}")
def get_note(nid: str):
    if not _valid_id(nid):
        raise HTTPException(400, "invalid id")
    p = _find(nid)
    if not p:
        raise HTTPException(404, "note not found")
    m, b = _parse(p)
    memo, summary = _split_body(b)
    return {
        "id": nid, "title": m.get("title", ""), "pdf": m.get("pdf", ""),
        "created": m.get("created", ""), "updated": m.get("updated", ""),
        "body": memo, "summary": summary, "filename": p.name,
    }


@app.put("/api/notes/{nid}")
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
    _write(new_path, meta, _compose_body(note.body, note.summary))
    return {"id": nid, "title": title, "pdf": pdf,
            "created": created, "updated": now, "filename": new_path.name}


@app.delete("/api/notes/{nid}")
def delete_note(nid: str):
    if not _valid_id(nid):
        raise HTTPException(400, "invalid id")
    p = _find(nid)
    if not p:
        raise HTTPException(404, "note not found")
    p.unlink()
    (PDF_DIR / f"{nid}.pdf").unlink(missing_ok=True)  # 紐づくPDFキャッシュも削除
    return {"ok": True}


# ---------- PDF 本体のローカルキャッシュ ----------
# 開いた PDF を id（内容ハッシュ）で保存し、リロード/一覧クリックで復元する。


def _pdf_path(pid: str) -> Path:
    if not _valid_id(pid):
        raise HTTPException(400, "invalid id")
    return PDF_DIR / f"{pid}.pdf"


@app.put("/api/pdf/{pid}")
async def put_pdf(pid: str, request: Request):
    path = _pdf_path(pid)
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty body")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, "pdf too large")
    path.write_bytes(data)
    return {"ok": True, "id": pid, "bytes": len(data)}


@app.get("/api/pdf/{pid}")
def get_pdf(pid: str):
    path = _pdf_path(pid)
    if not path.exists():
        raise HTTPException(404, "pdf not cached")
    return FileResponse(path, media_type="application/pdf")


# API ルートより後にマウント（/api/* が優先される）
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
