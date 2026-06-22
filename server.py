"""Naruhodo — AI が読み解く、次世代の論文リーダー。

解説はローカルLLM（Ollama）、質問はクラウドLLM（Gemini/OpenAI/Claude）。
起動:  python server.py  または  uvicorn server:app --port 8432 --reload
"""

import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

# 質問機能のクラウドLLM SDK。翻訳だけなら未導入でも動く。
try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:  # noqa: BLE001
    genai = None
    genai_types = None
try:
    import openai as openai_mod
except ImportError:  # noqa: BLE001
    openai_mod = None
try:
    import anthropic as anthropic_mod
except ImportError:  # noqa: BLE001
    anthropic_mod = None
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(Path(__file__).parent / ".env")

# 解説はローカルLLM（Ollama）で生成。クラウド送信なし・APIキー不要。
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:4b").strip()

# 質問機能はクラウドLLM（Gemini / OpenAI / Anthropic）に投げる。
# 翻訳=ローカル(Ollama)、質問=クラウドという役割分担。
# プロバイダ・APIキーを設定すれば質問欄が有効になる。
_legacy_key = os.environ.get("GEMINI_API_KEY", "").strip()
_legacy_model = os.environ.get("GEMINI_MODEL", "").strip()
ASK_PROVIDER = os.environ.get("ASK_PROVIDER", "").strip() or ("gemini" if _legacy_key else "")
ASK_API_KEY = os.environ.get("ASK_API_KEY", "").strip() or _legacy_key
ASK_MODEL = os.environ.get("ASK_MODEL", "").strip() or _legacy_model or "gemini-3.5-flash"
ASK_BASE_URL = os.environ.get("ASK_BASE_URL", "").strip()

PROVIDER_DEFAULTS = {
    "gemini": {"model": "gemini-3.5-flash"},
    "openai": {"model": "gpt-5.4-mini", "base_url": "https://api.openai.com"},
    "anthropic": {"model": "claude-sonnet-4-6", "base_url": "https://api.anthropic.com"},
    "custom": {"model": "", "base_url": "http://localhost:1234"},
}

