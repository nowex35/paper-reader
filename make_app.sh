#!/bin/bash
# Paper Reader.app を「このリポジトリの場所」に合わせて生成する。
#
# .app は環境依存（中のランチャが絶対パス固定）なのでリポジトリには含めない。
# clone した各自のマシンで一度だけ実行すれば、その clone 先のパスで
# 正しく動く Paper Reader.app が手に入る。
#
#   ./make_app.sh
#
# 事前に .venv を作っておくこと（README「セットアップ 2.」参照）。
set -euo pipefail

APPDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$APPDIR/Paper Reader.app"

if [ ! -x "$APPDIR/.venv/bin/python" ]; then
  echo "⚠️  $APPDIR/.venv が見つかりません。先に README の「セットアップ 2.」を実行してください:" >&2
  echo "    python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Paper Reader</string>
  <key>CFBundleDisplayName</key><string>Paper Reader</string>
  <key>CFBundleIdentifier</key><string>local.paperreader</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleExecutable</key><string>paper-reader</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict>
</plist>
PLIST

# ランチャ。$APPDIR はビルド時に確定値で焼き込み、内部の \$APPDIR は実行時参照。
cat > "$APP/Contents/MacOS/paper-reader" <<EOF
#!/bin/bash
# 自動生成: make_app.sh。この clone の場所に固定（移動しても動く）。
APPDIR="$APPDIR"
cd "\$APPDIR" || exit 1
exec "\$APPDIR/.venv/bin/python" -u "\$APPDIR/desktop.py" >> "\$APPDIR/.app.log" 2>&1
EOF
chmod +x "$APP/Contents/MacOS/paper-reader"

echo "✅ 生成しました: $APP"
echo "   ダブルクリックで起動できます。Dock に置きたい場合は /Applications か Dock にドラッグ。"
