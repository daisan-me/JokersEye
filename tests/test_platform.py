import importlib.util
import io
import json
from pathlib import Path
import sys
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
        source.window = window
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
        class Screen:
            def __init__(self, x, y, width, height):
                self.x, self.y, self.width, self.height = x, y, width, height
        main, side = Screen(0, 0, 1920, 1080), Screen(1920, -200, 2560, 1440)
        self.assertEqual(desktop.screen_list(type('W', (), {'screens': [main, side]})),
                         [{'x': 0, 'y': 0, 'width': 1920, 'height': 1080}, {'x': 1920, 'y': -200, 'width': 2560, 'height': 1440}])
        self.assertEqual(desktop.screen_area([main, side], '1920,-200,2560,1440'), (side, (1930, -190, 2540, 1370)))
        self.assertEqual(desktop.screen_area([main], '1920,-200,2560,1440'), (None, desktop.SOURCE_AREA))  # unplugged: as before
        self.assertEqual(desktop.screen_area([main, side], ''), (None, desktop.SOURCE_AREA))
        created = []
        class FakeWebview:
            screens = [main, side]
            def create_window(self, title, **options):
                created.append(options)
                window = FakeWindow()
                window.events = mock.MagicMock()
                return window
        for platform in ('win32', 'darwin'):
            source = desktop.SourceWindow(FakeWebview(), FakeClient({}), tempfile.gettempdir(), index=5)
            source.count, source.screen = 6, '1920,-200,2560,1440'
            with mock.patch.object(desktop.sys, 'platform', platform):
                source.ensure_window()
        windows, mac = created
        self.assertFalse(windows['focus'])  # never takes the keyboard from what the user is doing
        self.assertNotIn('screen', windows)  # Windows: positions on the whole desktop
        x, y, width, height = desktop.tile(5, 6, (1930, -190, 2540, 1370))
        self.assertEqual((windows['x'], windows['y'], windows['width'], windows['height']), (x, y, width, height))
        self.assertTrue(1920 <= x and x + width <= 1920 + 2560 and -200 <= y and y + height <= -200 + 1440)
        self.assertIs(mac['screen'], side)  # macOS: positions relative to the chosen monitor
        self.assertEqual((mac['x'], mac['y'], mac['focus']), (x - 1920, y + 200, False))

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
