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
from urllib.parse import parse_qsl, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402
from platform_support import default_data_dir, open_external, show_error  # noqa: E402

APP_TITLE = "Joker's eye"
APP_ID = "JokersEye.Desktop"
SOURCE_TITLE = "Joker's eye · 公開データ取得ブラウザー"
SOURCE_HOST = "min-repo.com"
# User decision (2026-10-07): at most 240 pages a minute (the collector caps the overall rate),
# read by 12 windows that each start at most one page every 1.5 seconds.
SOURCE_WINDOWS = 12
SOURCE_COLUMNS = 4
PAGE_CYCLE = 1.5
# Every run_js of every window goes through pywebview's one UI thread, so a window asks its page
# rarely: once a second while loading, then right after the page reports it has loaded.
LOADING_CHECK = 1.0
LOADED_CHECK = 0.25
# The app's WebViews may reach only the public data site and this app itself. Ads, trackers and
# other third-party loads fail at name resolution, so a page is ready sooner (user decision, 2026-10-07).
ALLOWED_HOSTS = (SOURCE_HOST, "*." + SOURCE_HOST, "127.0.0.1", "localhost")
WEBVIEW2_ARGUMENTS = '--disable-features=ElasticOverscroll --host-resolver-rules="MAP * ~NOTFOUND, %s"' % \
    ", ".join("EXCLUDE " + host for host in ALLOWED_HOSTS)


def limit_webview_hosts(environ=None):
    """WebView2 reads extra browser arguments from this variable when its environment is created;
    pywebview sets them for every window alike, so this is the one place to add them."""
    environ = os.environ if environ is None else environ
    environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = WEBVIEW2_ARGUMENTS
ROOT = server.ROOT

EXTERNAL_LINK_JS = """(()=>{if(window.__jokerExternal)return;window.__jokerExternal=true;
const send=u=>{try{const x=new URL(u,location.href);if(x.origin!==location.origin&&/^https?:$/.test(x.protocol)){window.pywebview.api.open_external(x.href);return true;}}catch(e){}return false;};
document.addEventListener('click',e=>{const a=e.target&&e.target.closest?e.target.closest('a[href]'):null;if(a&&send(a.href)){e.preventDefault();e.stopPropagation();}},true);
window.open=u=>{send(String(u));return null;};})()"""
READINESS_JS = (ROOT / "web" / "source-readiness.js").read_text(encoding="utf-8")
EMPTY_JS = "document.readyState === 'complete' && document.scripts.length === 0 && !!document.body && document.body.innerHTML.trim() === ''"
# One script call per check: every run_js of every window goes through the one UI thread, so
# separate calls for the URL, status, readiness and HTML queued up behind each other with 12 windows.
PROBE_JS = ("(()=>{%s\nconst n=performance.getEntriesByType('navigation')[0];"
            "const restricted=sourceAccessRestricted(document);const ready=!restricted&&!!sourceDocumentReady(document,%s);"
            "return JSON.stringify({href:location.href,status:n&&n.responseStatus?n.responseStatus:0,restricted:restricted,"
            "ready:ready,empty:" + EMPTY_JS + ",html:ready?document.documentElement.outerHTML:null,"
            "timing:n?[n.requestStart,n.responseStart,n.responseEnd,n.domInteractive].map(Math.round):null});})()")
