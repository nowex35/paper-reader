Naruhodo for Windows
====================

■ 必要なもの
- Windows 10/11（WebView2 ランタイム。通常はプリインストール済み）
- Python 3.10 以上（https://www.python.org/downloads/
  インストーラで「Add python.exe to PATH」にチェックを入れてください）
- Ollama（解説機能に必要。https://ollama.com/download
  未インストールの場合、初回起動時に winget での自動インストールを試みます）

■ 使い方
1. この zip を書き込み可能な場所（例: ドキュメント）に展開する
2. Naruhodo.bat をダブルクリック
   - 初回のみ依存パッケージのセットアップが走ります（数分）
   - 以降はそのままアプリが起動します

■ 質問機能（オプション）
- OpenAI Codex (ChatGPT): APIキー不要。ChatGPT アカウント（Plus/Pro など）で
  ログインして使えます。Codex CLI のインストールが必要です:
    npm install -g @openai/codex
- Gemini / OpenAI / Claude: 各社の APIキーを設定画面から登録

■ トラブルシューティング
- 起動しない場合は、同じフォルダの naruhodo.log を確認してください
- ウィンドウが真っ白な場合は WebView2 ランタイムをインストールしてください:
  https://developer.microsoft.com/microsoft-edge/webview2/

■ 注意
- Windows 版には自動アップデート機能はありません。
  新しいバージョンは https://github.com/nowex35/paper-reader/releases から
  ダウンロードして、展開したフォルダを置き換えてください
  （notes / pdfs 等のデータフォルダをコピーすれば引き継げます）
