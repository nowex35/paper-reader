#!/bin/bash
# Naruhodo.dmg を生成する。
# ユーザーは DMG を開いて .app を /Applications にドラッグするだけ。
# 初回起動時に Python venv 構築・Ollama セットアップ・モデル pull が自動で走る。
#
# ── 署名 & 公証（Gatekeeper 警告なし配布）──
#
# 環境変数で Developer ID を渡すと、正式署名 → Apple 公証 → staple まで自動実行:
#
#   DEVELOPER_ID="Developer ID Application: Your Name (TEAMID)" \
#   NOTARY_PROFILE="naruhodo" \
#   ./make_dmg.sh
#
# 事前準備（1回だけ）:
#   1. Apple Developer Program に登録（$99/年）
#      https://developer.apple.com/programs/
#   2. Xcode → Settings → Accounts で Apple ID を追加
#   3. Keychain Access に「Developer ID Application」証明書をインストール
#      (Xcode → Settings → Accounts → Manage Certificates → "+" → Developer ID Application)
#   4. 公証用プロファイルを作成:
#      xcrun notarytool store-credentials "naruhodo" \
#        --apple-id "your@email.com" \
#        --team-id "TEAMID" \
#        --password "app-specific-password"
#      (App-specific password: https://appleid.apple.com → サインインとセキュリティ → アプリ用パスワード)
#
# 環境変数を指定しなければ従来通り ad-hoc 署名（自分用ビルド）。
#
set -euo pipefail

SRCDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK=$(mktemp -d)
APP="$WORK/Naruhodo.app"
DMG="$SRCDIR/Naruhodo.dmg"

VERSION="${VERSION:-$(git -C "$SRCDIR" describe --tags --abbrev=0 2>/dev/null | sed 's/^v//' || echo '0.0.0')}"
BUILD="${BUILD:-$(git -C "$SRCDIR" rev-list HEAD --count 2>/dev/null || echo '1')}"
SPARKLE_PUBLIC_KEY="${SPARKLE_PUBLIC_KEY:-}"

echo "📦 Naruhodo.app をパッケージング中…"

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"

# ---- Info.plist ----
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Naruhodo</string>
  <key>CFBundleDisplayName</key><string>Naruhodo</string>
  <key>CFBundleIdentifier</key><string>local.naruhodo</string>
  <key>CFBundleVersion</key><string>$BUILD</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleExecutable</key><string>naruhodo</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict>
</plist>
PLIST

if [ -n "$SPARKLE_PUBLIC_KEY" ]; then
  /usr/libexec/PlistBuddy -c "Add :SUFeedURL string 'https://nowex35.github.io/paper-reader/appcast.xml'" "$APP/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Add :SUPublicEDKey string '$SPARKLE_PUBLIC_KEY'" "$APP/Contents/Info.plist"
fi

# ---- アイコン ----
if [ -f "$SRCDIR/icon.icns" ]; then
  cp "$SRCDIR/icon.icns" "$APP/Contents/Resources/AppIcon.icns"
fi

# ---- ソースコードを同梱 ----
cp "$SRCDIR/server.py" "$SRCDIR/desktop.py" "$SRCDIR/requirements.txt" "$APP/Contents/Resources/app/"
rsync -a --exclude '__pycache__' "$SRCDIR/routers/" "$APP/Contents/Resources/app/routers/"
cp -r "$SRCDIR/static" "$APP/Contents/Resources/app/static"
if [ -f "$SRCDIR/Naruhodo.png" ]; then
  cp "$SRCDIR/Naruhodo.png" "$APP/Contents/Resources/app/Naruhodo.png"
fi
# .env.example を .env として同梱（ユーザーが自分で作らなくて済む）
if [ -f "$SRCDIR/.env.example" ]; then
  cp "$SRCDIR/.env.example" "$APP/Contents/Resources/app/.env.example"
fi

# ---- データディレクトリの雛形 ----
mkdir -p "$APP/Contents/Resources/app/notes"
mkdir -p "$APP/Contents/Resources/app/bookmarks"
mkdir -p "$APP/Contents/Resources/app/conversations"
mkdir -p "$APP/Contents/Resources/app/pdfs"

# ---- Sparkle.framework（自動アップデート） ----
if [ -n "$SPARKLE_PUBLIC_KEY" ]; then
  SPARKLE_VER="2.9.3"
  echo "🔄 Sparkle $SPARKLE_VER をダウンロード中…"
  mkdir -p "$WORK/sparkle"
  curl -sL "https://github.com/sparkle-project/Sparkle/releases/download/$SPARKLE_VER/Sparkle-$SPARKLE_VER.tar.xz" \
    | tar xJ -C "$WORK/sparkle"
  mkdir -p "$APP/Contents/Frameworks"
  cp -R "$WORK/sparkle/Sparkle.framework" "$APP/Contents/Frameworks/"
fi

# ---- ランチャスクリプト（自己完結・初回セットアップ付き） ----
cat > "$APP/Contents/MacOS/naruhodo" <<'LAUNCHER'
#!/bin/bash
# Naruhodo ランチャ — 初回起動時に環境を自動構築する。

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
eval "$(brew shellenv 2>/dev/null)" || true

BUNDLE_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
export NARUHODO_BUNDLE_PATH="$BUNDLE_DIR"
RESOURCES="$BUNDLE_DIR/Contents/Resources"
APPDATA="$HOME/Library/Application Support/Naruhodo"
LOG="$APPDATA/naruhodo.log"

mkdir -p "$APPDATA"

log() { echo "[$(date '+%H:%M:%S')] $1" >> "$LOG"; }
log "=== Naruhodo launch ==="

