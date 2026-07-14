"""Naruhodo — デスクトップ起動ランチャ。

アプリアイコンから起動され、(1)ローディング画面を即表示、(2)Ollama を
必要なら立ち上げ（未インストールなら自動インストール）、(3)モデルを必要
なら自動 pull、(4)ローカルサーバを空きポートで起動し、(5)本体に遷移する。
ターミナル不要。ログは .app.log に出る。
"""

import atexit
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import uvicorn

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = "qwen3.5:4b"

_desktop_translations = {}


def _load_desktop_translations():
    global _desktop_translations
    lang = "ja"
    try:
        settings = os.path.join(APP_DIR, ".settings.json")
        lang = json.loads(open(settings, encoding="utf-8").read()).get("ui_language", "ja")
    except Exception:  # noqa: BLE001
        pass
    path = os.path.join(APP_DIR, "static", "locales", f"{lang}.json")
    try:
        with open(path, encoding="utf-8") as f:
            _desktop_translations = json.loads(f.read())
    except Exception:  # noqa: BLE001
        path_ja = os.path.join(APP_DIR, "static", "locales", "ja.json")
        try:
            with open(path_ja, encoding="utf-8") as f:
                _desktop_translations = json.loads(f.read())
        except Exception:  # noqa: BLE001
            pass
    return lang


def _dt(key, **kwargs):
    s = _desktop_translations.get(key, key)
    for k, v in kwargs.items():
        s = s.replace(f"{{{k}}}", str(v))
    return s

_we_started_ollama = False
_ollama_proc: subprocess.Popen | None = None
_cleanup_done = False


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


def _build_loading_html(lang="ja") -> str:
    icon_uri = _icon_data_uri()
    icon_tag = f'<img class="icon" src="{icon_uri}" width="80" height="80" alt="" />' if icon_uri else '<div class="icon">NH</div>'
    return f"""<!DOCTYPE html>
<html lang="{lang}">
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
  <div id="status">{_dt("desktop.preparing")}</div>
  <div class="bar-wrap"><div id="bar"></div></div>
</body>
</html>"""


LOADING_HTML = None


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


def _win_ollama_app() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA", ""),
                        "Programs", "Ollama", "ollama app.exe")


def _ollama_installed() -> bool:
    if shutil.which("ollama") is not None:
        return True
    if sys.platform == "darwin":
        return os.path.isdir("/Applications/Ollama.app")
    if sys.platform == "win32":
        return os.path.isfile(_win_ollama_app())
    return False


def _install_ollama() -> bool:
    if sys.platform == "win32":
        if not shutil.which("winget"):
            return False
        try:
            print("[naruhodo] Installing Ollama via winget...")
            subprocess.run(["winget", "install", "-e", "--id", "Ollama.Ollama",
                            "--silent", "--accept-package-agreements",
                            "--accept-source-agreements"],
                           check=True, capture_output=True, timeout=600)
            return True
        except Exception:  # noqa: BLE001
            return False
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
                msg = status or _dt("desktop.downloading_model", model=model)
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
        safe_status = json.dumps(status)
        window.evaluate_js(
            f'document.getElementById("status").textContent = {safe_status};'
            f'document.getElementById("bar").style.width = "{pct}%";'
        )
    except Exception:  # noqa: BLE001
        pass



_sparkle_updater = None


def _init_sparkle() -> None:
    """Sparkle を初期化する。webview.start() の前に呼ぶこと。"""
    global _sparkle_updater
    bundle_path = os.environ.get("NARUHODO_BUNDLE_PATH", "")
    sparkle_path = os.path.join(bundle_path, "Contents", "Frameworks", "Sparkle.framework")
    if not bundle_path or not os.path.isdir(sparkle_path):
        return
    try:
        import objc
        objc.loadBundle("Sparkle", globals(), bundle_path=sparkle_path)
        NSBundle = objc.lookUpClass("NSBundle")
        SPUUpdater = objc.lookUpClass("SPUUpdater")
        SPUStandardUserDriver = objc.lookUpClass("SPUStandardUserDriver")

        host = NSBundle.bundleWithPath_(bundle_path)
        driver = SPUStandardUserDriver.alloc().initWithHostBundle_delegate_(host, None)
        _sparkle_updater = SPUUpdater.alloc() \
            .initWithHostBundle_applicationBundle_userDriver_delegate_(
                host, host, driver, None)
        _sparkle_updater.startUpdater_(None)
        print(f"[naruhodo] Sparkle updater ready (feed={_sparkle_updater.feedURL()})")
    except Exception as e:  # noqa: BLE001
        print(f"[naruhodo] Sparkle init skipped: {e}")


