"""Joker's eye desktop window for Windows and macOS (pywebview: WebView2 / WKWebView).

Replaces the Windows-only C# launcher. Responsibilities are unchanged:
  * start the local service on a free port with a per-launch session token
  * show the UI in a native window; open external links in the default browser
  * keep the session alive with a heartbeat, even while minimized
  * single instance (a second launch only brings the first window forward)
  * provide the visible public-data browser that the collector asks for
  * stop the local service when the window is closed
"""
import argparse
import json
import logging
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402
from platform_support import default_data_dir, open_external, show_error  # noqa: E402

APP_TITLE = "Joker's eye"
APP_ID = "JokersEye.Desktop"
SOURCE_TITLE = "Joker's eye · 公開データ取得ブラウザー"
SOURCE_HOST = "min-repo.com"
ROOT = server.ROOT

EXTERNAL_LINK_JS = """(()=>{if(window.__jokerExternal)return;window.__jokerExternal=true;
const send=u=>{try{const x=new URL(u,location.href);if(x.origin!==location.origin&&/^https?:$/.test(x.protocol)){window.pywebview.api.open_external(x.href);return true;}}catch(e){}return false;};
document.addEventListener('click',e=>{const a=e.target&&e.target.closest?e.target.closest('a[href]'):null;if(a&&send(a.href)){e.preventDefault();e.stopPropagation();}},true);
window.open=u=>{send(String(u));return null;};})()"""
READY_JS = "!!document.querySelector('h1') && !!document.querySelector('table') && document.readyState !== 'loading'"
EMPTY_JS = "document.readyState === 'complete' && document.scripts.length === 0 && !!document.body && document.body.innerHTML.trim() === ''"
STATUS_JS = "(()=>{const n=performance.getEntriesByType('navigation')[0];return n&&n.responseStatus?n.responseStatus:0;})()"
FOLLOW_JS = ("(()=>{const target=new URL(%s,location.href).href;const link=Array.from(document.querySelectorAll('a[href]'))"
             ".find(a=>a.href===target&&!a.target);if(!link)return false;link.click();return true;})()")


def truthy(value):
    """run_js returns native bool (WebView2) or NSNumber-like ints/strings (WKWebView)."""
    return value is True or value == 1 or value == "true"


def run_script(window, script):
    """Run a JS expression as-is. `evaluate_js` wraps the code in eval(), which pages
    whose CSP lacks 'unsafe-eval' (including this app's own) reject on macOS."""
    return window.run_js(script)


