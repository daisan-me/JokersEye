"""Start the built app once with a temporary data folder and check that its window shows the UI.

Uses the app's own `--diagnostics` mode (connection text, version, store map), then the app
shuts itself down. Run after tools/build_package.py:
    python tools/smoke_test_package.py
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_package import ROOT, executable_path  # noqa: E402

TIMEOUT = 180


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to a code page without「●」etc.
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    with tempfile.TemporaryDirectory() as folder:
        report = Path(folder) / "diagnostics.json"
        process = subprocess.Popen([str(executable_path()), "--data", str(Path(folder) / "data"), "--diagnostics", str(report)])
        deadline = time.monotonic() + TIMEOUT
        map_report = Path(str(report) + ".map.json")
        while time.monotonic() < deadline and not map_report.exists() and process.poll() is None:
            time.sleep(1)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
        logs = sorted((Path(folder) / "data").glob("*.log"))
        for log in logs:
            print("----- %s\n%s" % (log.name, log.read_text(encoding="utf-8", errors="replace")[-4000:]))
        if not map_report.exists():
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
