import importlib.util
import io
import json
from types import SimpleNamespace
from pathlib import Path
import sys
import threading
import tempfile
import time
import unittest
from unittest import mock

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
import platform_support  # noqa: E402

spec = importlib.util.spec_from_file_location('desktop', APP / 'desktop.py')
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


class DataDirTests(unittest.TestCase):
    def test_windows_keeps_the_1_0_location(self):
        path = platform_support.default_data_dir('/app', {'LOCALAPPDATA': 'C:/Users/x/AppData/Local'}, 'win32', '/home/x')
        self.assertEqual(path, Path('C:/Users/x/AppData/Local') / 'JokersEye')

    def test_windows_without_localappdata_uses_the_project_folder(self):
        self.assertEqual(platform_support.default_data_dir('/app', {}, 'win32', '/home/x'), Path('/app') / 'data')

    def test_macos(self):
        path = platform_support.default_data_dir('/app', {}, 'darwin', '/Users/x')
        self.assertEqual(path, Path('/Users/x/Library/Application Support/JokersEye'))

    def test_linux_respects_xdg(self):
        self.assertEqual(platform_support.default_data_dir('/app', {'XDG_DATA_HOME': '/xdg'}, 'linux', '/home/x'), Path('/xdg/JokersEye'))
        self.assertEqual(platform_support.default_data_dir('/app', {}, 'linux', '/home/x'), Path('/home/x/.local/share/JokersEye'))

    def test_external_links_only_http_and_https(self):
        for bad in ('file:///etc/passwd', 'javascript:alert(1)', '', None, 'ftp://example.com'):
            self.assertFalse(platform_support.open_external(bad))


    def test_error_dialog_survives_a_console_that_cannot_print_the_title(self):
        # Japanese Windows consoles use cp932, which has no em dash; a windowed app may have no console.
        with mock.patch.object(platform_support.sys, 'platform', 'linux'):
            for stream in (io.TextIOWrapper(io.BytesIO(), encoding='cp932'), None):
                with mock.patch.object(platform_support.sys, 'stderr', stream), mock.patch('builtins.print', side_effect=UnicodeEncodeError('cp932', '—', 0, 1, 'x')):
                    platform_support.show_error("Joker's eye — 起動エラー", 'message')


class SourceBrowserRuleTests(unittest.TestCase):
    def test_only_https_on_the_public_data_site(self):
        self.assertTrue(desktop.source_url_allowed('https://min-repo.com/3382117/?kishu=all'))
        for bad in ('http://min-repo.com/1/', 'https://min-repo.com.evil.example/1/', 'https://evil.example/min-repo.com/',
                    'https://user@evil.example/', 'file:///tmp/x', ''):
            self.assertFalse(desktop.source_url_allowed(bad), bad)

    def test_page_comparison_ignores_case_and_escaping_but_not_query(self):
        self.assertTrue(desktop.same_page('HTTPS://Min-Repo.com/tag/%E3%82%B4/', 'https://min-repo.com/tag/ゴ/'))
        self.assertFalse(desktop.same_page('https://min-repo.com/1/?kishu=all', 'https://min-repo.com/1/'))

    def test_model_page_matches_the_unescaped_url_webview2_reports(self):
        # pywebview on WebView2 returns System.Uri.ToString(): non-ASCII query text unescaped.
        requested = 'https://min-repo.com/3389999/?kishu=L%E6%9D%B1%E4%BA%AC%E5%96%B0%E7%A8%AE'
        self.assertTrue(desktop.same_page('https://min-repo.com/3389999/?kishu=L東京喰種', requested))
        self.assertTrue(desktop.same_page('https://min-repo.com/1/?kishu=%E3%82%B9%E3%83%9E%E3%82%B9%E3%83%AD+%E3%83%8F%E3%83%8A%E3%83%93',
                                          'https://min-repo.com/1/?kishu=スマスロ%20ハナビ'))
        self.assertFalse(desktop.same_page('https://min-repo.com/3389999/?kishu=L東京喰種', 'https://min-repo.com/3389999/?kishu=L%E6%9D%B1'))
        self.assertFalse(desktop.same_page('https://min-repo.com/1/?kishu=A-SLOT%2B', 'https://min-repo.com/1/?kishu=A-SLOT+'))

    def test_javascript_results_from_both_engines(self):
        for value in (True, 1, 'true'):
            self.assertTrue(desktop.truthy(value))
        for value in (False, 0, None, 'false', ''):
            self.assertFalse(desktop.truthy(value))


