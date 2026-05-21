# Paper Reader

> Read English papers without leaving the page: select any passage and get an
> instant Japanese translation + glossary + gist, streamed from a **local LLM**.
> Fully offline, no API key, no cloud.

英語論文の PDF をブラウザで開き、**分からない箇所を選択するとその場で
日本語訳＋用語解説＋主旨**が右ペインにストリーム表示されるローカルツール。
コピー → 画面切替 → 貼付 → 復帰 の往復をなくすのが目的。

- **完全ローカル**。PDFもテキストも一切外部に送らない。解説は
  あなたのPC内で動く **ローカルLLM（Ollama）** が生成。APIキー・課金なし。
- 既定モデル `qwen3:4b-instruct`（思考なし・~2.5GB、16GB Macで軽快・旧7B相当の
  品質）。`.env` で変更可（高品質: `qwen3:8b` / 和訳の自然さ重視: `gemma3:12b`）。

## セットアップ

### 1. ローカルLLM（Ollama・初回のみ）

Ollama サーバは**バックグラウンド常駐**させる（ターミナルを開きっぱなしにしない）。

**推奨: 公式アプリ** — <https://ollama.com/download> の `.dmg` を Applications に
入れて一度起動。以後メニューバー常駐＆ログイン時自動起動（`ollama` CLI も使える）。

**または Homebrew:**
```bash
brew install ollama
brew services start ollama   # launchd 常駐。再起動後も自動。ターミナル不要
```

どちらの方法でも最後にモデルを取得（初回のみ・約2.5GB）:
```bash
ollama pull qwen3:4b-instruct
```

> アプリ化後はアプリ側が Ollama 未起動を検知して自動起動するため、
> 配布後のユーザーはこの手順を意識しない。

### 2. アプリ本体

```bash
git clone https://github.com/nowex35/paper-reader.git
cd paper-reader
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env         # 通常は編集不要（既定で qwen3:4b-instruct）
```

> Ollama 未起動 / モデル未取得でもアプリは壊れず、画面にセットアップ
> 手順が表示される。準備後にページ再読み込みで通常動作に切り替わる。

## 起動

### A. ネイティブ窓で起動（推奨・ビルド不要・全環境）

```bash
source .venv/bin/activate
python desktop.py
```

専用ウィンドウで開く。Ollama が未起動なら自動で起動を試み、空きポートで
内蔵サーバを立てる（`desktop.py` が Ollama 確認 → FastAPI を空きポート起動 →
pywebview のネイティブ窓で表示。窓を閉じると終了）。不具合時のログは `.app.log`。

### B. アプリアイコンから（Dock に置きたい人向け・macOS）

`.app` は中のランチャが絶対パス固定で**環境依存**のためリポジトリには含めない。
clone した自分のマシンで一度だけ生成スクリプトを実行する:

```bash
./make_app.sh          # この clone の場所に合わせて Paper Reader.app を生成
```

生成された `Paper Reader.app` をダブルクリックで起動。ターミナル不要。
Dock に常駐させたい場合は `/Applications` か Dock にドラッグ
（ランチャは生成時の絶対パス固定なので移動しても動く）。
リポジトリを別の場所へ移動・再 clone したら `./make_app.sh` を再実行する。

### C. 開発用（ブラウザ + ターミナル）

```bash
source .venv/bin/activate
uvicorn server:app --port 8010
```

ブラウザで <http://localhost:8010> を開く。

> ⚠️ 指定ポートが他プロセスに使われていると別サーバの応答が返ることがある。
> 空いているポート（例: 8010）を使う。使用中ポートの確認:
> `lsof -nP -iTCP:8010 -sTCP:LISTEN`

## 使い方

1. 「PDFを開く」or ドラッグ&ドロップで論文を表示
2. 分からない箇所をドラッグで選択
3. 選択横の **「解説」ボタン**（または **⌘/Ctrl + E**）→ 右ペインに訳と解説
4. 各カードの **「コピー(原文+解説)」** で Notion へそのまま貼れる形をコピー

## メモ機能（自分の言葉でまとめて理解を深める）

すべて 1 画面で完結（タブ遷移・別ページなし）。ChatGPT のような構成。

### 読んだ論文の一覧 — 左サイドバー

- 上部 **「☰ 一覧」** でサイドバーを開閉（状態は記憶。「«」でも閉じる）
- 全メモを更新日順に表示。**タイトル/PDF名で絞り込み**、件数表示
- 項目クリック → そのメモを下部「このメモ」に読み込み（**PDF を開かなくても**
  読み返し・再編集できる）。選択中はハイライト。各項目ホバーで「×」削除
- 保存・削除はサイドバーへ即時反映

### 読みながら書く — 下部メモパネル

- 上部 **「📝 メモ」** で開閉（⌘/Ctrl+S でも開いて保存）。上端ドラッグで高さ調整
- パネル内は **「📝 メモ」** と **「🧩 summary」** の2タブ（状態は記憶）
- **メモ** タブ: タイトル＋自由本文（Markdown可）。**入力すると自動保存**、
  **「プレビュー」** で整形表示
- **summary** タブ: 後述の落合フォーマット6項目を「質問→メモ欄」形式で並べ、
  自分の言葉でまとめる。各項目とも Markdown 可・自動保存・プレビュー対応
- 論文の同定は PDF 内容ハッシュなので、同じ論文を開けば
  （ファイル名を変えても）前回のメモが自動で復元される

保存先は **`notes/<slug>-<id>.md`**（frontmatter 付き Markdown）。
メモ本文と summary は同じファイル内に分離して保存される（summary は
`<!--paper-reader:summary-->` 以降の専用セクション）。ツール外からも読め、
`git` で履歴管理でき、ブラウザを消しても残る。

### summary タブの落合フォーマットについて

「🧩 summary」タブの6項目は、論文を素早く構造的に把握するための
**落合陽一氏の論文まとめフォーマット（通称「落合フォーマット」）** を借用している。

> 1. どんなもの？
> 2. 先行研究と比べてどこがすごい？
> 3. 技術や手法のキモはどこ？
> 4. どうやって有効だと検証した？
> 5. 議論はある？
> 6. 次に読むべき論文は？
>
> — 落合陽一「先端技術とメディア表現1 #FTMA15」
> <https://www.slideshare.net/Ochyai/1-ftma15>

このフォーマットは本ツールの考案ではなく、上記を出典とする参考です。

## 設定

| 項目 | 場所 | 既定 |
|---|---|---|
| モデル | `.env` の `OLLAMA_MODEL` | `qwen3:4b-instruct` |
| Ollama エンドポイント | `.env` の `OLLAMA_HOST` | `http://localhost:11434` |
| 解説の構成・口調 | `server.py` の `SYSTEM_INSTRUCTION` | 訳／用語／主旨の3節 |

> APIキーは不要。解説はすべてローカルの Ollama で生成され、PDF・選択テキストは
> 一切外部に送信されない。

## 今後の拡張候補（未実装）

- pdf.js をローカル同梱して完全オフライン化
- 数式の KaTeX レンダリング
- 解説カードのエクスポート機能（Markdown / クリップボード整形の拡充）

## ライセンス

[MIT License](LICENSE) © 2026 Hayate Aizawa
