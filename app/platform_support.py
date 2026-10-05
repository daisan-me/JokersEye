"""OS differences (Windows / macOS / Linux) kept in one place, standard library only."""
import os
from pathlib import Path
import subprocess
import sys
import webbrowser

APP_DIR_NAME = "JokersEye"


def default_data_dir(root, environ=None, platform=None, home=None):
    """Where the SQLite database, backups, exports and logs live.

    Windows keeps the 1.0 location (%LOCALAPPDATA%\\JokersEye) so existing data is reused.
    """
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else Path(home)
    if platform.startswith("win"):
        base = environ.get("LOCALAPPDATA")
        return Path(base) / APP_DIR_NAME if base else Path(root) / "data"
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_DIR_NAME
    base = environ.get("XDG_DATA_HOME")
    return (Path(base) if base else home / ".local" / "share") / APP_DIR_NAME


def open_external(url):
    """Open an http(s) address in the default browser. Anything else is ignored."""
    if not isinstance(url, str) or not url.lower().startswith(("https://", "http://")):
        return False
    return bool(webbrowser.open(url))


def launch_browser(url):
    """Fallback UI when the desktop window is unavailable: an app-style window on Windows
    (Chrome/Edge), otherwise the default browser."""
    if sys.platform.startswith("win"):
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        ]
        for browser in candidates:
            if browser.exists():
                subprocess.Popen([str(browser), "--app=" + url, "--window-size=1380,920"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
    webbrowser.open(url)


def show_error(title, message):
    """Best-effort native error dialog; always also printed to stderr."""
    print("%s: %s" % (title, message), file=sys.stderr)
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, str(message), str(title), 0x10)
        elif sys.platform == "darwin":
            script = 'display dialog (item 1 of argv) with title (item 2 of argv) buttons {"OK"} default button 1 with icon stop'
            subprocess.run(["osascript", "-e", "on run argv", "-e", script, "-e", "end run", str(message), str(title)],
                           timeout=120, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
