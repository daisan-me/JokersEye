"""In-app update (app/updater.py): choosing main's build, preparing it, and the helper script that swaps
the app after it exits (run for real: sh on macOS/Linux, Windows PowerShell on Windows)."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
import updater  # noqa: E402
import versions  # noqa: E402

NAME = versions.APP_NAME
# The fake app started by the helper: records its arguments.
LAUNCHER = 'import json, sys\nopen(sys.argv[1], "w", encoding="utf-8").write(json.dumps(sys.argv[2:], ensure_ascii=False))\n'


def run(run_id, branch='main', sha=None, conclusion='success', event='push'):
    return {'id': run_id, 'head_branch': branch, 'head_sha': sha or ('%d' % run_id) * 40, 'conclusion': conclusion, 'event': event,
            'created_at': '2026-11-0%dT00:00:00Z' % run_id, 'updated_at': '2026-11-0%dT00:10:00Z' % run_id, 'html_url': 'https://github.com/run/%d' % run_id}


def artifact(artifact_id, name, expired=False):
    return {'id': artifact_id, 'name': name, 'expired': expired, 'size_in_bytes': 1000}


def wait_for(path, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            return json.loads(Path(path).read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            time.sleep(0.1)
    raise AssertionError('%s was not written' % path)


class FakeCollector:
    def __init__(self):
        self.active = False
        self.maintenance = False
        self.idle_entered = False

    @contextmanager
    def idle(self):
        self.idle_entered = True
        yield


class FakeStore:
    def __init__(self):
        self.collector = FakeCollector()
        self.backups = 0

    def backup(self):
        self.backups += 1


class FakeVersions:
    def __init__(self, platform='windows', package=None, sha='a' * 40):
        self.platform = platform
        self.trial = None
        self.build = {'version': '1.4.0', 'branch': 'main', 'sha': sha, 'builtAt': '2026-11-01T00:00:00Z', 'packaged': True}
        self.package = package
        self.requests = []

    def token(self):
        return 'token'

    def request(self, path):
        self.requests.append(path)
        if '/actions/workflows/' in path:
            return {'workflow_runs': [run(3, sha='c' * 40), run(2, sha='b' * 40)]}
        if path.endswith('/runs/3/artifacts'):
            return {'artifacts': [artifact(30, 'JokersEye-1.4.1-macos')]}  # no Windows package in the newest run
        return {'artifacts': [artifact(20, 'JokersEye-1.4.1-windows'), artifact(21, 'JokersEye-1.4.1-macos')]}

    def download(self, artifact_id, archive, progress):
        shutil.copy(self.package, archive)
        progress(Path(archive).stat().st_size, Path(archive).stat().st_size)


class PureFunctionTests(unittest.TestCase):
    def test_install_layout(self):
        folder, exe = updater.install_layout(Path('C:/Apps/JokersEye/%s/%s.exe' % (NAME, NAME)), 'windows')
        self.assertEqual((folder.name, exe.name), (NAME, NAME + '.exe'))
        bundle, _ = updater.install_layout(Path('/Applications/%s.app/Contents/MacOS/%s' % (NAME, NAME)), 'macos')
        self.assertEqual(bundle.as_posix(), '/Applications/%s.app' % NAME)
        with self.assertRaises(ValueError):
            updater.install_layout(Path('/private/var/folders/x/AppTranslocation/y/d/%s.app/Contents/MacOS/%s' % (NAME, NAME)), 'macos')
        with self.assertRaises(ValueError):
            updater.install_layout(Path('/usr/local/bin/python3'), 'macos')
        with self.assertRaises(ValueError):
            updater.install_layout(Path('/usr/bin/python3'), None)

    def test_newest_main_build_skips_other_branches_failures_and_other_systems(self):
        runs = [run(5, branch='feature/x'), run(4, conclusion='failure'), run(3, event='pull_request'), run(2), run(1)]
        artifacts = {5: [artifact(50, 'JokersEye-1.5.0-windows')], 4: [artifact(40, 'JokersEye-1.4.9-windows')],
                     3: [artifact(30, 'JokersEye-1.4.8-windows')], 2: [artifact(20, 'JokersEye-1.4.1-windows', expired=True)],
                     1: [artifact(10, 'JokersEye-1.4.0-macos'), artifact(11, 'JokersEye-1.4.0-windows')]}
        found = updater.newest_main_build(runs, artifacts, 'windows')
        self.assertEqual((found['artifactId'], found['version'], found['sha']), (11, '1.4.0', '1' * 40))
        self.assertIsNone(updater.newest_main_build(runs, artifacts, 'linux'))

    def test_parse_time_compares_github_and_build_info_stamps(self):
        self.assertEqual(updater.parse_time('2026-11-02T08:00:00Z'), updater.parse_time('2026-11-02T08:00:00+00:00'))
        self.assertIsNone(updater.parse_time(None))

    def test_relaunch_arguments_drop_diagnostics(self):
        self.assertEqual(updater.relaunch_arguments(['--data', 'X', '--diagnostics', 'r.json', '--diagnostics=y', '--debug']),
                         ['--data', 'X', '--debug'])


class HelperTests(unittest.TestCase):
    """The helper script itself, on this OS."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows: the started app may still be closing
        base = Path(self.tmp.name)
        self.data = base / 'data フォルダー'
        self.install = base / 'apps' / NAME
        self.work = base / 'apps' / (NAME + '.update')
        self.staged = self.work / 'package' / NAME
        self.backup = base / 'apps' / (NAME + '.previous')
        for folder, marker in ((self.install, 'old.txt'), (self.staged, 'new.txt')):
            folder.mkdir(parents=True)
            (folder / marker).write_text(marker, encoding='utf-8')
        self.launcher = base / 'launcher.py'
        self.launcher.write_text(LAUNCHER, encoding='utf-8')
        self.marker = base / 'launched.json'
        self.updater = updater.Updater(self.data, base, FakeVersions(platform=versions.platform_label() or 'macos'), FakeStore(),
                                       shutdown=lambda: None, executable=sys.executable, argv=['app'], frozen=True)
        finished = subprocess.Popen([sys.executable, '-c', 'pass'])
        finished.wait()  # reaped: the helper sees no such process
        self.updater.pid = finished.pid

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, **swap):
        arguments = ['--data', str(self.data), "Joker's eye"]
        latest = {'version': '1.4.1', 'sha': 'c' * 40}
        return self.updater._plan(latest, [sys.executable, str(self.launcher), str(self.marker)] + arguments, self.data, **swap)

    def test_swaps_the_folders_and_starts_the_app_again(self):
        self.updater._launch_helper(self.plan(install=self.install, staged=self.staged, backup=self.backup, work=self.work))
        result = wait_for(self.data / 'updates' / 'result.json')
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(wait_for(self.marker), ['--data', str(self.data), "Joker's eye"])
        self.assertTrue((self.install / 'new.txt').is_file())
        self.assertTrue((self.backup / 'old.txt').is_file())
        self.assertFalse(self.work.exists())
        report = self.updater.last_result()
        self.assertEqual((report['status'], report['to']['sha'], report['from']['version']), ('ok', 'c' * 40, '1.4.0'))
        self.updater.acknowledge()
        self.assertIsNone(self.updater.last_result())

    def test_a_failed_swap_keeps_the_old_app_and_starts_it(self):
        shutil.rmtree(self.staged)
        self.updater._launch_helper(self.plan(install=self.install, staged=self.staged, backup=self.backup, work=self.work))
        self.assertEqual(wait_for(self.data / 'updates' / 'result.json')['status'], 'failed')
        wait_for(self.marker)
        self.assertTrue((self.install / 'old.txt').is_file())

    def test_source_restart_only_waits_and_starts(self):
        self.updater._launch_helper(self.plan())
        self.assertEqual(wait_for(self.data / 'updates' / 'result.json')['status'], 'restarted')
        wait_for(self.marker)
        self.assertTrue((self.install / 'old.txt').is_file())

    def test_does_nothing_while_the_app_is_still_running(self):
        running = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        self.addCleanup(running.wait)
        self.addCleanup(running.kill)
        self.updater.pid, self.updater.timeout = running.pid, 1
        self.updater._launch_helper(self.plan(install=self.install, staged=self.staged, backup=self.backup, work=self.work))
        self.assertEqual(wait_for(self.data / 'updates' / 'result.json')['status'], 'busy')
        self.assertTrue((self.install / 'old.txt').is_file())
        self.assertFalse(self.marker.exists())


class PackageUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows: the started app may still be closing
        base = Path(self.tmp.name)
        self.data = base / 'data'
        self.parent = base / 'Downloads' / 'JokersEye-main-windows'
        self.install = self.parent / NAME
        (self.install / '_internal').mkdir(parents=True)
        self.exe = self.install / (NAME + '.exe')
        self.exe.write_text('old', encoding='utf-8')
        (self.parent / 'README.md').write_text('old readme', encoding='utf-8')
        self.package = base / 'package.zip'
        with zipfile.ZipFile(self.package, 'w') as zf:
            zf.writestr('%s/%s.exe' % (NAME, NAME), 'new')
            zf.writestr('%s/%s.exe.config' % (NAME, NAME), '<configuration/>')
            zf.writestr('%s/_internal/base_library.zip' % NAME, 'lib')
            zf.writestr('README.md', 'new readme')
        self.versions = FakeVersions(package=self.package)
        self.store = FakeStore()
        self.stopped = threading.Event()
        self.updater = updater.Updater(self.data, base, self.versions, self.store, shutdown=self.stopped.set,
                                       executable=self.exe, argv=[str(self.exe), '--data', str(self.data)], frozen=True)
        self.plans = []
        self.updater._launch_helper = self.plans.append

    def tearDown(self):
        self.tmp.cleanup()

    def finish(self):
        for _ in range(200):
            if self.updater.status()['task']['state'] != 'running':
                break
            time.sleep(0.05)
        return self.updater.status()

    def test_check_finds_mains_newest_package_for_this_system(self):
        status = self.updater.check()
        latest = status['latest']
        self.assertEqual((latest['artifactId'], latest['version'], latest['sha'], latest['available'], latest['newer']),
                         (20, '1.4.1', 'b' * 40, True, True))
        self.assertIn('branch=main', self.versions.requests[0])
        self.versions.build['sha'] = 'b' * 40
        self.assertFalse(self.updater.check()['latest']['available'])

    def test_start_downloads_unpacks_next_to_the_app_and_hands_over(self):
        self.updater.check()
        self.updater.start(backup=True)
        self.assertTrue(self.stopped.wait(10), self.updater.status())
        self.assertEqual(self.store.backups, 1)
        self.assertTrue(self.store.collector.idle_entered)
        plan = self.plans[0]
        self.assertEqual(Path(plan['install']), self.install)
        self.assertEqual(Path(plan['backup']), self.parent / (NAME + '.previous'))
        self.assertEqual((Path(plan['staged']) / (NAME + '.exe')).read_text(encoding='utf-8'), 'new')
        self.assertEqual([plan['exe']] + plan['args'], [str(self.install / (NAME + '.exe')), '--data', str(self.data)])
        self.assertEqual((plan['to']['sha'], plan['from']['sha']), ('b' * 40, 'a' * 40))
        self.assertEqual(json.loads((self.data / 'updates' / 'plan.json').read_text(encoding='ascii'))['install'], str(self.install))
        self.assertEqual((self.parent / 'README.md').read_text(encoding='utf-8'), 'new readme')
        self.assertIsNone(self.updater.last_result())  # this process is the one being replaced

    def test_refuses_a_folder_with_other_files_before_backing_up_or_downloading(self):
        (self.install / 'my notes.txt').write_text('x', encoding='utf-8')
        self.updater.check()
        self.updater.start(backup=True)
        status = self.finish()
        self.assertEqual(status['task']['state'], 'failed')
        self.assertIn('アプリ以外のファイル', status['task']['message'])
        self.assertFalse(self.stopped.is_set())
        self.assertEqual((self.plans, self.store.backups), ([], 0))
        self.assertFalse((self.parent / (NAME + '.update')).exists())
        self.assertTrue(self.exe.is_file())

    def test_a_broken_package_leaves_no_download_behind(self):
        with zipfile.ZipFile(self.package, 'w') as zf:
            zf.writestr('README.md', 'no app inside')
        self.updater.check()
        self.updater.start()
        status = self.finish()
        self.assertIn('実行ファイルがありません', status['task']['message'])
        self.assertFalse((self.parent / (NAME + '.update')).exists())

    def test_refuses_while_collecting_or_without_a_checked_version(self):
        with self.assertRaises(ValueError):
            self.updater.start()
        self.updater.check()
        self.store.collector.active = True
        with self.assertRaises(ValueError):
            self.updater.start()
        self.store.collector.active, self.store.collector.maintenance = False, True  # a delete or lock is running
        with self.assertRaisesRegex(ValueError, '削除・ロック'):
            self.updater.start()

    def test_trial_windows_cannot_update(self):
        self.versions.trial = {'branch': 'feature/x'}
        self.assertIsNone(self.updater.status()['mode'])
        with self.assertRaises(ValueError):
            self.updater.check()


