"""ルーター間で共有するパス定数とユーティリティ。"""

import re
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
STATIC_DIR = BASE_DIR / "static"
NOTES_DIR = BASE_DIR / "notes"
NOTES_DIR.mkdir(exist_ok=True)
PDF_DIR = BASE_DIR / "pdfs"
PDF_DIR.mkdir(exist_ok=True)
BOOKMARKS_DIR = BASE_DIR / "bookmarks"
BOOKMARKS_DIR.mkdir(exist_ok=True)
CONV_DIR = BASE_DIR / "conversations"
CONV_DIR.mkdir(exist_ok=True)
MAX_PDF_BYTES = 200 * 1024 * 1024  # 200MB

_ID_RE = re.compile(r"[0-9a-fA-F]{6,64}")


def _valid_id(nid: str) -> bool:
    return bool(_ID_RE.fullmatch(nid or ""))
