import importlib.util
import io
from pathlib import Path
import sys
import tempfile
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


class SourceBrowserUrlTests(unittest.TestCase):
    def browser(self, task, window):
        source = desktop.SourceBrowser(None, FakeClient(task), tempfile.gettempdir())
        source.window = window
        return source

    def test_current_url_prefers_the_encoded_location(self):
        source = self.browser({}, FakeWindow('https://min-repo.com/1/?kishu=L%E6%9D%B1'))
        self.assertEqual(source.current_url(), 'https://min-repo.com/1/?kishu=L%E6%9D%B1')
        source.window.href = None  # navigating: fall back to pywebview's URL
        self.assertEqual(source.current_url(), 'https://min-repo.com/1/?kishu=L東京喰種')


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