class Screen:
    """pywebview's Screen: x, y, width, height and the platform's frame."""
    def __init__(self, x, y, width, height, frame=None):
        self.x, self.y, self.width, self.height, self.frame = x, y, width, height, frame


def monitors_of(*screens):
    monitors = desktop.Monitors()
    monitors.items = desktop.read_monitors(SimpleNamespace(screens=list(screens)), 'linux')  # default room below
    return monitors


class FakeWindow:
    def __init__(self, href=None):
        self.href, self.destroyed, self.hidden = href, 0, 0

    def run_js(self, script):
        return self.href if script == 'location.href' else None

    def get_current_url(self):
        return 'https://min-repo.com/1/?kishu=L東京喰種'

    def destroy(self):
        self.destroyed += 1

    def hide(self):
        self.hidden += 1


class FakeClient:
    def __init__(self, task):
        self.task, self.requests = task, []

    def request(self, path, payload=None, timeout=3):
        self.requests.append(path)
        return self.task


class SourceBrowserWindowTests(unittest.TestCase):
    def browser(self, task, window):
        source = desktop.SourceWindow(None, FakeClient(task), tempfile.gettempdir())
        source.window, source.layout = window, (source.count, source.screen)  # as ensure_window() placed it
        return source

    def test_twelve_windows_share_one_stop_switch_and_tile_the_screen(self):
        source = desktop.SourceBrowser(None, FakeClient({}), tempfile.gettempdir())
        self.assertEqual([window.index for window in source.windows], list(range(12)))
        self.assertTrue(all(window.stop_event is source.stop_event for window in source.windows))
        for count in (3, 4, 6, 12):  # the choices of the Scraper tab
            created = []
            class FakeWebview:
                def create_window(self, title, **options):
                    created.append((title, options['x'], options['y'], options['width'], options['height']))
                    window = FakeWindow()
                    window.events = mock.MagicMock()
                    return window
            for window in source.windows[:count]:
                window.window, window.count, window.webview = None, count, FakeWebview()
                window.ensure_window()
            self.assertEqual(created[-1][0], desktop.SOURCE_TITLE + ' %d/%d' % (count, count))
            for i, (_, x, y, w, h) in enumerate(created):  # none hides another, all on a full-HD screen
                self.assertTrue(x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080, (count, created[i]))
                self.assertTrue(w >= 300 and h >= 300, (count, w, h))
                for (_, x2, y2, w2, h2) in created[i + 1:]:
                    self.assertTrue(x + w <= x2 or x2 + w2 <= x or y + h <= y2 or y2 + h2 <= y, (count, x, y, x2, y2))
        self.assertEqual(desktop.tile(11, 12)[2:], (470, 343))  # 12 windows: the earlier 4 x 3 tiles

    def test_windows_open_without_focus_on_the_chosen_monitor(self):
        main, side = Screen(0, 0, 1920, 1080), Screen(1920, -200, 2560, 1440)
        monitors = monitors_of(main, side)
        self.assertEqual([m['id'] for m in monitors.items], ['0,0,1920,1080', '1920,-200,2560,1440'])
        self.assertEqual(desktop.monitor_area(monitors.find('1920,-200,2560,1440')), (1930, -190, 2540, 1360))
        self.assertIsNone(monitors.find('9,9,9,9'))
        created = []
        class FakeWebview:
            @property
            def screens(self):  # windows must not read the monitors themselves (AppKit is main-thread only)
                raise AssertionError('read the monitors on a worker thread')
            def create_window(self, title, **options):
                created.append(options)
                window = FakeWindow()
                window.events = mock.MagicMock()
                return window
        for platform in ('win32', 'darwin'):
            source = desktop.SourceWindow(FakeWebview(), FakeClient({}), tempfile.gettempdir(), index=5, monitors=monitors)
            source.count, source.screen = 6, '1920,-200,2560,1440'
            with mock.patch.object(desktop.sys, 'platform', platform):
                source.ensure_window()
        windows, mac = created
        self.assertFalse(windows['focus'])  # never takes the keyboard from what the user is doing
        self.assertNotIn('screen', windows)  # Windows: positions on the whole desktop
        x, y, width, height = desktop.tile(5, 6, (1930, -190, 2540, 1360))
        self.assertEqual((windows['x'], windows['y'], windows['width'], windows['height']), (x, y, width, height))
        self.assertTrue(1920 <= x and x + width <= 1920 + 2560 and -200 <= y and y + height <= -200 + 1440)
        self.assertIs(mac['screen'], side)  # macOS: positions relative to the chosen monitor
        self.assertEqual((mac['x'], mac['y'], mac['focus']), (x - 1920, y + 200, False))

    def test_monitors_use_their_work_area_and_windows_pixels(self):
        # Windows: WinForms reports the system-DPI-aware pixels and the WorkingArea without the taskbar;
        # pywebview multiplies window positions by the system scale, so the area is divided by it.
        side = Screen(2880, 0, 2560, 1440, frame=SimpleNamespace(X=2880, Y=0, Width=2560, Height=1392))
        with mock.patch.object(desktop, 'system_scale', return_value=1.5):
            monitor = desktop.read_monitors(SimpleNamespace(screens=[side]), 'win32')[0]
        self.assertEqual((monitor['insets'], monitor['unit']), ((0, 0, 0, 48), 1.5))
        self.assertEqual(desktop.monitor_area(monitor), (1930, 10, 1686, 908))
        # macOS: the menu bar (37) and the Dock (70) are outside visibleFrame (Cocoa's y grows upwards).
        rect = lambda x, y, w, h: SimpleNamespace(origin=SimpleNamespace(x=x, y=y), size=SimpleNamespace(width=w, height=h))
        mac = Screen(0, 0, 1512, 982, frame=rect(0, 0, 1512, 982))
        self.assertEqual(desktop.work_insets(mac, rect(0, 70, 1512, 875), 'darwin'), (0, 37, 0, 70))
        self.assertIsNone(desktop.work_insets(Screen(0, 0, 10, 10), None, 'win32'))  # unreadable: the default room below

    def test_tiles_are_never_smaller_than_a_window(self):
        small = Screen(-800, 0, 800, 600)
        monitors = monitors_of(Screen(0, 0, 1920, 1080), small)
        self.assertEqual(monitors.find('-800,0,800,600')['fits'], [3, 4, 6])  # 12 tiles would be 191 px wide
        self.assertEqual(monitors.find('0,0,1920,1080')['fits'], [3, 4, 6, 12])
        created = []
        class FakeWebview:
            def create_window(self, title, **options):
                created.append(options)
                window = FakeWindow()
                window.events = mock.MagicMock()
                return window
        source = desktop.SourceWindow(FakeWebview(), FakeClient({}), tempfile.gettempdir(), index=11, monitors=monitors)
        source.count, source.screen = 12, '-800,0,800,600'
        with mock.patch.object(desktop.sys, 'platform', 'linux'):
            source.ensure_window()  # does not fit (the monitor changed since it was read): the main screen as before
        self.assertEqual((created[0]['x'], created[0]['y'], created[0]['width']), desktop.tile(11, 12)[:3])
        for count in (3, 4, 6, 12):
            for index in range(count):
                self.assertGreaterEqual(desktop.tile(index, count)[2:], desktop.MIN_TILE)

    def test_windows_gives_the_keyboard_back_when_a_source_window_takes_it(self):
        class Event(list):  # pywebview's events take handlers with +=
            def __iadd__(self, handler):
                self.append(handler)
                return self
        class Events:
            def __init__(self):
                self.shown, self.loaded, self.closing = Event(), Event(), Event()
        created = []
        class FakeWebview:
            def create_window(self, title, **options):
                window = FakeWindow()
                window.events = Events()
                window.native = SimpleNamespace(Handle=SimpleNamespace(ToInt32=lambda: 99))
                created.append(window)
                return window
        source = desktop.SourceWindow(FakeWebview(), FakeClient({}), tempfile.gettempdir())
        with mock.patch.object(desktop.sys, 'platform', 'win32'), mock.patch.object(desktop, 'foreground_window', return_value=7):
            source.ensure_window()
        self.assertEqual(len(created[0].events.shown), 1)  # runs when pywebview has shown (and activated) it
        calls = []
        user32 = SimpleNamespace(IsWindow=lambda hwnd: True, GetForegroundWindow=lambda: 99,
                                 SetForegroundWindow=lambda hwnd: calls.append(hwnd))
        self.assertTrue(desktop.give_back_focus(7, created[0], user32))
        self.assertEqual(calls, [7])  # the video (or the app) the user had gets the keyboard back
        user32.GetForegroundWindow = lambda: 5  # the user already moved on: left alone
        self.assertFalse(desktop.give_back_focus(7, created[0], user32))
        with mock.patch.object(desktop.sys, 'platform', 'darwin'):
            mac = desktop.SourceWindow(FakeWebview(), FakeClient({}), tempfile.gettempdir())
            mac.ensure_window()
        self.assertEqual(created[1].events.shown, [])  # macOS does not activate it

    def test_mac_reads_the_monitors_on_the_gui_thread(self):
        ran_on = []
        helper = SimpleNamespace(callAfter=lambda function: threading.Thread(target=function, name='gui').start())
        with mock.patch.dict(sys.modules, {'PyObjCTools': SimpleNamespace(AppHelper=helper), 'PyObjCTools.AppHelper': helper}):
            worker = threading.Thread(target=lambda: ran_on.append(desktop.call_on_gui_thread(
                lambda: threading.current_thread().name, platform='darwin')))
            worker.start(); worker.join(5)
        self.assertEqual(ran_on, ['gui'])  # handed to the GUI thread, not run on the worker
        self.assertEqual(desktop.call_on_gui_thread(lambda: threading.current_thread().name, platform='win32'),
                         threading.current_thread().name)  # Windows: read where it is asked

    def test_an_open_window_is_never_shown_again_so_it_never_takes_focus(self):
        created = []
        class FakeWebview:
            def create_window(self, title, **options):
                window = FakeWindow()
                window.shown = 0
                window.show = lambda: setattr(window, 'shown', window.shown + 1)
                window.events = mock.MagicMock()
                created.append((window, options))
                return window
        source = desktop.SourceWindow(FakeWebview(), FakeClient({}), tempfile.gettempdir())
        for _ in range(3):  # one call per page read
            source.ensure_window()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0][0].shown, 0)  # pywebview's show() activates the window
        self.assertFalse(source.on_closing())  # the user closes it during a run: hidden, the run stops
        source.ensure_window()  # made again without focus, not shown
        self.assertEqual((len(created), created[0][0].destroyed, created[0][0].shown), (2, 1, 0))
        self.assertFalse(created[1][1]['focus'])

    def test_a_window_placed_for_other_settings_is_opened_again(self):
        created = []
        class FakeWebview:
            def create_window(self, title, **options):
                window = FakeWindow()
                window.events = mock.MagicMock()
                created.append((title, options))
                return window
        client = FakeClient({'active': True, 'windows': 4, 'rate': 1, 'screen': ''})
        source = desktop.SourceWindow(FakeWebview(), client, tempfile.gettempdir(), index=1,
                                      monitors=monitors_of(Screen(0, 0, 1920, 1080), Screen(1920, 0, 2560, 1440)))
        platform = mock.patch.object(desktop.sys, 'platform', 'linux')  # whole-desktop positions, as on Windows
        platform.start()
        self.addCleanup(platform.stop)
        source.poll()
        source.ensure_window()  # 4 windows on the main screen
        first = source.window
        client.task = {'active': True, 'windows': 4, 'rate': 2, 'screen': ''}  # only the speed changed: kept
        source.poll()
        self.assertIs(source.window, first)
        # Stopped, changed to 6 windows on another monitor and started again before this window closed.
        client.task = {'active': True, 'windows': 6, 'rate': 2, 'screen': '1920,0,2560,1440'}
        source.poll()
        self.assertIsNone(source.window)
        self.assertEqual(first.destroyed, 1)
        source.ensure_window()
        self.assertEqual(created[-1][0], desktop.SOURCE_TITLE + ' 2/6')
        self.assertEqual((created[-1][1]['x'], created[-1][1]['y']), desktop.tile(1, 6, (1930, 10, 2540, 1360))[:2])
        client.task = {'active': True, 'unused': True, 'windows': 3, 'rate': 2, 'screen': ''}
        source.index = 4  # a window the next run does not use is closed
        source.poll()
        self.assertIsNone(source.window)

    def test_monitors_are_read_only_when_asked(self):
        class Collector:
            def __init__(self):
                self.reported = []
            def report_screens(self, screens):
                self.reported.append(screens)
        webview = SimpleNamespace(screens=[Screen(0, 0, 1920, 1080)])
        collector, monitors = Collector(), desktop.Monitors()
        platform = mock.patch.object(desktop.sys, 'platform', 'linux')  # no AppKit: the room-below default
        platform.start()
        self.addCleanup(platform.stop)
        desktop.refresh_screens(monitors, webview, collector)
        self.assertEqual(collector.reported, [[{'x': 0, 'y': 0, 'width': 1920, 'height': 1080, 'fits': [3, 4, 6, 12]}]])
        host = SimpleNamespace(screens_requested=threading.Event())
        desktop.serve_monitor_requests(host, monitors, webview, collector)  # nobody asked: not read
        self.assertEqual(len(collector.reported), 1)
        host.screens_requested.set()  # 「モニターを読み直す」
        desktop.serve_monitor_requests(host, monitors, webview, collector)
        self.assertEqual((len(collector.reported), host.screens_requested.is_set()), (2, False))
        desktop.refresh_screens(monitors, SimpleNamespace(), collector)  # unreadable: nothing reported (the tab says so)
        self.assertEqual(len(collector.reported), 2)
        source = Path(desktop.__file__).read_text(encoding='utf-8')
        self.assertNotIn('screens_seen', source)  # no periodic check of the monitors
        self.assertIn('serve_monitor_requests(host, source.monitors, webview, store.collector)', source)  # in the desktop loop
        self.assertIn('refresh_screens(source.monitors, webview, store.collector)  # once', source)  # and at startup

    def test_webviews_load_pages_as_an_ordinary_visitor(self):
        # Blocking third-party hosts made the site blank negative 差枚/出率 (2026-10-07): not done any more.
        self.assertFalse(hasattr(desktop, 'limit_webview_hosts'))

    def test_a_database_error_page_stops_at_once(self):
        url = 'https://min-repo.com/1/?kishu=X'
        window = FakeWindow()
        window.show = window.load_url = lambda *args: None
        probe = json.dumps({'href': url, 'status': 200, 'restricted': False, 'ready': False, 'empty': False,
                            'html': None, 'trouble': True, 'timing': [0, 9000, 9001, 9002]})
        window.run_js = lambda script: probe if 'trouble' in script else None
        source = self.browser({}, window)
        source.loaded.set()
        started = time.monotonic()
        with self.assertRaises(RuntimeError) as ex:
            source.fetch({'url': url, 'kind': 'bonus-model', 'seats': ['1']})
        self.assertIn('混雑または障害中', str(ex.exception))
        self.assertLess(time.monotonic() - started, 3)  # not the 55-second wait

    def test_a_window_starts_at_most_one_page_every_cycle(self):
        source = self.browser({}, FakeWindow())
        source.cycle = 0.5  # 2 pages a second
        starts = []
        def poll():
            starts.append(time.monotonic())
            if len(starts) == 3:
                source.stop_event.set()
            return starts[-1]
        source.poll = poll
        source.run()
        self.assertEqual(len(starts), 3)
        self.assertGreaterEqual(starts[2] - starts[0], 2 * source.cycle - 0.05)

    def test_a_page_sets_the_windows_pace_and_place_and_asks_with_its_number(self):
        window = FakeWindow()
        source = desktop.SourceWindow(None, FakeClient({'id': 'x', 'url': 'https://min-repo.com/1/', 'active': True, 'windows': 6, 'rate': 4}),
                                      tempfile.gettempdir(), index=2)
        source.window = window
        source.fetch = lambda task: '<html></html>'
        source.poll()
        self.assertEqual((source.count, source.cycle), (6, 0.25))
        self.assertEqual(source.client.requests[0], 'scrape/browser-task?wait=1&window=2')

    def test_current_url_prefers_the_encoded_location(self):
        source = self.browser({}, FakeWindow('https://min-repo.com/1/?kishu=L%E6%9D%B1'))
        self.assertEqual(source.current_url(), 'https://min-repo.com/1/?kishu=L%E6%9D%B1')
        source.window.href = None  # navigating: fall back to pywebview's URL
        self.assertEqual(source.current_url(), 'https://min-repo.com/1/?kishu=L東京喰種')

    def test_window_closes_when_the_run_has_ended(self):
        window = FakeWindow()
        source = self.browser({'active': False}, window)
        source.poll()
        self.assertEqual(window.destroyed, 1)
        self.assertIsNone(source.window)
        self.assertTrue(source.on_closing())  # the close is not turned back into hide + stop
        self.assertEqual(window.hidden, 0)
        source.poll()  # nothing left to close
        self.assertEqual(window.destroyed, 1)

    def test_window_stays_open_while_the_run_is_active(self):
        window = FakeWindow()
        source = self.browser({'active': True}, window)
        source.poll()
        self.assertEqual(window.destroyed, 0)
        self.assertIs(source.window, window)

    def test_user_close_during_a_run_hides_and_stops(self):
        window = FakeWindow()
        source = self.browser({'active': True}, window)
        self.assertFalse(source.on_closing())
        self.assertEqual(window.hidden, 1)
        self.assertIn('scrape/stop', source.client.requests)


class SingleInstanceTests(unittest.TestCase):
    def test_second_launch_is_refused_until_the_first_exits(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = desktop.SingleInstance(folder), desktop.SingleInstance(folder)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()


if __name__ == '__main__':
    unittest.main()