@unittest.skipUnless(shutil.which('git'), 'git is not installed')
class SourceUpdateTests(unittest.TestCase):
    def git(self, cwd, *args):
        return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', '-c', 'init.defaultBranch=main', *args],
                              cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, folder, version):
        (Path(folder) / 'VERSION').write_text(version + '\n', encoding='utf-8')
        self.git(folder, 'add', 'VERSION')
        self.git(folder, 'commit', '-q', '-m', version)
        self.git(folder, 'push', '-q', 'origin', 'HEAD:main')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows: the started app may still be closing
        base = Path(self.tmp.name)
        self.git(base, 'init', '-q', '--bare', 'origin.git')
        self.git(base, 'clone', '-q', 'origin.git', 'author')
        self.git(base / 'author', 'checkout', '-q', '-b', 'main')
        self.commit(base / 'author', '1.4.0')
        self.git(base, 'clone', '-q', 'origin.git', 'checkout')
        self.root = base / 'checkout'
        (self.root / 'launch.py').write_text('', encoding='utf-8')
        self.git(self.root, 'add', 'launch.py')
        self.git(self.root, 'commit', '-q', '-m', 'launcher')
        self.git(self.root, 'push', '-q', 'origin', 'main')
        self.git(base / 'author', 'pull', '-q', 'origin', 'main')
        self.commit(base / 'author', '1.4.1')
        self.data = base / 'data'
        self.versions = FakeVersions(platform='macos', sha=self.git(self.root, 'rev-parse', 'HEAD'))
        self.versions.build['packaged'] = False
        self.stopped = threading.Event()
        self.updater = updater.Updater(self.data, self.root, self.versions, FakeStore(), shutdown=self.stopped.set,
                                       executable=sys.executable, argv=[str(self.root / 'app' / 'desktop.py'), '--data', str(self.data)], frozen=False)
        self.plans = []
        self.updater._launch_helper = self.plans.append

    def tearDown(self):
        self.tmp.cleanup()

    def test_fast_forwards_to_main_and_restarts_through_launch_py(self):
        latest = self.updater.check()['latest']
        self.assertEqual((latest['available'], latest['newer'], latest['version']), (True, True, '1.4.1'))
        self.assertEqual(self.updater.status()['mode'], 'source')
        self.updater.start()
        self.assertTrue(self.stopped.wait(20), self.updater.status())
        self.assertEqual((self.root / 'VERSION').read_text(encoding='utf-8').strip(), '1.4.1')
        plan = self.plans[0]
        self.assertEqual([plan['exe']] + plan['args'], [sys.executable, str(self.root / 'launch.py'), '--data', str(self.data)])
        self.assertEqual(plan['install'], '')

    def test_local_changes_block_the_update(self):
        (self.root / 'launch.py').write_text('# edited\n', encoding='utf-8')
        latest = self.updater.check()['latest']
        self.assertTrue(latest['available'])
        self.assertIn('未コミット', latest['blocked'])
        with self.assertRaises(ValueError):
            self.updater.start()

    def test_up_to_date_checkout(self):
        self.git(self.root, 'pull', '-q', 'origin', 'main')
        self.assertFalse(self.updater.check()['latest']['available'])


if __name__ == '__main__':
    unittest.main()
