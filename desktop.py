"""Naruhodo — デスクトップ起動ランチャ。

アプリアイコンから起動され、(1)ローディング画面を即表示、(2)Ollama を
必要なら立ち上げ（未インストールなら自動インストール）、(3)モデルを必要
なら自動 pull、(4)ローカルサーバを空きポートで起動し、(5)本体に遷移する。
ターミナル不要。ログは .app.log に出る。
"""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request

import uvicorn

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = "qwen3.5:4b"


def _icon_data_uri() -> str:
    import base64
    icon_path = os.path.join(APP_DIR, "Naruhodo.png")
    if not os.path.isfile(icon_path):
        static_icon = os.path.join(APP_DIR, "static", "icon.png")
        if os.path.isfile(static_icon):
            icon_path = static_icon
        else:
            return ""
    with open(icon_path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def _build_loading_html() -> str:
    icon_uri = _icon_data_uri()
    icon_tag = f'<img class="icon" src="{icon_uri}" width="80" height="80" alt="" />' if icon_uri else '<div class="icon">NH</div>'
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif;
    background: #f0f1f3;
    color: #1c1c1e;
    gap: 24px;
  }}
  .icon {{ border-radius: 16px; }}
  .title {{ font-size: 22px; font-weight: 700; }}
  #status {{
    font-size: 14px; color: #6a6a70;
    min-height: 20px;
    transition: opacity .2s;
  }}
  .bar-wrap {{
    width: 260px; height: 6px;
    background: #dcdce0; border-radius: 3px;
    overflow: hidden;
  }}
  #bar {{
    height: 100%; width: 0%;
    background: linear-gradient(90deg, #3b82f6, #1c6dff);
    border-radius: 3px;
    transition: width .4s ease;
  }}
</style>
</head>
<body>
  {icon_tag}
  <div class="title">Naruhodo</div>
  <div id="status">起動準備中…</div>
  <div class="bar-wrap"><div id="bar"></div></div>
</body>
</html>"""


LOADING_HTML = _build_loading_html()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _ollama_up() -> bool:
    try:
        urllib.request.urlopen("http://localhost:11434/api/version", timeout=1.5)
        return True
    except Exception:  # noqa: BLE001
        return False


def _ollama_installed() -> bool:
    return (
        shutil.which("ollama") is not None
        or os.path.isdir("/Applications/Ollama.app")
    )


def _install_ollama() -> bool:
    if not shutil.which("brew"):
        return False
    try:
        print("[naruhodo] Installing Ollama via Homebrew...")
        subprocess.run(["brew", "install", "ollama"], check=True,
                        capture_output=True, timeout=300)
        return True
    except Exception:  # noqa: BLE001
        return False


def _current_model() -> str:
    try:
        settings = os.path.join(APP_DIR, ".settings.json")
        m = json.loads(open(settings, encoding="utf-8").read()).get("model", "")
        return (m or "").strip() or DEFAULT_MODEL
    except Exception:  # noqa: BLE001
        return DEFAULT_MODEL


def _model_present(model: str) -> bool:
    try:
        r = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        data = json.loads(r.read())
        names = [m.get("name", "") for m in data.get("models", [])]
        base = model.split(":")[0]
        return model in names or any(n.split(":")[0] == base for n in names)
    except Exception:  # noqa: BLE001
        return False


def _pull_model_with_progress(window, model: str) -> None:
    """Ollama の /api/pull を使い、ダウンロード進捗をローディング画面に反映する。"""
    import http.client
    conn = http.client.HTTPConnection("localhost", 11434, timeout=600)
    body = json.dumps({"name": model}).encode()
    conn.request("POST", "/api/pull", body=body,
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    buf = b""
    for chunk in iter(lambda: resp.read(1024), b""):
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            status = obj.get("status", "")
            total = obj.get("total", 0)
            completed = obj.get("completed", 0)
            if total and completed:
                pct = int(52 + (completed / total) * 16)
                size_mb = completed / 1024 / 1024
                total_mb = total / 1024 / 1024
                msg = f"{status}（{size_mb:.0f}/{total_mb:.0f} MB）"
            else:
                pct = 52
                msg = status or f"モデルをダウンロード中（{model}）…"
            _update_loading(window, msg, min(pct, 68))
    conn.close()


PORT = _free_port()


def _run_server() -> None:
    os.chdir(APP_DIR)
    from server import app

    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    server.run()


def _server_up() -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=1)
        return True
    except Exception:  # noqa: BLE001
        return False


def _update_loading(window, status: str, pct: int) -> None:
    try:
        window.evaluate_js(
            f'document.getElementById("status").textContent = "{status}";'
            f'document.getElementById("bar").style.width = "{pct}%";'
        )
    except Exception:  # noqa: BLE001
        pass



def _boot(window) -> None:
    """バックグラウンドで全セットアップを行い、完了後に本体へ遷移する。"""
    # 1. Ollama 確認・起動
    _update_loading(window, "Ollama を確認中…", 10)
    if not _ollama_installed():
        _update_loading(window, "Ollama をインストール中…", 15)
        _install_ollama()

    if not _ollama_up():
        _update_loading(window, "Ollama を起動中…", 20)
        try:
            if os.path.isdir("/Applications/Ollama.app"):
                subprocess.Popen(["open", "-a", "Ollama"])
            elif shutil.which("ollama"):
                subprocess.Popen(["ollama", "serve"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            pass
        for i in range(30):
            if _ollama_up():
                break
            _update_loading(window, "Ollama を起動中…", 20 + i)
            time.sleep(0.5)

    _update_loading(window, "モデルを確認中…", 50)

    # 2. モデル確認・pull（Ollama API で進捗を取得）
    if _ollama_up():
        model = _current_model()
        if not _model_present(model):
            _update_loading(window, f"モデルをダウンロード中（{model}）…", 52)
            try:
                _pull_model_with_progress(window, model)
            except Exception as e:  # noqa: BLE001
                print(f"[naruhodo] Model pull failed: {e}")

    _update_loading(window, "アプリを起動中…", 70)

    # 3. サーバー起動・待機
    threading.Thread(target=_run_server, daemon=True).start()
    for i in range(80):
        if _server_up():
            break
        _update_loading(window, "アプリを起動中…", 70 + min(i, 25))
        time.sleep(0.25)

    _update_loading(window, "準備完了", 100)
    time.sleep(0.3)

    # 4. 本体へ遷移
    window.load_url(f"http://127.0.0.1:{PORT}")


def main() -> None:
    print(f"[naruhodo] starting on port {PORT}")
    import webview

    window = webview.create_window(
        "Naruhodo",
        html=LOADING_HTML,
        width=1440,
        height=900,
        min_size=(900, 600),
    )

    webview.start(func=_boot, args=(window,))


if __name__ == "__main__":
    main()
