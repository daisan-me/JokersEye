import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

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


class SourceBrowserRuleTests(unittest.TestCase):
    def test_only_https_on_the_public_data_site(self):
        self.assertTrue(desktop.source_url_allowed('https://min-repo.com/3382117/?kishu=all'))
        for bad in ('http://min-repo.com/1/', 'https://min-repo.com.evil.example/1/', 'https://evil.example/min-repo.com/',
                    'https://user@evil.example/', 'file:///tmp/x', ''):
            self.assertFalse(desktop.source_url_allowed(bad), bad)

    def test_page_comparison_ignores_case_and_escaping_but_not_query(self):
        self.assertTrue(desktop.same_page('HTTPS://Min-Repo.com/tag/%E3%82%B4/', 'https://min-repo.com/tag/ゴ/'))
        self.assertFalse(desktop.same_page('https://min-repo.com/1/?kishu=all', 'https://min-repo.com/1/'))

    def test_javascript_results_from_both_engines(self):
        for value in (True, 1, 'true'):
            self.assertTrue(desktop.truthy(value))
        for value in (False, 0, None, 'false', ''):
            self.assertFalse(desktop.truthy(value))


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
