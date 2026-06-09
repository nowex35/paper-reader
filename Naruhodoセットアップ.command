#!/bin/bash
# Naruhodo ワンコマンドセットアップ
# ターミナルに貼るだけで、アプリが使える状態になります。
#
#   curl -sL <URL> | bash   または   ./setup.sh
#
set -euo pipefail

APPDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APPDIR"

echo ""
echo "==================================="
echo "  📄 Naruhodo セットアップ"
echo "==================================="
echo ""

# ---- 1. Homebrew 確認 ----
if ! command -v brew &>/dev/null; then
  echo "⚠️  Homebrew が見つかりません。インストールします…"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)"
fi

# ---- 2. Python 確認 ----
if ! command -v python3 &>/dev/null; then
  echo "📦 Python をインストールしています…"
  brew install python
fi

# ---- 3. Ollama インストール ----
if ! command -v ollama &>/dev/null; then
  echo "📦 Ollama をインストールしています…"
  brew install ollama
fi

# ---- 4. Ollama 起動 ----
if ! curl -sf http://localhost:11434/api/version &>/dev/null; then
  echo "🚀 Ollama を起動しています…"
  if [ -d "/Applications/Ollama.app" ]; then
    open -a Ollama
  else
    ollama serve &>/dev/null &
  fi
  echo "   起動を待機中…"
  for i in $(seq 1 30); do
    if curl -sf http://localhost:11434/api/version &>/dev/null; then
      break
    fi
    sleep 1
  done
fi

# ---- 5. モデル取得 ----
MODEL="qwen3:4b-instruct"
# .settings.json にモデル指定があればそれを使う
if [ -f "$APPDIR/.settings.json" ]; then
  SAVED=$(python3 -c "import json; print(json.load(open('$APPDIR/.settings.json')).get('model',''))" 2>/dev/null || true)
  if [ -n "$SAVED" ]; then
    MODEL="$SAVED"
  fi
fi

if ! ollama list 2>/dev/null | grep -q "$(echo "$MODEL" | cut -d: -f1)"; then
  echo "📥 LLMモデル ($MODEL) をダウンロードしています（初回のみ・約3GB）…"
  ollama pull "$MODEL"
fi

# ---- 6. Python 仮想環境 ----
if [ ! -x "$APPDIR/.venv/bin/python" ]; then
  echo "🐍 Python 環境をセットアップしています…"
  python3 -m venv "$APPDIR/.venv"
fi
echo "📦 依存パッケージをインストールしています…"
"$APPDIR/.venv/bin/pip" install -q -r "$APPDIR/requirements.txt"

# ---- 7. アプリ生成 ----
echo "🔨 Naruhodo.app を生成しています…"
bash "$APPDIR/make_app.sh"

echo ""
echo "==================================="
echo "  ✅ セットアップ完了！"
echo "==================================="
echo ""
echo "  Naruhodo.app をダブルクリックして起動してください。"
echo "  Dock にドラッグすればいつでもすぐ起動できます。"
echo ""
