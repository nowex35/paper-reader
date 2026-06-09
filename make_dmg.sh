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
# Naruhodo ランチャ — 初回起動時に環境を自動構築する。

RESOURCES="$(dirname "$0")/../Resources"
APPDATA="$HOME/Library/Application Support/Naruhodo Plus"

# ---- データ領域を準備 ----
mkdir -p "$APPDATA"
for d in notes bookmarks conversations pdfs static; do
  if [ ! -e "$APPDATA/$d" ]; then
    cp -r "$RESOURCES/app/$d" "$APPDATA/$d" 2>/dev/null || mkdir -p "$APPDATA/$d"
  fi
done
# ソースは常に最新を上書き
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
    brew install ollama 2>&1
  else
    # Homebrew も無い場合は公式インストーラを案内
    osascript -e 'display dialog "Naruhodo を使うには Ollama が必要です。\n\nhttps://ollama.com/download\n\nからインストールして、もう一度起動してください。" buttons {"OK"} default button "OK" with title "Naruhodo Plus"'
    open "https://ollama.com/download"
    exit 0
  fi
fi

# ---- Ollama 起動 ----
if ! ollama_ready; then
  if [ -d "/Applications/Ollama.app" ]; then
    open -a Ollama
  elif command -v ollama &>/dev/null; then
    ollama serve &>/dev/null &
  fi
  for i in $(seq 1 30); do
    ollama_ready && break
    sleep 1
  done
fi

# ---- Python venv ----
if [ ! -x "$APPDATA/.venv/bin/python" ]; then
  python3 -m venv "$APPDATA/.venv" 2>&1
fi
"$APPDATA/.venv/bin/pip" install -q -r "$APPDATA/requirements.txt" 2>&1

# ---- 起動 ----
exec "$APPDATA/.venv/bin/python" -u "$APPDATA/desktop.py" >> "$APPDATA/naruhodo.log" 2>&1
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
