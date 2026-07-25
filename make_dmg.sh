#!/bin/bash
# Naruhodo.dmg を生成する。
# ユーザーは DMG を開いて .app を /Applications にドラッグするだけ。
# Python ランタイム・依存パッケージ・Ollama をすべてバンドルに同梱するため、
# ユーザーは何も別途インストールしなくてよい（管理者パスワードも不要）。
# 初回起動時に走るのはモデルの pull のみ（アプリ内に進捗バーが出る）。
#
# Apple Silicon 専用。pydantic-core / jiter / cffi 等の wheel が arm64 単一で
# universal2 バンドルを作れないため、Intel Mac は対象外。
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

# 同梱する Python ランタイム（python-build-standalone / Apple Silicon 専用）
PBS_RELEASE="20260718"
PBS_PYTHON="3.14.6"
PY_MINOR="${PBS_PYTHON%.*}"

# 同梱する Ollama のバージョン（更新時はここだけ変える）
OLLAMA_VERSION="0.30.11"

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
  <key>LSRequiresNativeExecution</key><true/>
  <key>LSArchitecturePriority</key>
  <array><string>arm64</string></array>
</dict>
</plist>
PLIST

if [ -n "$SPARKLE_PUBLIC_KEY" ]; then
  /usr/libexec/PlistBuddy -c "Add :SUFeedURL string 'https://nowex35.github.io/paper-reader/appcast.xml'" "$APP/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Add :SUPublicEDKey string '$SPARKLE_PUBLIC_KEY'" "$APP/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Add :SUEnableAutomaticChecks bool true" "$APP/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Add :SUScheduledCheckInterval integer 3600" "$APP/Contents/Info.plist"
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

# ---- Python ランタイムを同梱 ----
# ユーザー環境の python3 / pip に依存せず、初回起動時のセットアップも不要にする。
# python-build-standalone は再配置可能なので、そのまま Resources に置けば動く。
PYROOT="$APP/Contents/Resources/python"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_RELEASE/cpython-$PBS_PYTHON+$PBS_RELEASE-aarch64-apple-darwin-install_only.tar.gz"

echo "🐍 Python $PBS_PYTHON を同梱中…"
mkdir -p "$APP/Contents/Resources"
curl -fsSL "$PBS_URL" | tar xz -C "$APP/Contents/Resources"

echo "📚 依存パッケージをインストール中…"
"$PYROOT/bin/python3" -m pip install -q -r "$SRCDIR/requirements.txt"
# Codex SDK は依存の同梱バイナリ(約200MB)を避けて SDK のみ入れる。
# codex バイナリはシステムにインストール済みのもの（brew install codex）を検出して使う。
"$PYROOT/bin/python3" -m pip install -q --no-deps openai-codex \
  || echo "   ⚠️  openai-codex のインストールに失敗（質問機能の Codex プロバイダのみ影響）"