# ---- データ領域を準備 ----
for d in notes bookmarks conversations pdfs static; do
  if [ ! -e "$APPDATA/$d" ]; then
    cp -r "$RESOURCES/app/$d" "$APPDATA/$d" 2>/dev/null || mkdir -p "$APPDATA/$d"
  fi
done
cp "$RESOURCES/app/server.py" "$RESOURCES/app/desktop.py" "$RESOURCES/app/requirements.txt" "$APPDATA/"
rm -rf "$APPDATA/routers"
cp -r "$RESOURCES/app/routers" "$APPDATA/routers"
[ -f "$RESOURCES/app/Naruhodo.png" ] && cp "$RESOURCES/app/Naruhodo.png" "$APPDATA/"
rm -rf "$APPDATA/static"
cp -r "$RESOURCES/app/static" "$APPDATA/static"
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
    osascript -e 'display dialog "Naruhodo を使うには Ollama が必要です。\n\nhttps://ollama.com/download\n\nからインストールして、もう一度起動してください。" buttons {"OK"} default button "OK" with title "Naruhodo"'
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
  osascript -e 'display dialog "Python 3 が見つかりません。\n\nbrew install python\n\nを実行してから、もう一度起動してください。" buttons {"OK"} default button "OK" with title "Naruhodo"'
  exit 1
fi
log "Using Python: $PYTHON"

# ---- Python venv ----
if [ ! -x "$APPDATA/.venv/bin/python" ]; then
  log "Creating venv..."
  "$PYTHON" -m venv "$APPDATA/.venv" >> "$LOG" 2>&1
  if [ $? -ne 0 ]; then
    log "ERROR: venv creation failed"
    osascript -e 'display dialog "Python 仮想環境の作成に失敗しました。\nログ: '"$LOG"'" buttons {"OK"} default button "OK" with title "Naruhodo"'
    exit 1
  fi
fi

log "Installing dependencies..."
"$APPDATA/.venv/bin/pip" install -q -r "$APPDATA/requirements.txt" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
  log "ERROR: pip install failed"
  osascript -e 'display dialog "依存パッケージのインストールに失敗しました。\nログ: '"$LOG"'" buttons {"OK"} default button "OK" with title "Naruhodo"'
  exit 1
fi

# ---- 起動 ----
log "Starting app..."
exec "$APPDATA/.venv/bin/python" -u "$APPDATA/desktop.py" >> "$LOG" 2>&1
LAUNCHER
chmod +x "$APP/Contents/MacOS/naruhodo"

# ---- 署名 ----
DEVELOPER_ID="${DEVELOPER_ID:-}"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"

if [ -n "$DEVELOPER_ID" ]; then
  echo "🔏 Developer ID で署名中…"
  if [ -d "$APP/Contents/Frameworks/Sparkle.framework" ]; then
    codesign --force --deep --options runtime --sign "$DEVELOPER_ID" "$APP/Contents/Frameworks/Sparkle.framework" 2>&1
  fi
  codesign --force --options runtime --sign "$DEVELOPER_ID" "$APP" 2>&1
  codesign --verify --deep --strict "$APP" 2>&1
  echo "   署名OK: $DEVELOPER_ID"
else
  echo "⚠️  DEVELOPER_ID 未設定 → ad-hoc 署名（自分用ビルド。他人に渡すと Gatekeeper 警告が出ます）"
  codesign --force --deep --sign - "$APP" 2>&1 || echo "   codesign スキップ"
  xattr -cr "$APP" 2>/dev/null || true
fi

# ---- Sparkle 用 zip（CI で EdDSA 署名される） ----
if [ -n "$SPARKLE_PUBLIC_KEY" ]; then
  echo "📦 Sparkle アップデート用 zip を作成中…"
  ditto -c -k --sequesterRsrc --keepParent "$APP" "$SRCDIR/Naruhodo.zip"
fi

# ---- DMG 作成（create-dmg で背景画像＋アイコン配置） ----
rm -f "$DMG"

CREATE_DMG_ARGS=(
  --volname "Naruhodo"
  --window-pos 200 120
  --window-size 660 400
  --icon-size 96
  --icon "Naruhodo.app" 165 200
  --app-drop-link 495 200
)
if [ -f "$SRCDIR/dmg-background.png" ]; then
  CREATE_DMG_ARGS+=(--background "$SRCDIR/dmg-background.png")
fi

create-dmg "${CREATE_DMG_ARGS[@]}" "$DMG" "$WORK" 2>&1 || true
rm -rf "$WORK"

# ---- 公証 & staple（Developer ID 署名時のみ） ----
if [ -n "$DEVELOPER_ID" ] && [ -n "$NOTARY_PROFILE" ]; then
  echo ""
  echo "📤 Apple に公証を提出中（数分かかります）…"
  xcrun notarytool submit "$DMG" \
    --keychain-profile "$NOTARY_PROFILE" \
    --wait 2>&1
  echo "📎 公証チケットを DMG に添付中…"
  xcrun stapler staple "$DMG" 2>&1
  echo ""
  echo "✅ $DMG を生成しました（署名 + 公証済み 🎉）"
  echo "   Gatekeeper 警告なしで配布できます。"
elif [ -n "$DEVELOPER_ID" ]; then
  echo ""
  echo "✅ $DMG を生成しました（署名済み・公証なし）"
  echo "   ⚠️  NOTARY_PROFILE を設定すれば公証も自動実行されます。"
  echo "   公証なしでも署名済みなので、Gatekeeper 警告は「開発元を確認」程度に軽減されます。"
else
  echo ""
  echo "✅ $DMG を生成しました（ad-hoc 署名・自分用）"
  echo "   他人に渡す場合は DEVELOPER_ID と NOTARY_PROFILE を設定してビルドしてください。"
fi
echo "   配布: DMG を渡すだけ。ユーザーは Naruhodo.app を Applications にドラッグ → ダブルクリック。"
