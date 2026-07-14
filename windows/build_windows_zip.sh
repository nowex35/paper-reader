#!/bin/bash
# Naruhodo-Windows.zip を生成する。
# Windows 版は macOS 版と同じ「ソース同梱・初回起動時に venv セットアップ」方式。
# パッケージングはファイル収集のみなので macOS / Linux のどこでも実行できる。
# 実行: bash windows/build_windows_zip.sh  → リポジトリルートに Naruhodo-Windows.zip
set -euo pipefail

SRCDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK=$(mktemp -d)
STAGE="$WORK/Naruhodo"
ZIP="$SRCDIR/Naruhodo-Windows.zip"

echo "📦 Naruhodo-Windows.zip をパッケージング中…"

mkdir -p "$STAGE"

# ---- ソースコード ----
cp "$SRCDIR/server.py" "$SRCDIR/desktop.py" "$SRCDIR/requirements.txt" "$STAGE/"
rsync -a --exclude '__pycache__' "$SRCDIR/routers/" "$STAGE/routers/"
rsync -a "$SRCDIR/static/" "$STAGE/static/"
[ -f "$SRCDIR/Naruhodo.png" ] && cp "$SRCDIR/Naruhodo.png" "$STAGE/"
# .env.example を .env として同梱（macOS 版セットアップと同じ挙動）
[ -f "$SRCDIR/.env.example" ] && cp "$SRCDIR/.env.example" "$STAGE/.env"

# ---- データディレクトリの雛形 ----
mkdir -p "$STAGE/notes" "$STAGE/bookmarks" "$STAGE/conversations" "$STAGE/pdfs"

# ---- ランチャと README（cmd/メモ帳のために CRLF に変換） ----
sed 's/$/\r/' "$SRCDIR/windows/Naruhodo.bat" > "$STAGE/Naruhodo.bat"
sed 's/$/\r/' "$SRCDIR/windows/README.txt" > "$STAGE/README.txt"

# ---- zip 化 ----
rm -f "$ZIP"
(cd "$WORK" && zip -qr "$ZIP" Naruhodo)
rm -rf "$WORK"

echo "✅ $ZIP を生成しました"
echo "   配布: zip を渡すだけ。ユーザーは展開して Naruhodo.bat をダブルクリック。"
