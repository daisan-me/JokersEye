"""Start the built app once with a temporary data folder and check that its window shows the UI.

Uses the app's own `--diagnostics` mode (connection text, version, store map), then the app
shuts itself down. On Windows the check runs on a copy whose files carry the "downloaded from
the Internet" mark (Zone.Identifier), as after a browser download and Explorer's "Extract All",
which is how users receive the package. Run after tools/build_package.py:
    python tools/smoke_test_package.py
    python tools/smoke_test_package.py --without-dotnet-config   # Windows: report how that copy fails without the .NET config
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_package import NAME, ROOT, app_path, dotnet_config_path, executable_path  # noqa: E402

TIMEOUT = 180
INTERNET_ZONE = "[ZoneTransfer]\r\nZoneId=3\r\n"


def downloaded_copy(folder):
    """Windows: a copy of the app marked as downloaded. Elsewhere: the built app itself."""
    if not sys.platform.startswith("win"):
        return executable_path()
    copy = Path(folder) / "app"
    shutil.copytree(app_path(), copy)
    for path in copy.rglob("*"):
        if path.is_file():
            with open(str(path) + ":Zone.Identifier", "w", encoding="ascii") as stream:
                stream.write(INTERNET_ZONE)
    return copy / (NAME + ".exe")


def run(executable, folder):
    data = Path(folder) / "data"
    report = Path(folder) / "diagnostics.json"
    map_report = Path(str(report) + ".map.json")
    failure = data / "desktop.log"  # written by the app when it cannot start
    process = subprocess.Popen([str(executable), "--data", str(data), "--diagnostics", str(report)],
                               env=dict(os.environ, JOKERS_EYE_DATA=str(data)))
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline and not map_report.exists() and not failure.exists() and process.poll() is None:
        time.sleep(1)
    if failure.exists():
        time.sleep(1)
        process.kill()  # the error dialog would wait for a click
        process.wait(timeout=30)  # Windows keeps the app's DLLs locked until it has exited
    else:
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=30)
    for log in sorted(data.glob("*.log")):
        print("----- %s\n%s" % (log.name, log.read_text(encoding="utf-8", errors="replace")[-4000:]))
    return report, map_report, failure, process


def reproduce_without_config():
    if not sys.platform.startswith("win"):
        print("Windows専用の確認です。")
        return
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        executable = downloaded_copy(folder)
        dotnet_config_path(executable.parent).unlink()
        _, map_report, failure, _ = run(executable, folder)
        if failure.exists() and "Python.Runtime" in failure.read_text(encoding="utf-8", errors="replace"):
            print("再現：.NETの設定がないと、ダウンロードした状態のファイルでは起動できない（Python.Runtime.dllを読み込めない）。")
        elif map_report.exists():
            print("再現せず：.NETの設定がなくても起動した。")
        else:
            print("再現せず：別の理由で起動を確認できなかった。")


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to a code page without「●」etc.
    parser = argparse.ArgumentParser()
    parser.add_argument("--without-dotnet-config", action="store_true")
    if parser.parse_args().without_dotnet_config:
        return reproduce_without_config()
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        report, map_report, failure, process = run(downloaded_copy(folder), folder)
        if failure.exists() or not map_report.exists():
            raise SystemExit("アプリの画面を確認できませんでした（%d秒以内に診断結果なし、終了コード %s）。" % (TIMEOUT, process.returncode))
        page = json.loads(report.read_text(encoding="utf-8"))["page"]
        floor = json.loads(map_report.read_text(encoding="utf-8"))
        print("page:", page)
        print("map:", floor)
        if version not in page["version"]:
            raise SystemExit("表示された版数 %r が VERSION %s と一致しません。" % (page["version"], version))
        if "ローカル接続" not in page["connection"]:
            raise SystemExit("ローカルサービスに接続できていません: %r" % page["connection"])
        if not (floor.get("physical") or floor.get("seats")):
            raise SystemExit("店内マップが表示されていません: %r" % floor)
    print("OK")


if __name__ == "__main__":
    main()