echo "✂️  未使用ファイルを削除中…"
PYLIB="$PYROOT/lib/python$PY_MINOR"
rm -rf "$PYLIB/site-packages/PyObjCTest" "$PYLIB/site-packages/pip" "$PYLIB/site-packages"/pip-*.dist-info
rm -rf "$PYLIB/idlelib" "$PYLIB/tkinter" "$PYLIB/turtledemo" "$PYLIB/ensurepip" "$PYLIB/pydoc_data"
rm -rf "$PYROOT/include" "$PYROOT/share"
rm -rf "$PYROOT/lib/tcl"* "$PYROOT/lib/tk"* "$PYROOT/lib/itcl"* "$PYROOT/lib/thread"* "$PYROOT/lib/pkgconfig"
rm -f "$PYROOT"/lib/libtcl*.dylib "$PYROOT"/lib/libtk*.dylib "$PYROOT"/lib/*.a
find "$PYLIB" -name "_tkinter*.so" -delete

# universal2 の wheel を arm64 のみに薄くする（Apple Silicon 専用ビルドのため）
find "$PYROOT" \( -name "*.so" -o -name "*.dylib" \) -print0 | while IFS= read -r -d '' f; do
  case "$(lipo -archs "$f" 2>/dev/null)" in
    *x86_64*) lipo -thin arm64 "$f" -output "$f.thin" 2>/dev/null && mv "$f.thin" "$f" ;;
  esac
done

# 同梱 Python が正しく動くか検証（壊れたバンドルを配らないため）
"$PYROOT/bin/python3" -c "import webview, fastapi, uvicorn, httpx, dotenv, pydantic, openai, anthropic; from google import genai" \
  || { echo "❌ 同梱 Python の import 検証に失敗しました"; exit 1; }
echo "   同梱 Python: $(du -sh "$PYROOT" | cut -f1)"

# ---- Ollama を同梱 ----
# ユーザーに Ollama を別途インストールさせない（/Applications への書き込みも
# 管理者パスワードも不要になる）。Apple Silicon では ollama と llama-server の
# 2バイナリだけで Metal 推論が動く。同梱の dylib 群は x86_64 専用なので入れない。
OLLAMADIR="$APP/Contents/Resources/ollama"
OLLAMA_TGZ="https://github.com/ollama/ollama/releases/download/v$OLLAMA_VERSION/ollama-darwin.tgz"

echo "🦙 Ollama $OLLAMA_VERSION を同梱中…"
OLLAMA_WORK="$WORK/ollama-dist"
mkdir -p "$OLLAMA_WORK" "$OLLAMADIR"
curl -fsSL "$OLLAMA_TGZ" | tar xz -C "$OLLAMA_WORK"
for f in ollama llama-server; do
  # 公式が将来 arm64 単一配布になっても壊れないように、fat のときだけ thin する
  case "$(lipo -archs "$OLLAMA_WORK/$f")" in
    *x86_64*) lipo -thin arm64 "$OLLAMA_WORK/$f" -output "$OLLAMADIR/$f" ;;
    *)        cp "$OLLAMA_WORK/$f" "$OLLAMADIR/$f" ;;
  esac
  chmod +x "$OLLAMADIR/$f"
done
rm -rf "$OLLAMA_WORK"

# MIT ライセンスの表示を同梱する（再配布の条件）
curl -fsSL -o "$OLLAMADIR/LICENSE" \
  "https://raw.githubusercontent.com/ollama/ollama/v$OLLAMA_VERSION/LICENSE" \
  || { echo "❌ Ollama の LICENSE を取得できませんでした"; exit 1; }
echo "   同梱 Ollama: $(du -sh "$OLLAMADIR" | cut -f1)"

# ---- Sparkle.framework（自動アップデート） ----
if [ -n "$SPARKLE_PUBLIC_KEY" ]; then
  SPARKLE_VER="2.9.3"
  echo "🔄 Sparkle $SPARKLE_VER をダウンロード中…"
  mkdir -p "$WORK/sparkle"
  curl -sL "https://github.com/sparkle-project/Sparkle/releases/download/$SPARKLE_VER/Sparkle-$SPARKLE_VER.tar.xz" \
    | tar xJ -C "$WORK/sparkle"
  mkdir -p "$APP/Contents/Frameworks"
  cp -R "$WORK/sparkle/Sparkle.framework" "$APP/Contents/Frameworks/"
  rm -rf "$WORK/sparkle"

  # Sparkle の日本語 UI 文言をカスタマイズ
  JA_LPROJ="$APP/Contents/Frameworks/Sparkle.framework/Resources/ja.lproj"
  mkdir -p "$JA_LPROJ"
  cat > "$JA_LPROJ/Sparkle.strings" <<'STRINGS'
"Automatically download and install updates in the future" = "今後は自動でアップデートをインストールする";
"Skip This Version" = "あとで通知";
"Remind Me Later" = "あとで通知";
STRINGS
fi

# ---- ネイティブランチャ（Sparkle XPC 署名検証のため） ----
echo "🔨 ネイティブランチャをコンパイル中…"
cc -o "$APP/Contents/MacOS/naruhodo" \
   -arch arm64 -mmacosx-version-min=11.0 \
   "$SRCDIR/launcher.c"

# ---- セットアップスクリプト（ネイティブランチャから呼ばれる） ----
cat > "$APP/Contents/Resources/naruhodo-setup.sh" <<'SETUP'
#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
eval "$(brew shellenv 2>/dev/null)" || true

BUNDLE_DIR="$NARUHODO_BUNDLE_PATH"
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

# 旧バージョンの .env には既定値の OLLAMA_HOST が書かれている。そのままだと
# 「ユーザーが接続先を明示した」と解釈され同梱 Ollama が使われないので外す。
# 自分で別の接続先を書いた人の設定は触らない。
if [ -f "$APPDATA/.env" ]; then
  sed -i '' -E 's|^OLLAMA_HOST=http://localhost:11434/?$|# &|' "$APPDATA/.env"
fi

cd "$APPDATA" || exit 1

# Ollama は同梱されており、desktop.py が進捗表示付きで起動する。
# ここでインストールや起動は行わない（/Applications への書き込みも不要）。

# Python ランタイムと依存パッケージはバンドルに同梱済み。
# 旧バージョンが作った venv が残っていれば掃除する（もう使わない）。
if [ -d "$APPDATA/.venv" ]; then
  log "Removing obsolete venv..."
  rm -rf "$APPDATA/.venv"
fi

log "Starting app..."
SETUP
chmod +x "$APP/Contents/Resources/naruhodo-setup.sh"

# ---- 署名 ----
DEVELOPER_ID="${DEVELOPER_ID:-}"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"

if [ -n "$DEVELOPER_ID" ]; then
  echo "🔏 Developer ID で署名中…"

  # 同梱 Python は Hardened Runtime 下で自前の .so を dlopen するため、
  # ライブラリ検証の緩和が必要。
  # DMG に混入しないよう $WORK の外に置く（$WORK は create-dmg のソースになる）
  ENTITLEMENTS="$(mktemp -t naruhodo-entitlements).plist"
  cat > "$ENTITLEMENTS" <<'ENT'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
  <key>com.apple.security.cs.allow-jit</key><true/>
</dict>
</plist>
ENT

  # 同梱 Python の全バイナリを内側から署名する（入れ子のコードは先に署名が要る）
  echo "   同梱 Python のバイナリに署名中…"
  find "$PYROOT" \( -name "*.so" -o -name "*.dylib" \) -print0 \
    | xargs -0 -n 20 codesign --force --timestamp --options runtime \
        --entitlements "$ENTITLEMENTS" --sign "$DEVELOPER_ID" 2>&1 | tail -3
  find "$PYROOT/bin" -type f -perm +111 -print0 \
    | xargs -0 -n 20 codesign --force --timestamp --options runtime \
        --entitlements "$ENTITLEMENTS" --sign "$DEVELOPER_ID" 2>&1 | tail -3

  # 同梱 Ollama は他社署名のまま入っているので、自分の Developer ID で署名し直す
  echo "   同梱 Ollama のバイナリに署名中…"
  codesign --force --timestamp --options runtime \
    --entitlements "$ENTITLEMENTS" --sign "$DEVELOPER_ID" \
    "$OLLAMADIR/ollama" "$OLLAMADIR/llama-server" 2>&1 | tail -2

  if [ -d "$APP/Contents/Frameworks/Sparkle.framework" ]; then
    codesign --force --deep --options runtime --sign "$DEVELOPER_ID" "$APP/Contents/Frameworks/Sparkle.framework" 2>&1
  fi
  codesign --force --timestamp --options runtime \
    --entitlements "$ENTITLEMENTS" --sign "$DEVELOPER_ID" "$APP" 2>&1
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
  SUBMIT_OUT=$(xcrun notarytool submit "$DMG" \
    --keychain-profile "$NOTARY_PROFILE" \
    --wait 2>&1)
  echo "$SUBMIT_OUT"
  SUBMISSION_ID=$(echo "$SUBMIT_OUT" | grep '  id:' | head -1 | awk '{print $2}')
  if echo "$SUBMIT_OUT" | grep -q "status: Invalid"; then
    echo "❌ 公証が拒否されました。詳細ログ:"
    xcrun notarytool log "$SUBMISSION_ID" --keychain-profile "$NOTARY_PROFILE" 2>&1
    exit 1
  fi
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
