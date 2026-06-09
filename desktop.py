"""Naruhodo — デスクトップ起動ランチャ。

アプリアイコンから起動され、(1)Ollama を必要なら立ち上げ（未インストール
なら自動インストール）、(2)モデルを必要なら自動 pull、(3)ローカルサーバを
空きポートで起動し、(4)pywebview のネイティブ窓で開く。
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
DEFAULT_MODEL = "qwen3:4b-instruct"


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
    """Homebrew で Ollama をインストール。Homebrew が無ければ諦める。"""
    if not shutil.which("brew"):
        return False
    try:
        print("[naruhodo] Installing Ollama via Homebrew...")
        subprocess.run(["brew", "install", "ollama"], check=True,
                        capture_output=True, timeout=300)
        return True
    except Exception:  # noqa: BLE001
        return False


def ensure_ollama() -> None:
    """Ollama を確保: 未インストール→インストール、未起動→起動。"""
    if not _ollama_installed():
        if not _install_ollama():
            print("[naruhodo] Ollama auto-install failed; will show setup guide")
            return
    if _ollama_up():
        return
    try:
        if os.path.isdir("/Applications/Ollama.app"):
            subprocess.Popen(["open", "-a", "Ollama"])
        elif shutil.which("ollama"):
            subprocess.Popen(["ollama", "serve"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        return
    for _ in range(30):
        if _ollama_up():
            return
        time.sleep(0.5)


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


def ensure_model() -> None:
    """必要なモデルが無ければ自動で pull する。"""
    if not _ollama_up():
        return
    model = _current_model()
    if _model_present(model):
        return
    ollama = shutil.which("ollama")
    if not ollama:
        return
    print(f"[naruhodo] Pulling model {model}...")
    try:
        subprocess.run([ollama, "pull", model], check=True, timeout=600)
    except Exception as e:  # noqa: BLE001
        print(f"[naruhodo] Model pull failed: {e}")


PORT = _free_port()


def _run_server() -> None:
    os.chdir(APP_DIR)
    from server import app

    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # 非メインスレッド用
    server.run()


def _wait_server() -> bool:
    for _ in range(80):
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/api/health", timeout=1
            )
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    return False


def main() -> None:
    print(f"[naruhodo] starting on port {PORT}")
    ensure_ollama()
    ensure_model()
    threading.Thread(target=_run_server, daemon=True).start()
    if not _wait_server():
        print("[naruhodo] server did not start in time")
        return
    import webview

    webview.create_window(
        "Naruhodo Plus",
        f"http://127.0.0.1:{PORT}",
        width=1440,
        height=900,
        min_size=(900, 600),
    )
    webview.start()  # 窓を閉じるまでブロック。閉じたらプロセス終了


if __name__ == "__main__":
    main()