def _boot(window) -> None:
    """バックグラウンドで全セットアップを行い、完了後に本体へ遷移する。"""
    # 1. Ollama 確認・起動
    _update_loading(window, _dt("desktop.checking_ollama"), 10)
    if not _ollama_installed():
        _update_loading(window, _dt("desktop.installing_ollama"), 15)
        _install_ollama()

    if not _ollama_up():
        _update_loading(window, _dt("desktop.starting_ollama"), 20)
        try:
            if sys.platform == "darwin" and os.path.isdir("/Applications/Ollama.app"):
                subprocess.Popen(["open", "-a", "Ollama"])
            elif sys.platform == "win32" and os.path.isfile(_win_ollama_app()):
                subprocess.Popen([_win_ollama_app()])
            elif shutil.which("ollama"):
                global _ollama_proc
                _ollama_proc = subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            global _we_started_ollama
            _we_started_ollama = True
        except Exception:  # noqa: BLE001
            pass
        for i in range(30):
            if _ollama_up():
                break
            _update_loading(window, _dt("desktop.starting_ollama"), 20 + i)
            time.sleep(0.5)

    _update_loading(window, _dt("desktop.checking_model"), 50)

    # 2. モデル確認・pull（Ollama API で進捗を取得）
    if _ollama_up():
        model = _current_model()
        if not _model_present(model):
            _update_loading(window, _dt("desktop.downloading_model", model=model), 52)
            try:
                _pull_model_with_progress(window, model)
            except Exception as e:  # noqa: BLE001
                print(f"[naruhodo] Model pull failed: {e}")

    _update_loading(window, _dt("desktop.starting_app"), 70)

    # 3. サーバー起動・待機
    threading.Thread(target=_run_server, daemon=True).start()
    for i in range(80):
        if _server_up():
            break
        _update_loading(window, _dt("desktop.starting_app"), 70 + min(i, 25))
        time.sleep(0.25)

    _update_loading(window, _dt("desktop.ready"), 100)
    time.sleep(0.3)

    # 4. 本体へ遷移
    window.load_url(f"http://127.0.0.1:{PORT}")

    # 5. Sparkle アップデートチェック
    if _sparkle_updater is not None:
        _sparkle_updater.checkForUpdatesInBackground()
        print("[naruhodo] Sparkle update check triggered")


def _cleanup_ollama() -> None:
    """アプリ終了時にモデルをアンロードし、自分で起動した Ollama を停止する。"""
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True
    try:
        model = _current_model()
        if model and _ollama_up():
            req = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=json.dumps({"model": model, "keep_alive": 0}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=3)
            print(f"[naruhodo] unloaded model {model}")
    except Exception:  # noqa: BLE001
        pass

    if _we_started_ollama:
        try:
            if _ollama_proc is not None and _ollama_proc.poll() is None:
                _ollama_proc.terminate()
                _ollama_proc.wait(timeout=5)
                print("[naruhodo] stopped ollama serve")
            elif sys.platform == "darwin":
                subprocess.run(["osascript", "-e",
                                'tell application "Ollama" to quit'],
                               timeout=5, capture_output=True)
                print("[naruhodo] quit Ollama.app")
        except Exception:  # noqa: BLE001
            pass


def _handle_term(signum, _frame):
    _cleanup_ollama()
    raise SystemExit(0)


def main() -> None:
    global LOADING_HTML
    print(f"[naruhodo] starting on port {PORT}")
    ui_lang = _load_desktop_translations()
    LOADING_HTML = _build_loading_html(ui_lang)
    _init_sparkle()
    atexit.register(_cleanup_ollama)
    signal.signal(signal.SIGTERM, _handle_term)

    import webview

    window = webview.create_window(
        "Naruhodo",
        html=LOADING_HTML,
        width=1440,
        height=900,
        min_size=(900, 600),
    )

    def _on_closing():
        _cleanup_ollama()

    window.events.closing += _on_closing
    webview.start(func=_boot, args=(window,))


if __name__ == "__main__":
    main()