PROVIDER_MODELS: dict[str, list[str]] = {
    "gemini": [
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite-preview",
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-3-pro-preview",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
        "gemini-flash-latest",
        "gemini-flash-lite-latest",
        "gemini-pro-latest",
    ],
    "openai": [
        "gpt-5.5",
        "gpt-5.5-pro",
        "gpt-5.4",
        "gpt-5.4-pro",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
    ],
    "anthropic": [
        "claude-fable-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
}

BASE_DIR = Path(__file__).parent

# 選択中モデルは実行中に /api/model で切替でき、再起動後も保つよう小さな
# 設定ファイルに残す（ローカル完結・localhost のみ）。未設定なら環境変数が既定。
SETTINGS_FILE = BASE_DIR / ".settings.json"


def _load_local_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_local_settings(data: dict) -> None:
    try:
        SETTINGS_FILE.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _load_model() -> str:
    m = _load_local_settings().get("model", "")
    return (m or "").strip() or OLLAMA_MODEL


def _save_model(m: str) -> None:
    s = _load_local_settings()
    s["model"] = m
    _save_local_settings(s)


NATIVE_LANGUAGES = {
    "ja": "日本語",
    "en": "English",
    "zh": "中文",
    "ko": "한국어",
}

def _load_native_language() -> str:
    return _load_local_settings().get("native_language", "ja")


NATIVE_LANGUAGE = _load_native_language()


def _unload_model(name: str) -> None:
    """Ollama のメモリから指定モデルを即時アンロードする。Ollama は使用後も
    既定で5分間モデルを載せたままにするため、切替時に旧モデルを残すと 8B 等で
    二重に載りメモリを圧迫する。keep_alive=0 の空リクエストで即解放する。"""
    if not name:
        return
    try:
        httpx.post(f"{OLLAMA_HOST}/api/generate",
                   json={"model": name, "keep_alive": 0}, timeout=5.0)
    except Exception:  # noqa: BLE001
        pass  # 解放は best-effort（落ちても切替自体は成功させる）


# 実行中に書き換わる「いま使うモデル」。OLLAMA_MODEL は初期値・既定値の役目。
CURRENT_MODEL = _load_model()
STATIC_DIR = BASE_DIR / "static"
NOTES_DIR = BASE_DIR / "notes"
NOTES_DIR.mkdir(exist_ok=True)
# PDF 本体のローカルキャッシュ（内容ハッシュ=id で保存）。
# 容量が大きく著作物なので git 管理外（.gitignore）。localhost のみ。
PDF_DIR = BASE_DIR / "pdfs"
PDF_DIR.mkdir(exist_ok=True)
MAX_PDF_BYTES = 200 * 1024 * 1024  # 200MB 上限（暴走防止）

def _build_explain_instruction(lang: str) -> str:
    if lang == "en":
        return """You are an assistant helping a researcher read English academic papers.
The user has selected a passage in a paper viewer.
Provide a concise and accurate explanation of the selected passage in Markdown.
Output must use exactly the following 2 headings — no preamble.

## Terminology & Symbols
Briefly explain technical terms, abbreviations, and mathematical symbols in the selection as a bullet list. If none, write "None."

## In Plain Terms
Help the reader intuitively grasp the meaning. Start with a one-sentence plain-language summary ("In short, …"), then add a concrete analogy or example.
For equations, something like "Basically, the larger the input, the slower the output grows."
For methods, something like "Think of it as skimming the table of contents instead of reading every page."
Keep accuracy intact. End with one sentence on where this passage fits in the paper's flow (intro / method / experiment / discussion).
"""
    if lang == "ja":
        return """あなたは英語の学術論文を読む日本人研究者を助けるアシスタントです。
ユーザーは論文ビューワで分からない箇所を選択しています。
渡された「選択箇所」について、簡潔で正確な日本語の解説を Markdown で出力してください。
出力は必ず次の3つの見出し構成にし、冗長な前置きは書かないこと。

## 日本語訳
選択箇所の自然で正確な日本語訳。専門用語は初出時に原語を括弧で併記する。

## 用語・記号の解説
選択箇所に出てくる専門用語・略語・数式記号を箇条書きで簡潔に説明する。無ければ「特になし」。

## 要するにどういうことか
読者が「あーそういうことね」と腹落ちできるように、具体的な例えや身近な比喩を交えて説明する。
まず一文で「つまり○○ということ」と平易に言い切り、その後に具体例や直感的なイメージで補足する。
たとえば数式なら「要するに入力が大きいほど出力が鈍くなる、という関係」のように噛み砕く。
手法の説明なら「ざっくり言えば、辞書を引く代わりに目次だけ見て当たりをつける方式」のように
読者の頭に絵が浮かぶレベルまで落とす。ただし正確さは犠牲にしないこと。
最後に、論文の流れの中でこの箇所がどこに位置するか（導入・手法・実験・考察など）を一言添える。
"""
    lang_name = NATIVE_LANGUAGES.get(lang, lang)
    return f"""あなたは英語の学術論文を読む研究者を助けるアシスタントです。
ユーザーは論文ビューワで分からない箇所を選択しています。
渡された「選択箇所」について、簡潔で正確な{lang_name}の解説を Markdown で出力してください。
出力は必ず次の3つの見出し構成にし、冗長な前置きは書かないこと。

## {lang_name}訳
選択箇所の自然で正確な{lang_name}訳。専門用語は初出時に原語を括弧で併記する。

## 用語・記号の解説
選択箇所に出てくる専門用語・略語・数式記号を箇条書きで簡潔に説明する。無ければ「特になし」。

## 要するにどういうことか
読者が直感的に理解できるように、具体的な例えや身近な比喩を交えて{lang_name}で説明する。
まず一文で平易に言い切り、その後に具体例や直感的なイメージで補足する。
正確さは犠牲にしないこと。
最後に、論文の流れの中でこの箇所がどこに位置するか（導入・手法・実験・考察など）を一言添える。
"""

SETUP_GUIDE = f"""> ⚠️ **Ollama が見つかりません**
>
> 解説機能には Ollama（ローカルLLM）が必要です。
>
> 1. [Ollama をインストール](https://ollama.com/download)
> 2. Ollama アプリを起動
> 3. `ollama pull {OLLAMA_MODEL}`
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
                "model": CURRENT_MODEL, "models": []}
    base = CURRENT_MODEL.split(":")[0]
    present = CURRENT_MODEL in models or any(
        m == CURRENT_MODEL or m.split(":")[0] == base for m in models
    )
    return {"running": True, "model_present": present,
            "model": CURRENT_MODEL, "models": models}


def stream_ollama(text: str, context: str | None):
    payload = {
        "model": CURRENT_MODEL,
        "messages": [
            {"role": "system", "content": _build_explain_instruction(NATIVE_LANGUAGE)},
            {"role": "user", "content": build_prompt(text, context)},
        ],
        "stream": True,
        # qwen3.5 等のハイブリッド推論モデルは既定で回答前に思考トークンを長く
        # 出すため最初の出力が遅い。翻訳・用語解説に思考は不要なので明示的に切る
        # （非推論モデル=qwen3:*-instruct 等では無視される）。
        "think": False,
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


# ---------- 内容についての質問（クラウドLLM・論文全文を文脈に持つ） ----------

def _build_ask_instruction(lang: str) -> str:
    if lang == "en":
        return """You are an excellent research assistant helping a researcher read English academic papers.
The full text of the paper the user is currently reading is appended after this instruction.
Answer the user's questions accurately and in detail, grounded in the paper's context, in English.

- When evidence exists in the paper, cite the location (section, figure, table, equation number).
- When stating something not in the paper, explicitly mark it as inference or general knowledge.
- Break down equations and jargon as needed. Skip lengthy preambles.
- Structure answers in Markdown.
"""
    lang_name = NATIVE_LANGUAGES.get(lang, lang)
    return f"""あなたは英語の学術論文を読む研究者を支える、優秀な研究アシスタントです。
ユーザーがいま読んでいる論文の全文が、この指示の後ろに添付されています。これを踏まえ、
論文の文脈に即してユーザーの質問に{lang_name}で詳しく・正確に答えてください。

- 論文中に根拠がある場合は、その箇所（節・図表・式番号など）に触れて答える。
- 論文に書かれていないことを述べるときは、推測・一般知識であると明示する。
- 数式・専門用語は必要に応じて噛み砕く。冗長な前置きは省く。
- Markdown で構造化して答える。
"""


def ask_status_info() -> dict:
    has_key = bool(ASK_API_KEY)
    provider = ASK_PROVIDER
    sdk_ok = {
        "gemini": bool(genai),
        "openai": bool(openai_mod),
        "anthropic": bool(anthropic_mod),
        "custom": bool(openai_mod),
    }
    if provider == "custom":
        available = bool(ASK_BASE_URL) and sdk_ok.get(provider, False)
    else:
        available = has_key and bool(provider) and sdk_ok.get(provider, False)
    return {
        "available": available,
        "provider": provider,
        "key": has_key,
        "model": ASK_MODEL,
    }


def _build_system(paper: str) -> str:
    system = _build_ask_instruction(NATIVE_LANGUAGE)
    paper = _sanitize(paper)
    if paper.strip():
        system += "\n\n# 対象論文（全文）\n" + paper.strip()
    return system


def _build_question(question: str, selection: str | None) -> str:
    parts = []
    selection = _sanitize(selection)
    if selection and selection.strip():
        parts.append("【いま選択している箇所（質問の対象）】\n" + selection.strip())
    parts.append("【質問】\n" + _sanitize(question).strip())
    return "\n\n".join(parts)


def _build_messages(question: str, selection: str | None,
                    history: list[dict] | None) -> list[dict]:
    msgs = []
    for h in history or []:
        role = "assistant" if (h or {}).get("role") == "model" else "user"
        text = _sanitize((h or {}).get("text", ""))
        if text:
            msgs.append({"role": role, "content": text})
    msgs.append({"role": "user", "content": _build_question(question, selection)})
    return msgs


def stream_gemini(question: str, paper: str, selection: str | None,
                  history: list[dict] | None):
    client = genai.Client(api_key=ASK_API_KEY)
    contents = []
    for h in history or []:
        role = "model" if (h or {}).get("role") == "model" else "user"
        text = _sanitize((h or {}).get("text", ""))
        if text:
            contents.append(
                genai_types.Content(role=role, parts=[genai_types.Part(text=text)])
            )
    contents.append(genai_types.Content(
        role="user", parts=[genai_types.Part(text=_build_question(question, selection))]
    ))
    config = genai_types.GenerateContentConfig(
        system_instruction=_build_system(paper),
    )
    stream = client.models.generate_content_stream(
        model=ASK_MODEL, contents=contents, config=config
    )
    for chunk in stream:
        piece = getattr(chunk, "text", None)
        if piece:
            yield piece


def stream_openai(question: str, paper: str, selection: str | None,
                  history: list[dict] | None):
    if not openai_mod:
        yield "> ⚠️ openai パッケージが未インストールです。`pip install openai` を実行してください。"
        return
    base = ASK_BASE_URL or "https://api.openai.com"
    client = openai_mod.OpenAI(
        api_key=ASK_API_KEY,
        base_url=f"{base.rstrip('/')}/v1",
        timeout=300.0,
    )
    messages = [{"role": "system", "content": _build_system(paper)}]
    messages.extend(_build_messages(question, selection, history))
    stream = client.chat.completions.create(
        model=ASK_MODEL,
        messages=messages,
        temperature=0.4,
        stream=True,
    )
    for chunk in stream:
        piece = chunk.choices[0].delta.content if chunk.choices else None
        if piece:
            yield piece


def stream_anthropic(question: str, paper: str, selection: str | None,
                     history: list[dict] | None):
    if not anthropic_mod:
        yield "> ⚠️ anthropic パッケージが未インストールです。`pip install anthropic` を実行してください。"
        return
    base = ASK_BASE_URL or "https://api.anthropic.com"
    client = anthropic_mod.Anthropic(
        api_key=ASK_API_KEY,
        base_url=base.rstrip("/"),
        timeout=300.0,
    )
    messages = _build_messages(question, selection, history)
    with client.messages.stream(
        model=ASK_MODEL,
        system=_build_system(paper),
        messages=messages,
        max_tokens=16384,
        temperature=0.4,
    ) as stream:
        for text in stream.text_stream:
            yield text


def stream_custom(question: str, paper: str, selection: str | None,
                  history: list[dict] | None):
    """OpenAI互換エンドポイント向け。APIキーはダミーでも可。"""
    if not openai_mod:
        yield "> ⚠️ openai パッケージが未インストールです。`pip install openai` を実行してください。"
        return
    if not ASK_BASE_URL:
        yield "> ⚠️ ベースURLが未設定です。設定画面でエンドポイントURLを入力してください。"
        return
    client = openai_mod.OpenAI(
        api_key=ASK_API_KEY or "not-needed",
        base_url=f"{ASK_BASE_URL.rstrip('/')}/v1",
        timeout=300.0,
    )
    messages = [{"role": "system", "content": _build_system(paper)}]
    messages.extend(_build_messages(question, selection, history))
    stream = client.chat.completions.create(
        model=ASK_MODEL or "default",
        messages=messages,
        temperature=0.4,
        stream=True,
    )
    for chunk in stream:
        piece = chunk.choices[0].delta.content if chunk.choices else None
        if piece:
            yield piece


STREAM_FN = {
    "gemini": stream_gemini, "openai": stream_openai,
    "anthropic": stream_anthropic, "custom": stream_custom,
}


def _fetch_provider_models(provider: str, api_key: str = "",
                           base_url: str = "") -> list[str] | None:
    """プロバイダの公式APIからモデル一覧を取得。失敗時は None。"""
    key = api_key or ASK_API_KEY
    burl = base_url or ASK_BASE_URL
    if provider == "custom":
        if not burl or not openai_mod:
            return None
        try:
            client = openai_mod.OpenAI(
                api_key=key or "not-needed",
                base_url=f"{burl.rstrip('/')}/v1",
                timeout=10.0,
            )
            resp = client.models.list()
            return sorted(m.id for m in resp) or None
        except Exception:  # noqa: BLE001
            return None
    if not key:
        return None
    try:
        if provider == "gemini" and genai:
            client = genai.Client(api_key=key)
            skip_contains = ("embedding", "tts", "robotics", "image", "audio",
                             "live", "translate", "computer-use", "customtools")
            old_prefix = re.compile(r"^gemini-[012]\.[0-4]")
            models = []
            for m in client.models.list():
                name = m.name or ""
                if name.startswith("models/"):
                    name = name[7:]
                if not name.startswith("gemini"):
                    continue
                if any(s in name for s in skip_contains):
                    continue
                if old_prefix.match(name):
                    continue
                models.append(name)
            return sorted(models) if models else None
        if provider == "openai" and openai_mod:
            client = openai_mod.OpenAI(api_key=key, timeout=10.0)
            resp = client.models.list()
            models = []
            for m in resp:
                mid = m.id
                if mid.startswith("gpt-"):
                    models.append(mid)
            return sorted(models) if models else None
        if provider == "anthropic" and anthropic_mod:
            client = anthropic_mod.Anthropic(api_key=key, timeout=10.0)
            resp = client.models.list(limit=100)
            models = [m.id for m in resp.data if m.id.startswith("claude-")]
            return sorted(models) if models else None
    except Exception:  # noqa: BLE001
        pass
    return None


app = FastAPI(title="Naruhodo")


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


def _normalize_math(s: str) -> str:
    """数式記号（Mathematical Alphanumeric Symbols, U+1D400–U+1D7FF）を
    ASCII/ギリシャ文字へ正規化する。pdf.js は数式の斜体 cost/φ/P 等をこの
    領域の文字（𝑐𝑜𝑠𝑡 𝜙 𝑃 …）として抽出するが、選択箇所と周辺文脈が
    これで埋まると軽量モデルが翻訳を諦めて英語原文を「日本語訳」に
    丸写しする。NFKC で 𝑐𝑜𝑠𝑡→cost・𝜙→φ に戻すと翻訳が安定し、
    出力に奇妙なグリフが残らなくなる（NFKC 対象外の予約符号は不変）。"""
    return "".join(
        unicodedata.normalize("NFKC", ch) if 0x1D400 <= ord(ch) <= 0x1D7FF else ch
        for ch in s
    )


def _sanitize(s: str | None) -> str:
    """JSON 経由で混入し得る孤立サロゲート（数式の斜体等を pdf.js 抽出時に
    片割れだけ拾った結果）を除去。残すと UTF-8 エンコードで落ちる。
    併せて数式記号を ASCII/ギリシャ文字へ正規化する。"""
    return _normalize_math(_SURROGATE_RE.sub("", s or ""))


def build_prompt(text: str, context: str | None) -> str:
    parts = []
    text = _sanitize(text)
    context = _sanitize(context)
    if context and context.strip():
        parts.append("【参考: 同じページの周辺テキスト（訳出は不要、文脈把握用）】\n" + context.strip())
    parts.append("【選択箇所】\n" + text.strip())
    return "\n\n".join(parts)


WELCOMED_FILE = BASE_DIR / ".welcomed"
LAST_PDF_FILE = BASE_DIR / ".last-pdf.json"
LAYOUT_FILE = BASE_DIR / ".layout"
FONT_SIZES_FILE = BASE_DIR / ".font-sizes.json"


@app.get("/api/welcomed")
def get_welcomed():
    return {"welcomed": WELCOMED_FILE.exists()}


@app.post("/api/welcomed")
def set_welcomed():
    WELCOMED_FILE.write_text("1", encoding="utf-8")
    return {"welcomed": True}


@app.get("/api/last-pdf")
def get_last_pdf():
    try:
        data = json.loads(LAST_PDF_FILE.read_text(encoding="utf-8"))
        return {"id": data.get("id", ""), "name": data.get("name", "")}
    except Exception:  # noqa: BLE001
        return {"id": "", "name": ""}


class LastPdfIn(BaseModel):
    id: str
    name: str = "document.pdf"


@app.put("/api/last-pdf")
def set_last_pdf(req: LastPdfIn):
    LAST_PDF_FILE.write_text(
        json.dumps({"id": req.id, "name": req.name}, ensure_ascii=False),
        encoding="utf-8")
    return {"ok": True}


@app.get("/api/layout")
def get_layout():
    try:
        return {"layout": LAYOUT_FILE.read_text(encoding="utf-8").strip()}
    except Exception:  # noqa: BLE001
        return {"layout": "standard"}


class LayoutIn(BaseModel):
    layout: str


@app.put("/api/layout")
def set_layout(req: LayoutIn):
    val = req.layout.strip() if req.layout else "standard"
    LAYOUT_FILE.write_text(val, encoding="utf-8")
    return {"layout": val}


@app.get("/api/font-sizes")
def get_font_sizes():
    try:
        return json.loads(FONT_SIZES_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


@app.put("/api/font-sizes")
async def put_font_sizes(request: Request):
    data = await request.json()
    FONT_SIZES_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True, **ollama_status()}


@app.get("/api/llm-status")
def llm_status():
    return ollama_status()


class ModelIn(BaseModel):
    model: str


@app.post("/api/model")
def set_model(req: ModelIn):
    """使用するモデルを切り替える（実行中に反映＋設定ファイルに永続化）。"""
    global CURRENT_MODEL
    name = (req.model or "").strip()
    if not name:
        raise HTTPException(400, "model is empty")
    old = CURRENT_MODEL
    CURRENT_MODEL = name
    _save_model(name)
    if old and old != name:
        _unload_model(old)  # 旧モデルをメモリから解放（新モデルは次の解説時に load）
    return ollama_status()


@app.post("/api/pull-model")
def pull_model():
    """現在のモデルを Ollama から pull する（未取得時にアプリ内から実行）。"""
    import subprocess as _sp
    import shutil as _sh
    ollama = _sh.which("ollama")
    if not ollama:
        raise HTTPException(500, "ollama command not found")
    model = CURRENT_MODEL

    def gen():
        yield f"モデル {model} をダウンロード中…\n"
        try:
            proc = _sp.Popen([ollama, "pull", model],
                             stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True)
            for line in proc.stdout:
                yield line
            proc.wait()
            if proc.returncode == 0:
                yield "\n✅ ダウンロード完了！ページを再読み込みしてください。\n"
            else:
                yield f"\n⚠️ ダウンロード失敗（終了コード {proc.returncode}）\n"
        except Exception as e:  # noqa: BLE001
            yield f"\n⚠️ エラー: {e}\n"

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


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
                f"> ⚠️ モデル **{CURRENT_MODEL}** が未取得です。\n>\n"
                f"> ターミナルで `ollama pull {CURRENT_MODEL}` を実行してから、"
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


class AskRequest(BaseModel):
    question: str
    paper: str = ""            # 論文全文（フロントが各ページ抽出テキストを結合して送る）
    selection: str | None = None  # いま選択中の箇所（あれば質問対象として引用）
    history: list[dict] = []   # [{role: "user"|"model", text: str}, ...]


@app.get("/api/ask-status")
def ask_status():
    return ask_status_info()


class ProviderModelsIn(BaseModel):
    api_key: str = ""
    base_url: str = ""


@app.post("/api/provider-models/{provider}")
def get_provider_models(provider: str, body: ProviderModelsIn | None = None):
    """プロバイダのAPIから利用可能なモデル一覧を取得。APIキー未設定やエラー時はプリセットを返す。"""
    if provider not in PROVIDER_DEFAULTS:
        raise HTTPException(400, "unknown provider")
    body = body or ProviderModelsIn()
    fetched = _fetch_provider_models(provider, body.api_key, body.base_url)
    if fetched is not None:
        return {"models": fetched, "source": "api"}
    return {"models": PROVIDER_MODELS.get(provider, []), "source": "preset"}


class SettingsIn(BaseModel):
    provider: str | None = None
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    native_language: str | None = None


ENV_FILE = BASE_DIR / ".env"


def _read_env_lines() -> list[str]:
    if ENV_FILE.exists():
        return ENV_FILE.read_text(encoding="utf-8").splitlines()
    return []


def _write_env_key(key: str, value: str) -> None:
    lines = _read_env_lines()
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


@app.get("/api/settings")
def get_settings():
    return {
        "provider": ASK_PROVIDER,
        "key_set": bool(ASK_API_KEY),
        "model": ASK_MODEL,
        "base_url": ASK_BASE_URL,
        "providers": list(PROVIDER_DEFAULTS.keys()),
        "native_language": NATIVE_LANGUAGE,
        "native_languages": NATIVE_LANGUAGES,
    }


@app.put("/api/settings")
def put_settings(req: SettingsIn):
    global ASK_PROVIDER, ASK_API_KEY, ASK_MODEL, ASK_BASE_URL, NATIVE_LANGUAGE
    if req.provider is not None:
        ASK_PROVIDER = req.provider.strip()
        _write_env_key("ASK_PROVIDER", ASK_PROVIDER)
    if req.api_key is not None:
        ASK_API_KEY = req.api_key.strip()
        _write_env_key("ASK_API_KEY", ASK_API_KEY)
    if req.model is not None:
        m = req.model.strip()
        if m:
            ASK_MODEL = m
            _write_env_key("ASK_MODEL", ASK_MODEL)
    if req.base_url is not None:
        ASK_BASE_URL = req.base_url.strip()
        _write_env_key("ASK_BASE_URL", ASK_BASE_URL)
    if req.native_language is not None:
        nl = req.native_language.strip()
        if nl in NATIVE_LANGUAGES:
            NATIVE_LANGUAGE = nl
            s = _load_local_settings()
            s["native_language"] = nl
            _save_local_settings(s)
    return {**ask_status_info(), "native_language": NATIVE_LANGUAGE, "saved": True}


@app.post("/api/ask")
def ask(req: AskRequest):
    question = (req.question or "").strip()
    if not question:
        return JSONResponse({"error": "質問が空です"}, status_code=400)

    def gen():
        status = ask_status_info()
        if not status["available"]:
            yield "> ⚠️ 質問機能が未設定です。⚙ 設定からAPIキーを登録してください。"
            return
        fn = STREAM_FN.get(ASK_PROVIDER)
        if not fn:
            yield f"> ⚠️ 未対応のプロバイダ: {ASK_PROVIDER}"
            return
        try:
            yield from fn(question, req.paper, req.selection, req.history)
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
    (PDF_DIR / f"{nid}.pdf").unlink(missing_ok=True)
    (BOOKMARKS_DIR / f"{nid}.json").unlink(missing_ok=True)
    (CONV_DIR / f"{nid}.json").unlink(missing_ok=True)
    return {"ok": True}


# ---------- しおり（PDFごとのブックマーク位置） ----------
# 1 PDF = bookmarks/<id>.json。id は PDF 内容の SHA-256（notes と同じ）。
# 位置は { pageIndex, y(0..1), t(任意のタイムスタンプ) } の配列で持つ。

BOOKMARKS_DIR = BASE_DIR / "bookmarks"
BOOKMARKS_DIR.mkdir(exist_ok=True)


class BookmarkItem(BaseModel):
    pageIndex: int
    y: float
    t: int | None = None


class BookmarksIn(BaseModel):
    items: list[BookmarkItem] = []


def _bm_path(pid: str) -> Path:
    if not _valid_id(pid):
        raise HTTPException(400, "invalid id")
    return BOOKMARKS_DIR / f"{pid}.json"


@app.get("/api/bookmarks/{pid}")
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


@app.put("/api/bookmarks/{pid}")
def put_bookmarks(pid: str, payload: BookmarksIn):
    path = _bm_path(pid)
    items = [it.model_dump(exclude_none=True) for it in payload.items]
    if items:
        path.write_text(json.dumps({"items": items}, ensure_ascii=False),
                        encoding="utf-8")
    else:
        path.unlink(missing_ok=True)  # 空ならファイルごと削除
    return {"ok": True, "count": len(items)}


# ---------- 会話履歴（解説・質問カード） ----------
# PDF ごとにサイドペインのカードを保存し、再度開いたときに復元する。

CONV_DIR = BASE_DIR / "conversations"
CONV_DIR.mkdir(exist_ok=True)


class ConvItem(BaseModel):
    type: str  # "explain" | "ask"
    src: str
    body: str


class ConvIn(BaseModel):
    items: list[ConvItem] = []


def _conv_path(pid: str) -> Path:
    if not _valid_id(pid):
        raise HTTPException(400, "invalid id")
    return CONV_DIR / f"{pid}.json"


@app.get("/api/conversations/{pid}")
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


@app.put("/api/conversations/{pid}")
def put_conversations(pid: str, payload: ConvIn):
    path = _conv_path(pid)
    items = [it.model_dump() for it in payload.items]
    if items:
        path.write_text(json.dumps({"items": items}, ensure_ascii=False),
                        encoding="utf-8")
    else:
        path.unlink(missing_ok=True)
    return {"ok": True, "count": len(items)}


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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8432, reload=True)
