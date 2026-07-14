"""Naruhodo — AI が読み解く、次世代の論文リーダー。

解説はローカルLLM（Ollama）、質問はクラウドLLM（Gemini/OpenAI/Claude/Codex）。
起動:  python server.py  または  uvicorn server:app --port 8432 --reload
"""

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unicodedata
import webbrowser
from pathlib import Path

import httpx
from dotenv import load_dotenv

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
try:
    import openai_codex as codex_sdk
    from openai_codex.generated.v2_all import (
        AgentMessageDeltaNotification as _CodexDelta,
    )
except ImportError:  # noqa: BLE001
    codex_sdk = None
    _CodexDelta = None
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
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
ASK_PROVIDER = os.environ.get("ASK_PROVIDER", "").strip() or ("gemini" if _legacy_key else "codex")
ASK_API_KEY = os.environ.get("ASK_API_KEY", "").strip() or _legacy_key
ASK_MODEL = (os.environ.get("ASK_MODEL", "").strip() or _legacy_model
             or ("gpt-5.6-sol" if ASK_PROVIDER == "codex" else "gemini-3.5-flash"))
ASK_BASE_URL = os.environ.get("ASK_BASE_URL", "").strip()

PROVIDER_DEFAULTS = {
    "gemini": {"model": "gemini-3.5-flash"},
    "openai": {"model": "gpt-5.4-mini", "base_url": "https://api.openai.com"},
    "anthropic": {"model": "claude-sonnet-4-6", "base_url": "https://api.anthropic.com"},
    "codex": {"model": "gpt-5.6-sol"},
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
    "codex": [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
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


def _load_skip_translation() -> bool:
    return _load_local_settings().get("skip_translation", False)


SKIP_TRANSLATION = _load_skip_translation()



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


def _load_ui_language() -> str:
    return _load_local_settings().get("ui_language", "ja")


def _load_ui_translations(lang: str) -> dict[str, str]:
    path = STATIC_DIR / "locales" / f"{lang}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        path_ja = STATIC_DIR / "locales" / "ja.json"
        try:
            return json.loads(path_ja.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}


UI_LANGUAGE = _load_ui_language()
_ui_translations = _load_ui_translations(UI_LANGUAGE)


def _t(key: str, **kwargs) -> str:
    s = _ui_translations.get(key, key)
    for k, v in kwargs.items():
        s = s.replace(f"{{{k}}}", str(v))
    return s


_paper_cache: dict[str, str] = {}  # paper_id → 論文全文。質問2回目以降の再送を省略

def _is_japanese_text(text: str) -> bool:
    ja_count = sum(1 for c in text if '　' <= c <= '鿿' or '豈' <= c <= '﫿')
    alpha_count = sum(1 for c in text if c.isalpha())
    return alpha_count > 0 and ja_count / alpha_count > 0.3


def _build_explain_instruction(lang: str, source_is_native: bool = False) -> str:
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
    if lang == "ja" and source_is_native:
        return """あなたは日本語の学術論文・文書を読む日本人研究者を助けるアシスタントです。
ユーザーは論文ビューワで分からない箇所を選択しています。
渡された「選択箇所」について、簡潔で正確な日本語の解説を Markdown で出力してください。
出力は必ず次の2つの見出し構成にし、冗長な前置きは書かないこと。

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

def _build_summarize_instruction(lang: str) -> str:
    if lang == "en":
        return """You are an assistant helping a researcher read academic papers.
The user has selected a passage. Summarize it as 3–5 concise bullet points in Markdown.
Each bullet should capture one key point. No preamble — start directly with bullets.
Focus on: what is stated, why it matters, and any key numbers or results."""
    lang_name = NATIVE_LANGUAGES.get(lang, lang)
    return f"""あなたは学術論文を読む研究者を助けるアシスタントです。
ユーザーが選択した箇所を、{lang_name}で3〜5個の箇条書きに要約してください。
各項目は1つの要点を簡潔に捉えること。前置きなしで箇条書きから始めること。
重要な数値・結果・主張を漏らさないこと。"""


def _build_math_instruction(lang: str) -> str:
    if lang == "en":
        return """You are an assistant helping a researcher understand mathematical content in academic papers.
The user has selected a passage containing equations, formulas, or mathematical notation.
Provide a clear explanation in Markdown with exactly these 3 headings — no preamble.

## Symbols & Notation
Define every symbol, variable, operator, and subscript/superscript in the selection as a bullet list.

## Step-by-Step Breakdown
Walk through the mathematical logic: what each term does, how they combine, and the derivation if applicable. Use inline math ($...$) notation.

## Intuitive Meaning
Explain what this math "means" in plain language. Use a concrete analogy or example. Something like "As X grows, Y shrinks exponentially" or "This measures how far the prediction is from the truth." Keep it accurate."""
    lang_name = NATIVE_LANGUAGES.get(lang, lang)
    return f"""あなたは学術論文の数式を解説するアシスタントです。
ユーザーが選択した箇所には数式・数学的記法が含まれています。
以下の3つの見出し構成で{lang_name}の解説を出力してください。前置き不要。

## 記号・表記の定義
選択箇所に登場するすべての変数・演算子・添字を箇条書きで定義する。

## ステップごとの分解
数式の論理を順を追って説明する。各項が何をしているか、どう組み合わさるか、導出があれば示す。インライン数式（$...$）を使うこと。

## 直感的な意味
この数式が「何を意味しているか」を平易な{lang_name}で説明する。
「Xが大きくなるとYは指数的に小さくなる」「予測と真の値の距離を測る指標」のように、
読者が具体的なイメージを持てるように書く。正確さは犠牲にしないこと。"""


def _build_critical_instruction(lang: str) -> str:
    if lang == "en":
        return """You are a critical reading assistant helping a researcher evaluate academic papers.
The user has selected a passage. Analyze it critically in Markdown with exactly these 3 headings — no preamble.

## Assumptions
List the explicit and implicit assumptions this passage relies on. What must be true for the claims to hold?

## Limitations & Weaknesses
Identify methodological limitations, potential confounds, missing controls, generalizability concerns, or logical gaps.

## Questions to Consider
Suggest 2–3 specific follow-up questions a critical reader should ask. Frame them as "Does this account for…?", "What happens if…?", "How robust is this to…?" """
    lang_name = NATIVE_LANGUAGES.get(lang, lang)
    return f"""あなたは学術論文の批判的読解を支援するアシスタントです。
ユーザーが選択した箇所を批判的に分析し、以下の3つの見出し構成で{lang_name}の解説を出力してください。前置き不要。

## 前提条件
この箇所が依拠している明示的・暗黙的な前提を列挙する。主張が成り立つために何が真でなければならないか。

## 限界・弱点
方法論上の限界、交絡要因、統制の欠如、一般化可能性の懸念、論理的なギャップを指摘する。

## 検討すべき問い
批判的な読者が問うべき具体的なフォローアップ質問を2〜3個提案する。
「〇〇を考慮しているか？」「△△の場合はどうなるか？」「この結果は◻◻に対してどの程度頑健か？」のように。"""


def _build_relate_instruction(lang: str) -> str:
    if lang == "en":
        return """You are an assistant helping a researcher contextualize concepts in academic papers.
The user has selected a passage. Explain how it relates to the broader field in Markdown with exactly these 3 headings — no preamble.

## Related Concepts
List related methods, theories, or techniques and briefly explain how each connects to the selected passage.

## Historical Context
Where does this fit in the evolution of the field? What came before, and what does this build upon?

## Practical Implications
What are the real-world applications or downstream effects? Who would use this and why?"""
    lang_name = NATIVE_LANGUAGES.get(lang, lang)
    return f"""あなたは学術論文の文脈理解を支援するアシスタントです。
ユーザーが選択した箇所を、より広い研究分野の中で位置づけ、以下の3つの見出し構成で{lang_name}の解説を出力してください。前置き不要。

## 関連する概念
関連する手法・理論・技術を挙げ、選択箇所とのつながりを簡潔に説明する。

## 歴史的な位置づけ
この分野の発展の中でどこに位置するか。何の上に構築されているか。

## 実用的な意味
現実世界での応用や下流への影響は何か。誰がどのような場面で使うか。"""


MODE_INSTRUCTIONS = {
    "explain": _build_explain_instruction,
    "summarize": _build_summarize_instruction,
    "math": _build_math_instruction,
    "critical": _build_critical_instruction,
    "relate": _build_relate_instruction,
}


def _setup_guide() -> str:
    return _t("server.setup_guide", model=CURRENT_MODEL)


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


def stream_ollama(text: str, context: str | None, mode: str = "explain"):
    builder = MODE_INSTRUCTIONS.get(mode, _build_explain_instruction)
    if mode == "explain":
        source_is_native = SKIP_TRANSLATION or (NATIVE_LANGUAGE != "en" and _is_japanese_text(text))
        instruction = builder(NATIVE_LANGUAGE, source_is_native)
    else:
        instruction = builder(NATIVE_LANGUAGE)
    payload = {
        "model": CURRENT_MODEL,
        "messages": [
            {"role": "system", "content": instruction},
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


# Codex は APIキーではなく ChatGPT サブスクの OAuth 認証で動く。
# `codex app-server` サブプロセスと JSON-RPC で通信する（配線は公式 SDK に任せる）。
# クライアントは初回利用時に遅延起動し、プロセス終了時に閉じる。
_codex_client = None
_codex_lock = threading.Lock()
_codex_workdir: str | None = None


def _codex_bin() -> str | None:
    p = os.environ.get("CODEX_BIN", "").strip()
    if p and Path(p).is_file():
        return p
    p = shutil.which("codex")
    if p:
        return p
    for cand in ("/opt/homebrew/bin/codex", "/usr/local/bin/codex"):
        if Path(cand).is_file():
            return cand
    try:
        from codex_cli_bin import bundled_codex_path
        return str(bundled_codex_path())
    except Exception:  # noqa: BLE001
        return None


def _codex_env() -> dict[str, str]:
    """codex 起動用の環境変数。macOS の GUI アプリ起動時は PATH が最小構成のため、
    npm 版 codex（node シム）が node を見つけられるようログインシェルの PATH を足す。
    Windows は GUI でもユーザーの PATH を引き継ぐのでそのまま返す。"""
    env = dict(os.environ)
    if sys.platform != "darwin":
        return env
    extra = "/opt/homebrew/bin:/usr/local/bin"
    try:
        out = subprocess.run(["/bin/zsh", "-lc", "printf %s \"$PATH\""],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            extra = out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    env["PATH"] = extra + ":" + env.get("PATH", "")
    return env


def _get_codex():
    global _codex_client, _codex_workdir
    with _codex_lock:
        if _codex_client is None:
            bin_path = _codex_bin()
            if not bin_path:
                raise RuntimeError(_t("server.codex_not_found"))
            # cwd は空ディレクトリにして、read-only サンドボックスでも
            # ユーザーのファイルがモデルから見えないようにする。
            _codex_workdir = tempfile.mkdtemp(prefix="naruhodo-codex-")
            _codex_client = codex_sdk.Codex(codex_sdk.CodexConfig(
                codex_bin=bin_path, client_name="naruhodo",
                env=_codex_env()))
        return _codex_client


def _close_codex() -> None:
    global _codex_client
    with _codex_lock:
        if _codex_client is not None:
            try:
                _codex_client.close()
            except Exception:  # noqa: BLE001
                pass
            _codex_client = None
        if _codex_workdir:
            shutil.rmtree(_codex_workdir, ignore_errors=True)


atexit.register(_close_codex)


def _codex_account():
    """ログイン中のアカウント。未ログイン・エラー時は None。"""
    if not codex_sdk or not _codex_bin():
        return None
    try:
        acct = _get_codex().account().account
        return getattr(acct, "root", acct) if acct else None
    except Exception:  # noqa: BLE001
        return None


def ask_status_info() -> dict:
    has_key = bool(ASK_API_KEY)
    provider = ASK_PROVIDER
    sdk_ok = {
        "gemini": bool(genai),
        "openai": bool(openai_mod),
        "anthropic": bool(anthropic_mod),
        "codex": bool(codex_sdk),
        "custom": bool(openai_mod),
    }
    if provider == "custom":
        available = bool(ASK_BASE_URL) and sdk_ok.get(provider, False)
    elif provider == "codex":
        available = sdk_ok["codex"] and _codex_account() is not None
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
        yield _t("server.pip_openai")
        return
    base = ASK_BASE_URL or "https://api.openai.com"
    client = openai_mod.OpenAI(
        api_key=ASK_API_KEY,
        base_url=f"{base.rstrip('/')}/v1",
        timeout=300.0,
    )
    input_parts = [{"role": "system", "content": _build_system(paper)}]
    input_parts.extend(_build_messages(question, selection, history))
    stream = client.responses.create(
        model=ASK_MODEL,
        input=input_parts,
        stream=True,
    )
    for event in stream:
        if event.type == "response.output_text.delta":
            yield event.delta


def stream_anthropic(question: str, paper: str, selection: str | None,
                     history: list[dict] | None):
    if not anthropic_mod:
        yield _t("server.pip_anthropic")
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
        yield _t("server.pip_openai")
        return
    if not ASK_BASE_URL:
        yield _t("server.base_url_missing")
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


def stream_codex(question: str, paper: str, selection: str | None,
                 history: list[dict] | None):
    """ChatGPT サブスク認証の Codex。APIキー不要・履歴はプロンプトに畳み込む。"""
    if not codex_sdk:
        yield _t("server.pip_codex")
        return
    client = _get_codex()
    parts = []
    hist_lines = []
    for h in history or []:
        role = "アシスタント" if (h or {}).get("role") == "model" else "ユーザー"
        text = _sanitize((h or {}).get("text", ""))
        if text:
            hist_lines.append(f"{role}: {text}")
    if hist_lines:
        parts.append("【これまでの会話】\n" + "\n\n".join(hist_lines))
    parts.append(_build_question(question, selection))
    # ASK_MODEL はプロバイダ間で共有なので、Codex のモデルでない値が
    # 残っている場合は指定せず Codex 既定モデルに任せる。
    model = ASK_MODEL if ASK_MODEL.startswith("gpt-") else None
    thread = client.thread_start(
        approval_mode=codex_sdk.ApprovalMode.deny_all,
        sandbox=codex_sdk.Sandbox.read_only,
        cwd=_codex_workdir,
        ephemeral=True,
        model=model,
        base_instructions=_build_system(paper),
    )
    handle = thread.turn("\n\n".join(parts))
    for ev in handle.stream():
        if isinstance(ev.payload, _CodexDelta):
            yield ev.payload.delta


STREAM_FN = {
    "gemini": stream_gemini, "openai": stream_openai,
    "anthropic": stream_anthropic, "codex": stream_codex,
    "custom": stream_custom,
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
    if provider == "codex":
        # 未使用時に app-server サブプロセスを起動しない（起動時プリフェッチ対策）
        if not codex_sdk or not _codex_bin():
            return None
        if _codex_client is None and ASK_PROVIDER != "codex":
            return None
        try:
            # SDK(0.1.0b3) の pydantic 型は新しい codex が返す reasoningEffort 値
            # （max/ultra）で検証エラーになるため、生 JSON-RPC で取得する
            raw = _get_codex()._client._request_raw(
                "model/list", {"includeHidden": False})
            models = [m["id"] for m in raw.get("data", []) if not m.get("hidden")]
            return models or None
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

from routers.bookmarks import router as bookmarks_router
from routers.conversations import router as conversations_router
from routers.notes import router as notes_router
from routers.pdf import router as pdf_router

app.include_router(notes_router)
app.include_router(bookmarks_router)
app.include_router(conversations_router)
app.include_router(pdf_router)


_ALLOWED_ORIGINS = {
    "http://localhost",
    "http://127.0.0.1",
}


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin", "")
    if not origin:
        return True
    base = re.sub(r":\d+$", "", origin)
    return base in _ALLOWED_ORIGINS


@app.middleware("http")
async def security_middleware(request, call_next):
    if not _origin_ok(request):
        return JSONResponse({"error": "origin not allowed"}, status_code=403)
    resp = await call_next(request)
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


class ExplainRequest(BaseModel):
    text: str
    context: str | None = None
    mode: str = "explain"


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
        yield _t("server.pull_downloading", model=model)
        try:
            proc = _sp.Popen([ollama, "pull", model],
                             stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True)
            for line in proc.stdout:
                yield line
            proc.wait()
            if proc.returncode == 0:
                yield _t("server.pull_done")
            else:
                yield _t("server.pull_failed", code=proc.returncode)
        except Exception as e:  # noqa: BLE001
            yield _t("server.pull_error", error=str(e))

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


@app.post("/api/explain")
def explain(req: ExplainRequest):
    text = (req.text or "").strip()
    if not text:
        return JSONResponse({"error": _t("server.empty_selection")}, status_code=400)

    def gen():
        st = ollama_status()
        if not st["running"]:
            yield _setup_guide()
            return
        if not st["model_present"]:
            yield _t("server.model_not_found", model=CURRENT_MODEL)
            return
        try:
            yield from stream_ollama(text, req.context, req.mode)
        except httpx.HTTPStatusError as e:  # noqa: BLE001
            yield _t("server.ollama_error", status=e.response.status_code)
        except Exception as e:  # noqa: BLE001
            yield _t("server.generic_error", error=f"{type(e).__name__}: {e}")

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


class AskRequest(BaseModel):
    question: str
    paper: str = ""            # 論文全文（フロントが各ページ抽出テキストを結合して送る）
    paper_id: str = ""         # PDF内容ハッシュ。2回目以降はpaperを省略しキャッシュから取得
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


_codex_login_handle = None


def _codex_status_dict() -> dict:
    installed = bool(codex_sdk) and bool(_codex_bin())
    acct = _codex_account() if installed else None
    info = {"installed": installed, "logged_in": acct is not None,
            "email": "", "plan": ""}
    if acct is not None:
        info["email"] = getattr(acct, "email", "") or ""
        plan = getattr(acct, "plan_type", None)
        info["plan"] = getattr(plan, "value", "") if plan else ""
    return info


@app.get("/api/codex/status")
def codex_status():
    """codex CLI の有無と ChatGPT ログイン状態。設定画面で codex 選択時に呼ばれる。"""
    return _codex_status_dict()


@app.post("/api/codex/login")
def codex_login():
    global _codex_login_handle
    if not codex_sdk:
        raise HTTPException(400, _t("server.pip_codex"))
    if not _codex_bin():
        raise HTTPException(400, _t("server.codex_not_found"))
    handle = _get_codex().login_chatgpt()
    _codex_login_handle = handle
    # wait() はブロックするのでバックグラウンドで完了を待つ。
    # フロントは /api/codex/status をポーリングして logged_in を確認する。
    threading.Thread(target=lambda: _swallow(handle.wait), daemon=True).start()
    webbrowser.open(handle.auth_url)
    return {"auth_url": handle.auth_url}


def _swallow(fn) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001
        pass


@app.post("/api/codex/logout")
def codex_logout():
    global _codex_login_handle
    if _codex_login_handle is not None:
        _swallow(_codex_login_handle.cancel)
        _codex_login_handle = None
    if codex_sdk and _codex_bin():
        try:
            _get_codex().logout()
        except Exception:  # noqa: BLE001
            pass
    return _codex_status_dict()


class SettingsIn(BaseModel):
    provider: str | None = None
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    native_language: str | None = None
    ui_language: str | None = None
    skip_translation: bool | None = None


ENV_FILE = BASE_DIR / ".env"


def _read_env_lines() -> list[str]:
    if ENV_FILE.exists():
        return ENV_FILE.read_text(encoding="utf-8").splitlines()
    return []


def _write_env_key(key: str, value: str) -> None:
    value = value.replace("\r", "").replace("\n", "")
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
        "ui_language": UI_LANGUAGE,
        "skip_translation": SKIP_TRANSLATION,
    }


@app.put("/api/settings")
def put_settings(req: SettingsIn):
    global ASK_PROVIDER, ASK_API_KEY, ASK_MODEL, ASK_BASE_URL, NATIVE_LANGUAGE, UI_LANGUAGE, _ui_translations, SKIP_TRANSLATION
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
    if req.ui_language is not None:
        ul = req.ui_language.strip()
        if ul in NATIVE_LANGUAGES:
            UI_LANGUAGE = ul
            _ui_translations = _load_ui_translations(ul)
            s = _load_local_settings()
            s["ui_language"] = ul
            _save_local_settings(s)
    if req.skip_translation is not None:
        SKIP_TRANSLATION = req.skip_translation
        s = _load_local_settings()
        s["skip_translation"] = SKIP_TRANSLATION
        _save_local_settings(s)
    return {**ask_status_info(), "native_language": NATIVE_LANGUAGE, "ui_language": UI_LANGUAGE, "skip_translation": SKIP_TRANSLATION, "saved": True}


@app.post("/api/ask")
def ask(req: AskRequest):
    question = (req.question or "").strip()
    if not question:
        return JSONResponse({"error": _t("server.empty_question")}, status_code=400)

    paper = (req.paper or "").strip()
    pid = (req.paper_id or "").strip()
    if paper and pid:
        if len(_paper_cache) >= 20:
            _paper_cache.pop(next(iter(_paper_cache)))
        _paper_cache[pid] = paper
    elif not paper and pid:
        paper = _paper_cache.get(pid, "")

    def gen():
        status = ask_status_info()
        if not status["available"]:
            yield _t("server.ask_not_configured")
            return
        fn = STREAM_FN.get(ASK_PROVIDER)
        if not fn:
            yield _t("server.unknown_provider", provider=ASK_PROVIDER)
            return
        try:
            yield from fn(question, paper, req.selection, req.history)
        except Exception as e:  # noqa: BLE001
            yield _t("server.generic_error", error=f"{type(e).__name__}: {e}")

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


# API ルートより後にマウント（/api/* が優先される）
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8432)
