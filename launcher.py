"""MBR Dashboard launcher — runs Flask in a background thread and shows the
UI inside a native pywebview window. Designed to be frozen with
PyInstaller --onefile --windowed so the user just double-clicks the .exe
and gets a standalone desktop app with no browser, no console window."""
import os
import sys
import time
import socket
import threading
import urllib.request


def _resource_root():
    """Folder containing bundled templates/ and static/ at runtime."""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


def _user_dir():
    """Folder where editable files (.env, log) live next to the .exe."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _load_env_file(path):
    """Tiny KEY=VALUE parser so we don't need python-dotenv at runtime."""
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


def _pick_port(default=5000):
    """Return the requested port if free, otherwise any free local port."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", default))
        return default
    except OSError:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def _wait_for_server(url, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def main():
    user_dir = _user_dir()
    res_root = _resource_root()
    _load_env_file(os.path.join(user_dir, ".env"))

    os.environ.setdefault("MBR_TEMPLATE_FOLDER", os.path.join(res_root, "templates"))
    os.environ.setdefault("MBR_STATIC_FOLDER", os.path.join(res_root, "static"))

    port = int(os.environ.get("MBR_PORT") or _pick_port())
    url = f"http://127.0.0.1:{port}/"

    # Import app after env is configured so it picks up template paths
    from app import app  # noqa: E402

    def _run_server():
        try:
            app.run(host="127.0.0.1", port=port, debug=False,
                    use_reloader=False, threaded=True)
        except Exception:
            pass

    threading.Thread(target=_run_server, daemon=True).start()
    _wait_for_server(url)

    # Open the dashboard in a native window. Falls back to default browser
    # if pywebview isn't available for some reason.
    try:
        import webview
        webview.create_window(
            "MBR Dashboard",
            url,
            width=1480,
            height=920,
            min_size=(1100, 720),
            text_select=True,
            zoomable=True,
        )
        webview.start()
    except Exception:
        import webbrowser
        webbrowser.open(url)
        # Keep the server alive — block on the daemon thread
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