FOLLOW_JS = ("(()=>{if(location.protocol!=='https:')return false;const target=new URL(%s,location.href).href;const link=Array.from(document.querySelectorAll('a[href]'))"
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
    """Same page regardless of percent-encoding. WebView2 reports the URL through
    System.Uri.ToString(), which unescapes non-ASCII query text (?kishu=L東京喰種), while
    the collector requests the encoded form (?kishu=L%E6%9D%B1...)."""
    a, b = urlsplit(left), urlsplit(right)
    return (a.scheme.lower(), (a.hostname or "").lower(), unquote(a.path), parse_qsl(a.query, keep_blank_values=True)) == \
           (b.scheme.lower(), (b.hostname or "").lower(), unquote(b.path), parse_qsl(b.query, keep_blank_values=True))


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


class SourceWindow:
    """One visible ordinary browser window that renders public pages for the collector.

    It never exports cookies/tokens, never works around access restrictions
    (HTTP 401/403/429, CAPTCHA, authentication) and only visits https://min-repo.com.
    """
    LOG_LOCK = threading.Lock()

    def __init__(self, webview, client, folder, index=0, stop_event=None):
        self.webview, self.client, self.folder, self.index = webview, client, Path(folder), index
        self.window = None
        self.loaded = threading.Event()
        self.disposed = False
        self.last_task = None
        self.last_timing = None  # navigation timing of the last page read (for the slow-page log)
        self.stop_event = stop_event or threading.Event()

    def log(self, message):
        try:
            with self.LOG_LOCK, open(self.folder / "source-browser.log", "a", encoding="utf-8") as file:
                file.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + "[%d] " % (self.index + 1) + message + "\n")
        except OSError:
            pass

    def ensure_window(self):
        if self.window is None:
            # Tile the windows 4 x 3 so that none hides another (a fully covered page may be throttled).
            column, row = self.index % SOURCE_COLUMNS, self.index // SOURCE_COLUMNS
            self.window = self.webview.create_window("%s %d/%d" % (SOURCE_TITLE, self.index + 1, SOURCE_WINDOWS),
                                                     html="<!doctype html><title></title>", width=470, height=340,
                                                     x=10 + column * 475, y=10 + row * 350)
            self.window.events.loaded += lambda: self.loaded.set()
            self.window.events.closing += self.on_closing
        else:
            self.window.show()

    def on_closing(self):
        if self.disposed or self.window is None:  # app exit, or close_window() after the run ended
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

    def probe(self, task):
        """URL, HTTP status, restriction, readiness and (when ready) the HTML, in one call."""
        result = self.evaluate(PROBE_JS % (READINESS_JS, json.dumps(task, ensure_ascii=False)))
        try:
            return json.loads(result) if isinstance(result, str) else None
        except ValueError:
            return None

    def current_url(self):
        # The page's own location.href keeps the browser's percent-encoding; pywebview's
        # get_current_url() on WebView2 is an unescaped System.Uri string.
        href = self.evaluate("location.href")
        if isinstance(href, str) and href:
            return href
        try:
            return str(self.window.get_current_url() or "")
        except Exception:
            return ""

    def close_window(self):
        """Close the source browser once the collector has finished (or was stopped)."""
        window, self.window = self.window, None
        if window is None:
            return
        try:
            window.destroy()
        except Exception as ex:
            self.log("close-window " + type(ex).__name__)

    def run(self):
        while not self.stop_event.is_set() and not self.disposed:
            try:
                started = self.poll()
            except Exception as ex:
                self.log("poll-error " + type(ex).__name__ + ": " + str(ex))
                started = None
            if started is None:
                self.stop_event.wait(0.2)
            else:  # at most one page every PAGE_CYCLE seconds per window
                self.stop_event.wait(max(0.0, started + PAGE_CYCLE - time.monotonic()))

    def poll(self):
        """Read one page if the collector has one. Returns when that page was started, or None."""
        try:
            # The collector holds this request up to a second; allow far longer than that, since a
            # task the collector handed out but this window never received is lost until re-sent.
            task = self.client.request("scrape/browser-task?wait=1", timeout=10)
        except (OSError, ValueError) as ex:
            self.log("task-request " + type(ex).__name__)
            return None
        # The collector reports `active` until the run (including the CSV sync) ends.
        if task.get("active") is False and self.window is not None:
            self.close_window()
        if "id" not in task or task["id"] == self.last_task:
            return None
        task_id, error = task["id"], None
        self.last_task, self.last_timing = task_id, None
        started = time.monotonic()
        try:
            html = self.fetch(task)
        except Exception as ex:
            html, error = None, str(ex)
        if self.disposed:
            return None
        payload = {"id": task_id, "html": html} if error is None else {"id": task_id, "error": error}
        try:
            self.client.request("scrape/browser-result", payload, timeout=15)
        except (OSError, ValueError):
            pass
        elapsed = time.monotonic() - started
        if error is None and elapsed > PAGE_CYCLE:
            timing = self.last_timing or [0, 0, 0, 0]  # requestStart, responseStart, responseEnd, domInteractive (ms)
            self.log("slow-page %.1fs server-wait %dms html %dms parse %dms %s" % (
                elapsed, timing[1] - timing[0], timing[2] - timing[1], timing[3] - timing[2], task.get("url", "")))
        return started

    def fetch(self, task):
        url = task.get("url", "")
        if not source_url_allowed(url):
            raise RuntimeError("公開取得元のURLではありません。")
        self.ensure_window()
        self.loaded.clear()
        status, status_text = 0, "loading"
        # Follow a genuine link of the currently rendered page when available; this keeps
        # ordinary navigation/referrers. No headers, cookies or checks are manufactured.
        # Model/seat pages never link to each other, so they skip this check (one less UI-thread call).
        bonus_page = str(task.get("kind", "")).startswith("bonus-")
        followed = False if bonus_page else self.evaluate(FOLLOW_JS % json.dumps(url))
        if not truthy(followed):
            self.window.load_url(url)
        started = time.monotonic()
        give_up, reloaded = started + 55, False  # stop waiting after 55 seconds, as before
        while time.monotonic() < give_up:
            if self.disposed or self.stop_event.is_set():
                return None
            self.loaded.wait(LOADED_CHECK if self.loaded.is_set() else LOADING_CHECK)
            elapsed = time.monotonic() - started
            # Wait for the requested current-day table, not unrelated rankings (web/source-readiness.js).
            probe = self.probe(task)
            if probe is None or not same_page(probe.get("href") or "", url):
                continue
            if probe.get("status") and status == 0:
                status = int(probe["status"])
                status_text = "HTTP %s" % status
            if status in (401, 403, 429):
                raise RuntimeError("公開サイトが取得を制限しました（HTTP %s）。再試行せず停止します。" % status)
            if probe.get("restricted"):
                raise RuntimeError("公開サイトが取得を制限しました（認証・確認画面）。操作・再試行せず停止します。")
            if probe.get("ready") and isinstance(probe.get("html"), str):
                self.last_timing = probe.get("timing")
                return probe["html"]
            # A successful but genuinely empty document is transient: reload once, normally.
            # If it stays empty, give up on this page so the collector can revisit it later.
            if elapsed >= 6 and status in (0, 200) and probe.get("empty"):
                if reloaded and elapsed >= 12.5:
                    break
                if not reloaded:
                    self.log("empty-document ordinary-reload-once " + url)
                    time.sleep(3)
                    self.loaded.clear()
                    self.evaluate("location.reload()")
                    reloaded = True
        if self.disposed:
            return None
        shape = self.evaluate("JSON.stringify({title:document.title,length:document.documentElement.outerHTML.length,"
                              "tables:document.querySelectorAll('table').length})")
        self.log("unreadable-page %s %s %s" % (url, status_text, shape if isinstance(shape, str) else ""))
        raise RuntimeError("公開ページを表示できませんでした（%s）。未掲載・閲覧制限・ブラウザー確認を確認してください。制限の回避は行いません。" % status_text)

    def dispose(self):
        self.disposed = True
        self.stop_event.set()
        self.close_window()


class SourceBrowser:
    """SOURCE_WINDOWS source windows reading the collector's pages side by side."""

    def __init__(self, webview, client, folder, count=SOURCE_WINDOWS):
        self.stop_event = threading.Event()
        self.windows = [SourceWindow(webview, client, folder, index, self.stop_event) for index in range(count)]

    def run(self):
        threads = [threading.Thread(target=window.run, daemon=True) for window in self.windows]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def dispose(self):
        self.stop_event.set()
        for window in self.windows:
            window.dispose()


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
    # Wait for the first /api/state round trip instead of a fixed delay (slow CI machines).
    for _ in range(40):
        time.sleep(0.5)
        if run_script(window, "document.querySelector('#connection').textContent") not in (None, "接続中"):
            break
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
    limit_webview_hosts()
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
