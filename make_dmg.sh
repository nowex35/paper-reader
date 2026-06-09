#!/bin/bash
# Naruhodo Plus.dmg を生成する。
# ユーザーは DMG を開いて .app を /Applications にドラッグするだけ。
# 初回起動時に Python venv 構築・Ollama セットアップ・モデル pull が自動で走る。
set -euo pipefail

SRCDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK=$(mktemp -d)
APP="$WORK/Naruhodo Plus.app"
DMG="$SRCDIR/Naruhodo Plus.dmg"

echo "📦 Naruhodo Plus.app をパッケージング中…"

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"

# ---- Info.plist ----
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Naruhodo Plus</string>
  <key>CFBundleDisplayName</key><string>Naruhodo Plus</string>
  <key>CFBundleIdentifier</key><string>local.naruhodo-plus</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleExecutable</key><string>naruhodo</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict>
</plist>
PLIST

# ---- アイコン ----
if [ -f "$SRCDIR/icon.icns" ]; then
  cp "$SRCDIR/icon.icns" "$APP/Contents/Resources/AppIcon.icns"
fi

# ---- ソースコードを同梱 ----
cp "$SRCDIR/server.py" "$SRCDIR/desktop.py" "$SRCDIR/requirements.txt" "$APP/Contents/Resources/app/"
cp -r "$SRCDIR/static" "$APP/Contents/Resources/app/static"
# .env.example を .env として同梱（ユーザーが自分で作らなくて済む）
if [ -f "$SRCDIR/.env.example" ]; then
  cp "$SRCDIR/.env.example" "$APP/Contents/Resources/app/.env.example"
fi

# ---- データディレクトリの雛形 ----
mkdir -p "$APP/Contents/Resources/app/notes"
mkdir -p "$APP/Contents/Resources/app/bookmarks"
mkdir -p "$APP/Contents/Resources/app/conversations"
mkdir -p "$APP/Contents/Resources/app/pdfs"

# ---- ランチャスクリプト（自己完結・初回セットアップ付き） ----
cat > "$APP/Contents/MacOS/naruhodo" <<'LAUNCHER'
#!/bin/bash
# Naruhodo Plus ランチャ — 初回起動時に環境を自動構築する。

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
eval "$(brew shellenv 2>/dev/null)" || true

RESOURCES="$(dirname "$0")/../Resources"
APPDATA="$HOME/Library/Application Support/Naruhodo Plus"
LOG="$APPDATA/naruhodo.log"

mkdir -p "$APPDATA"

log() { echo "[$(date '+%H:%M:%S')] $1" >> "$LOG"; }
log "=== Naruhodo Plus launch ==="

# ---- データ領域を準備 ----
for d in notes bookmarks conversations pdfs static; do
  if [ ! -e "$APPDATA/$d" ]; then
    cp -r "$RESOURCES/app/$d" "$APPDATA/$d" 2>/dev/null || mkdir -p "$APPDATA/$d"
  fi
done
cp "$RESOURCES/app/server.py" "$RESOURCES/app/desktop.py" "$RESOURCES/app/requirements.txt" "$APPDATA/"
cp -r "$RESOURCES/app/static" "$APPDATA/"
if [ -f "$RESOURCES/app/.env.example" ] && [ ! -f "$APPDATA/.env" ]; then
  cp "$RESOURCES/app/.env.example" "$APPDATA/.env"
fi

cd "$APPDATA" || exit 1

# ---- Ollama 確認・インストール ----
ollama_ready() {
  curl -sf http://localhost:11434/api/version &>/dev/null
}

if ! command -v ollama &>/dev/null && [ ! -d "/Applications/Ollama.app" ]; then
  if command -v brew &>/dev/null; then
    log "Installing Ollama via brew..."
    brew install ollama >> "$LOG" 2>&1
  else
    osascript -e 'display dialog "Naruhodo Plus を使うには Ollama が必要です。\n\nhttps://ollama.com/download\n\nからインストールして、もう一度起動してください。" buttons {"OK"} default button "OK" with title "Naruhodo Plus"'
    open "https://ollama.com/download"
    exit 0
  fi
fi

# ---- Ollama 起動 ----
if ! ollama_ready; then
  log "Starting Ollama..."
  if [ -d "/Applications/Ollama.app" ]; then
    open -a Ollama
  elif command -v ollama &>/dev/null; then
    ollama serve >> "$LOG" 2>&1 &
  fi
  for i in $(seq 1 30); do
    ollama_ready && break
    sleep 1
  done
fi

# ---- Python を探す ----
PYTHON=""
for p in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if command -v "$p" &>/dev/null; then
    PYTHON="$p"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  log "ERROR: python3 not found"
  osascript -e 'display dialog "Python 3 が見つかりません。\n\nbrew install python\n\nを実行してから、もう一度起動してください。" buttons {"OK"} default button "OK" with title "Naruhodo Plus"'
  exit 1
fi
log "Using Python: $PYTHON"

# ---- Python venv ----
if [ ! -x "$APPDATA/.venv/bin/python" ]; then
  log "Creating venv..."
  "$PYTHON" -m venv "$APPDATA/.venv" >> "$LOG" 2>&1
  if [ $? -ne 0 ]; then
    log "ERROR: venv creation failed"
    osascript -e 'display dialog "Python 仮想環境の作成に失敗しました。\nログ: '"$LOG"'" buttons {"OK"} default button "OK" with title "Naruhodo Plus"'
    exit 1
  fi
fi

log "Installing dependencies..."
"$APPDATA/.venv/bin/pip" install -q -r "$APPDATA/requirements.txt" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
  log "ERROR: pip install failed"
  osascript -e 'display dialog "依存パッケージのインストールに失敗しました。\nログ: '"$LOG"'" buttons {"OK"} default button "OK" with title "Naruhodo Plus"'
  exit 1
fi

# ---- 起動 ----
log "Starting app..."
exec "$APPDATA/.venv/bin/python" -u "$APPDATA/desktop.py" >> "$LOG" 2>&1
LAUNCHER
chmod +x "$APP/Contents/MacOS/naruhodo"

# ---- DMG 作成 ----
# Applications へのエイリアスを入れる（ドラッグ&ドロップ用）
ln -s /Applications "$WORK/Applications"

rm -f "$DMG"
hdiutil create -volname "Naruhodo Plus" -srcfolder "$WORK" -ov -format UDZO "$DMG" 2>&1

rm -rf "$WORK"

echo ""
echo "✅ $DMG を生成しました"
echo "   配布: DMG を渡すだけ。ユーザーは Naruhodo Plus.app を Applications にドラッグ → ダブルクリック。"