def source_url_allowed(url):
    """Only ordinary https pages on the public data site may be shown."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.hostname == SOURCE_HOST


def same_page(left, right):
    a, b = urlsplit(left), urlsplit(right)
    return (a.scheme.lower(), (a.hostname or "").lower(), unquote(a.path), a.query) == \
           (b.scheme.lower(), (b.hostname or "").lower(), unquote(b.path), b.query)


class SingleInstance:
    """Process-wide lock in the data folder (released by the OS if the app dies)."""

    def __init__(self, folder):
        self.path = Path(folder) / "desktop.lock"
        self.handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self):
        if self.handle:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                self.handle.close()
            except OSError:
                pass
            self.handle = None


class Client:
    """Talks to the local service with the session token (never through a proxy)."""

    def __init__(self, url, token):
        self.url, self.token = url, token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, payload=None, timeout=3):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url + "api/" + path, data=data, method="GET" if data is None else "POST",
                                     headers={"X-Joker-Token": self.token, "Content-Type": "application/json"})
        with self.opener.open(req, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")


class Bridge:
    """Exposed to the app page as window.pywebview.api."""

    def open_external(self, url):
        return open_external(url)


class SourceBrowser:
    """Visible ordinary browser window that renders public pages for the collector.

    It never exports cookies/tokens, never works around access restrictions
    (HTTP 401/403/429, CAPTCHA, authentication) and only visits https://min-repo.com.
    """

    def __init__(self, webview, client, folder):
        self.webview, self.client, self.folder = webview, client, Path(folder)
        self.window = None
        self.loaded = threading.Event()
        self.busy = False
        self.disposed = False
        self.last_task = None
        self.stop_event = threading.Event()

    def log(self, message):
        try:
            with open(self.folder / "source-browser.log", "a", encoding="utf-8") as file:
                file.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + message + "\n")
        except OSError:
            pass

    def ensure_window(self):
        if self.window is None:
            self.window = self.webview.create_window(SOURCE_TITLE, html="<!doctype html><title></title>", width=1000, height=750)
            self.window.events.loaded += lambda: self.loaded.set()
            self.window.events.closing += self.on_closing
        else:
            self.window.show()

    def on_closing(self):
        if self.disposed:
            return True
        self.window.hide()
        try:
            self.client.request("scrape/stop", {})
        except (OSError, ValueError):
            pass
        return False

    def evaluate(self, script):
        try:
            return run_script(self.window, script)
        except Exception as ex:  # page is navigating
            self.log("evaluate-js " + type(ex).__name__)
            return None

    def current_url(self):
        try:
            return self.window.get_current_url() or ""
        except Exception:
            return ""

    def run(self):
        while not self.stop_event.wait(0.75):
            if self.busy or self.disposed:
                continue
            self.busy = True
            try:
                self.poll()
            except Exception as ex:
                self.log("poll-error " + type(ex).__name__ + ": " + str(ex))
            finally:
                self.busy = False

    def poll(self):
        try:
            task = self.client.request("scrape/browser-task")
        except (OSError, ValueError):
            return
        if "id" not in task or task["id"] == self.last_task:
            return
        task_id, url, error = task["id"], task.get("url", ""), None
        self.last_task = task_id
        try:
            html = self.fetch(url)
        except Exception as ex:
            html, error = None, str(ex)
        if self.disposed:
            return
        payload = {"id": task_id, "html": html} if error is None else {"id": task_id, "error": error}
        try:
            self.client.request("scrape/browser-result", payload, timeout=15)
        except (OSError, ValueError):
            pass

    def fetch(self, url):
        if not source_url_allowed(url):
            raise RuntimeError("公開取得元のURLではありません。")
        self.ensure_window()
        self.loaded.clear()
        status, status_text = 0, "loading"
        # Follow a genuine link of the currently rendered page when available; this keeps
        # ordinary navigation/referrers. No headers, cookies or checks are manufactured.
        followed = self.evaluate(FOLLOW_JS % json.dumps(url)) if self.current_url().startswith("https://") else False
        if not truthy(followed):
            self.window.load_url(url)
        for i in range(110):
            if self.disposed or self.stop_event.is_set():
                return None
            time.sleep(0.5)
            if not same_page(self.current_url(), url):
                continue
            if self.loaded.is_set() and status == 0:
                status = int(self.evaluate(STATUS_JS) or 0)
                status_text = "HTTP %s" % status if status else "HTTP 不明"
            if status in (401, 403, 429):
                raise RuntimeError("公開サイトが取得を制限しました（HTTP %s）。再試行せず停止します。" % status)
            if truthy(self.evaluate(READY_JS)):
                html = self.evaluate("document.documentElement.outerHTML")
                if isinstance(html, str):
                    return html
            # A successful but genuinely empty document is transient: reload once, normally.
            if i == 12 and status in (0, 200) and truthy(self.evaluate(EMPTY_JS)):
                self.log("empty-document ordinary-reload-once " + url)
                time.sleep(3)
                self.loaded.clear()
                self.evaluate("location.reload()")
        if self.disposed:
            return None
        self.log("unreadable-page %s %s" % (url, status_text))
        raise RuntimeError("公開ページを表示できませんでした（%s）。未掲載・閲覧制限・ブラウザー確認を確認してください。制限の回避は行いません。" % status_text)

    def dispose(self):
        self.disposed = True
        self.stop_event.set()
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass


def set_app_identity(root):
    """Taskbar/Dock identity and icon. Best effort; the app works without it."""
    icon_ico, icon_png = root / "web" / "icon.ico", root / "web" / "icon.png"
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        elif sys.platform == "darwin":
            from AppKit import NSApplication, NSImage
            image = NSImage.alloc().initWithContentsOfFile_(str(icon_png))
            if image:
                NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception:
        logging.info("App identity could not be applied", exc_info=True)
    return icon_ico


def apply_windows_icon(icon_ico):
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, APP_TITLE)
        for size, which in ((16, 0), (32, 1)):  # ICON_SMALL, ICON_BIG
            handle = user32.LoadImageW(None, str(icon_ico), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            if hwnd and handle:
                user32.SendMessageW(hwnd, 0x80, which, handle)  # WM_SETICON
    except Exception:
        logging.info("Window icon could not be applied", exc_info=True)


def bring_existing_to_front(folder):
    statefile = Path(folder) / "desktop-session.json"
    for _ in range(30):
        try:
            session = json.loads(statefile.read_text(encoding="utf-8"))
            Client(session["url"], session["token"]).request("focus", {})
            return
        except (OSError, ValueError, KeyError):
            time.sleep(0.1)


def run_diagnostics(window, client, path):
    """Same checks as the old `--diagnostics` mode (page/connection text, map grid)."""
    time.sleep(1.5)
    page = run_script(window, "JSON.stringify({connection:document.querySelector('#connection').textContent,version:document.querySelector('.rail-bottom small').textContent,title:document.title})")
    Path(path).write_text('{"page":%s}' % page, encoding="utf-8")
    run_script(window, "document.querySelector('[data-page=map]').click()")
    time.sleep(1.5)
    result = run_script(window, "JSON.stringify({grid:!!document.querySelector('.machine-seat-grid'),seats:document.querySelectorAll('.machine-seat').length,physical:!!document.querySelector('.physical-floor-svg')})")
    Path(str(path) + ".map.json").write_text(result, encoding="utf-8")
    client.request("shutdown", {})


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=os.environ.get("JOKERS_EYE_DATA", str(default_data_dir(ROOT))))
    parser.add_argument("--diagnostics", default="")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    folder = Path(args.data)
    folder.mkdir(parents=True, exist_ok=True)
    instance = SingleInstance(folder)
    if not instance.acquire():
        bring_existing_to_front(folder)
        return 0
    try:
        import webview
    except ImportError:
        instance.release()
        raise RuntimeError("pywebviewがありません。`python launch.py` で起動するか、`pip install -r requirements.txt` を実行してください。")
    store = server.Store(folder)
    logging.basicConfig(filename=folder / "application.log", level=logging.INFO, format="%(asctime)s %(message)s")
    host = server.Host(("127.0.0.1", 0), store)
    url = "http://127.0.0.1:%s/" % host.server_port
    statefile = folder / "desktop-session.json"
    temp = folder / ("desktop-session-%s.tmp" % host.server_port)
    temp.write_text(json.dumps({"url": url, "token": host.token}), encoding="utf-8")
    os.replace(temp, statefile)
    serving = threading.Thread(target=host.serve_forever, daemon=True)
    serving.start()
    client = Client(url, host.token)
    icon_ico = set_app_identity(ROOT)
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(APP_TITLE, url=url + "#" + host.token, js_api=Bridge(), width=1380, height=920,
                                   min_size=(900, 650), background_color="#0f1519")
    source = SourceBrowser(webview, client, folder)
    closing = threading.Event()

    def on_loaded():
        run_script(window, EXTERNAL_LINK_JS)

    def on_closing():
        closing.set()
        source.dispose()

    window.events.loaded += on_loaded
    window.events.closing += on_closing

    def background():
        apply_windows_icon(icon_ico)
        threading.Thread(target=source.run, daemon=True).start()
        if args.diagnostics:
            threading.Thread(target=run_diagnostics, args=(window, client, args.diagnostics), daemon=True).start()
        beat = 0.0
        while not closing.is_set() and serving.is_alive():
            now = time.monotonic()
            if now - beat >= 20:
                beat = now
                try:
                    client.request("heartbeat", {})
                except (OSError, ValueError):
                    pass
            if host.focus_requested.is_set():
                host.focus_requested.clear()
                try:
                    window.restore()
                    window.show()
                except Exception:
                    pass
            time.sleep(0.25)
        if not closing.is_set():  # "アプリを終了" in the page stopped the service
            closing.set()
            source.dispose()
            try:
                window.destroy()
            except Exception:
                pass

    try:
        webview.start(background, debug=bool(args.debug), storage_path=str(folder / "WebView2"), private_mode=False)
    finally:
        closing.set()
        source.dispose()
        store.collector.stop()
        try:
            host.shutdown()
        except Exception:
            pass
        serving.join(timeout=4)
        host.server_close()
        try:
            current = json.loads(statefile.read_text(encoding="utf-8"))
            if secrets.compare_digest(current.get("token", ""), host.token):
                statefile.unlink()
        except (OSError, ValueError, AttributeError):
            pass
        instance.release()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as ex:
        folder = Path(os.environ.get("JOKERS_EYE_DATA", str(default_data_dir(ROOT))))
        try:
            folder.mkdir(parents=True, exist_ok=True)
            with open(folder / "desktop.log", "a", encoding="utf-8") as log:
                log.write("%s %s: %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), type(ex).__name__, ex))
        except OSError:
            pass
        show_error("Joker's eye — 起動エラー", str(ex))
        sys.exit(1)
