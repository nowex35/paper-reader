"""Paper Reader — デスクトップ起動ランチャ。

アプリアイコンから起動され、(1)Ollama を必要なら立ち上げ、(2)ローカル
サーバを空きポートで起動し、(3)pywebview のネイティブ窓で開く。
ターミナル不要。ログは .app.log に出る。
"""

import os
import socket
import subprocess
import threading
import time
import urllib.request

import uvicorn

APP_DIR = os.path.dirname(os.path.abspath(__file__))


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


def ensure_ollama() -> None:
    """Ollama 未起動なら公式アプリを起動して待つ（無くても致命的でない）。"""
    if _ollama_up():
        return
    try:
        subprocess.Popen(["open", "-a", "Ollama"])
    except Exception:  # noqa: BLE001
        return
    for _ in range(30):
        if _ollama_up():
            return
        time.sleep(0.5)


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
    print(f"[paper-reader] starting on port {PORT}")
    ensure_ollama()
    threading.Thread(target=_run_server, daemon=True).start()
    if not _wait_server():
        print("[paper-reader] server did not start in time")
        return
    import webview

    webview.create_window(
        "Paper Reader",
        f"http://127.0.0.1:{PORT}",
        width=1440,
        height=900,
        min_size=(900, 600),
    )
    webview.start()  # 窓を閉じるまでブロック。閉じたらプロセス終了


if __name__ == "__main__":
    main()
