import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import urllib.request
import zipfile

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
import versions  # noqa: E402

spec = importlib.util.spec_from_file_location('versions_server', APP / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def run(run_id, branch, sha, conclusion='success', status='completed', event='push'):
    return {'id': run_id, 'head_branch': branch, 'head_sha': sha, 'conclusion': conclusion, 'status': status, 'event': event,
            'created_at': '2026-10-06T00:00:%02dZ' % run_id, 'html_url': 'https://github.com/run/%d' % run_id}


def artifact(artifact_id, name, expired=False):
    return {'id': artifact_id, 'name': name, 'expired': expired, 'size_in_bytes': 1000}


class PureFunctionTests(unittest.TestCase):
    def test_version_id_is_folder_safe(self):
        self.assertEqual(versions.version_id('feature/bbrb-scraper', 'abcdef1234'), 'feature-bbrb-scraper-abcdef1')
        self.assertEqual(versions.version_id('../..', 'abcdef1234'), 'branch-abcdef1')

    def test_platform_label(self):
        self.assertEqual(versions.platform_label('win32'), 'windows')
        self.assertEqual(versions.platform_label('darwin'), 'macos')
        self.assertIsNone(versions.platform_label('linux'))

    def test_build_info_from_package_file_and_from_git(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.git' / 'refs' / 'heads' / 'feature').mkdir(parents=True)
            (root / '.git' / 'HEAD').write_text('ref: refs/heads/feature/x\n', encoding='utf-8')
            (root / '.git' / 'refs' / 'heads' / 'feature' / 'x').write_text('a' * 40 + '\n', encoding='utf-8')
            info = versions.load_build_info(root, '1.3.1')
            self.assertEqual((info['branch'], info['sha'], info['packaged'], info['version']), ('feature/x', 'a' * 40, False, '1.3.1'))
            (root / 'build-info.json').write_text(json.dumps({'branch': 'main', 'sha': 'b' * 40, 'builtAt': '2026-10-06T00:00:00+00:00'}), encoding='utf-8')
            info = versions.load_build_info(root, '1.3.1')
            self.assertEqual((info['branch'], info['packaged'], info['version']), ('main', True, '1.3.1'))

    def test_latest_builds_keeps_the_newest_successful_package_per_branch(self):
        runs = [run(5, 'main', 'e' * 40, conclusion=None, status='in_progress'), run(4, 'feature', 'd' * 40, conclusion='failure'),
                run(3, 'main', 'c' * 40), run(2, 'feature', 'b' * 40), run(1, 'main', 'a' * 40), run(6, 'other', 'f' * 40, conclusion='failure')]
        artifacts = {3: [artifact(30, 'JokersEye-1.3.1-windows'), artifact(31, 'JokersEye-1.3.1-macos')],
                     2: [artifact(20, 'JokersEye-1.3.0-windows', expired=True)], 1: [artifact(10, 'JokersEye-1.2.0-windows')]}
        rows = {row['branch']: row for row in versions.latest_builds(runs, artifacts, 'windows')}
        self.assertEqual((rows['main']['artifactId'], rows['main']['version'], rows['main']['sha']), (30, '1.3.1', 'c' * 40))
        self.assertEqual(rows['main']['newer']['status'], 'in_progress')
        self.assertIsNone(rows['feature']['artifactId'])  # only package expired
        self.assertEqual(rows['other']['newer']['conclusion'], 'failure')
        self.assertEqual(versions.latest_builds(runs, artifacts, 'macos')[0]['artifactId'], 31)

    def test_safe_extract_refuses_paths_outside_the_target(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / 'bad.zip'
            with zipfile.ZipFile(archive, 'w') as zf:
                zf.writestr('../escape.txt', 'x')
            with self.assertRaises(ValueError):
                versions.safe_extract(archive, Path(folder) / 'out')
            self.assertFalse((Path(folder) / 'escape.txt').exists())


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / 'data'
        self.store = server.Store(self.folder)
        os.environ.pop(versions.TRIAL_ENV, None)
        self.manager = versions.VersionManager(self.folder, server.ROOT, '1.3.1', self.store.copy_into)
        self.manager.platform = 'windows'

    def tearDown(self):
        for process in self.manager.processes.values():
            process.wait(timeout=10)
        os.environ.pop(versions.TRIAL_ENV, None)
        self.tmp.cleanup()

    def test_token_format_is_checked_before_contacting_github(self):
        for bad in ('', 'short', 'has space ' * 5, None):
            with self.assertRaises(ValueError):
                self.manager.set_token(bad)
        self.assertFalse(self.manager.overview()['tokenRegistered'])

    def test_token_file_is_private(self):
        self.manager.request = lambda path, token=None, opener=None: {'full_name': versions.REPO}
        self.manager.set_token('github_pat_' + 'x' * 30)
        self.assertTrue(self.manager.overview()['tokenRegistered'])
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.manager.token_file.stat().st_mode), 0o600)
        self.manager.delete_token()
        self.assertFalse(self.manager.overview()['tokenRegistered'])

    def test_trial_window_cannot_manage_versions(self):
        os.environ[versions.TRIAL_ENV] = json.dumps({'branch': 'feature', 'sha': 'a' * 40})
        trial = versions.VersionManager(self.folder, server.ROOT, '1.3.1', self.store.copy_into)
        self.assertEqual(trial.overview()['trial']['branch'], 'feature')
        for call in (lambda: trial.install(1), lambda: trial.launch('x-aaaaaaa'), lambda: trial.set_token('github_pat_' + 'x' * 30)):
            with self.assertRaises(ValueError):
                call()

    def test_launch_and_remove_reject_unknown_or_unsafe_ids(self):
        for bad in ('../data', 'missing-aaaaaaa', ''):
            with self.assertRaises(ValueError):
                self.manager.launch(bad)
            with self.assertRaises(ValueError):
                self.manager.remove(bad)

    def test_install_downloads_copies_data_and_launches_with_its_own_folder(self):
        self.store.import_csv('date,seat,model,games,bb,rb,net,rate\n2026-01-01,1,A,10,1,0,5,main\n', 'a.csv', 'test')
        report = Path(self.tmp.name) / 'launched.json'
        package = Path(self.tmp.name) / 'package.zip'
        with zipfile.ZipFile(package, 'w') as zf:
            zf.writestr("Joker's eye/Joker's eye.exe", 'import json, os, sys\nopen(%r, "w").write(json.dumps({"argv": sys.argv[1:], "trial": os.environ.get(%r)}))\n'
                        % (str(report), versions.TRIAL_ENV))
        meta = {'id': 77, 'name': 'JokersEye-1.4.0-windows', 'expired': False, 'size_in_bytes': package.stat().st_size,
                'created_at': '2026-10-06T00:00:00Z', 'workflow_run': {'id': 9, 'head_branch': 'feature/x', 'head_sha': 'c' * 40}}
        self.manager.request = lambda path, token=None, opener=None: meta
        self.manager.download_address = lambda artifact_id, opener=None: package.resolve().as_uri()
        # The fake app is a Python script: run it with this Python on every OS.
        real_popen = subprocess.Popen
        popen = mock.patch.object(versions.subprocess, 'Popen', side_effect=lambda args, **kw: real_popen([sys.executable] + list(args), **kw))
        popen.start()
        self.addCleanup(popen.stop)
        original_open = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout=None: original_open(req.full_url)
        try:
            self.manager.install(77)
            for _ in range(100):
                if self.manager.status()['state'] != 'running':
                    break
                time.sleep(0.05)
        finally:
            urllib.request.urlopen = original_open
        self.assertEqual(self.manager.status()['state'], 'done', self.manager.status())
        ident = 'feature-x-ccccccc'
        for _ in range(100):
            if report.exists():
                break
            time.sleep(0.05)
        launched = json.loads(report.read_text(encoding='utf-8'))
        data = self.folder / 'versions' / ident / 'data'
        self.assertEqual(launched['argv'], ['--data', str(data)])
        self.assertEqual(json.loads(launched['trial'])['branch'], 'feature/x')
        copied = server.Store(data)
        self.assertEqual(copied.state()['summary']['records'], 1)
        self.assertEqual(self.manager.installed()[0]['version'], '1.4.0')
        self.manager.processes[ident].wait(timeout=10)
        self.manager.remove(ident)
        self.assertFalse((self.folder / 'versions' / ident).exists())
        self.assertEqual(server.Store(self.folder).state()['summary']['records'], 1)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.host = server.Host(('127.0.0.1', 0), server.Store(self.tmp.name))
        import threading
        self.thread = threading.Thread(target=self.host.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:%s' % self.host.server_port

    def tearDown(self):
        self.host.shutdown()
        self.host.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def get(self, path):
        req = urllib.request.Request(self.base + path, headers={'X-Joker-Token': self.host.token})
        with urllib.request.urlopen(req) as response:
            return json.load(response)

    def test_state_and_versions_report_what_is_running(self):
        state = self.get('/api/state')
        self.assertEqual(state['build']['version'], server.VERSION)
        self.assertIsNone(state['trial'])
        overview = self.get('/api/versions')
        self.assertEqual((overview['repo'], overview['tokenRegistered'], overview['installed']), (versions.REPO, False, []))
        for path in ('/versions.js', '/versions.css'):
            with urllib.request.urlopen(self.base + path) as response:
                self.assertEqual(response.status, 200)


if __name__ == '__main__':
    unittest.main()
