"""Build the double-click package for the current OS (Windows: folder with "Joker's eye.exe", macOS: "Joker's eye.app").

Python and pywebview are bundled, so users need no installation or command line.
Run in CI (.github/workflows/package.yml):
    python -m pip install -r requirements.txt -r requirements-build.txt
    python tools/build_package.py

Output: build/dist/ (the app) and build/package/ (what users download).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "Joker's eye"
BUNDLE_ID = "me.daisan.jokers-eye"
BUILD = ROOT / "build"
DIST = BUILD / "dist"
PACKAGE = BUILD / "package"
DOCS = ("README.md", "CHANGELOG.md")


# .NET Framework refuses assemblies carrying the "downloaded from the Internet" mark, which Explorer's
# "Extract All" copies onto every file of a downloaded ZIP. pywebview loads Python.Runtime.dll and the
# WebView2 assemblies through pythonnet, so the packaged app opts in for its own files.
DOTNET_CONFIG = """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <runtime>
    <loadFromRemoteSources enabled="true" />
  </runtime>
</configuration>
"""


def dotnet_config_path(folder=None):
    """Windows: the config .NET reads for the app (<exe>.config next to the EXE)."""
    return (Path(folder) if folder else app_path()) / (NAME + ".exe.config")


def app_path():
    """The built app: a folder with the EXE on Windows, an .app bundle on macOS."""
    return DIST / (NAME + ".app") if sys.platform == "darwin" else DIST / NAME


def executable_path():
    if sys.platform == "darwin":
        return app_path() / "Contents" / "MacOS" / NAME
    return app_path() / (NAME + ".exe")


def build_info():
    """Shown on the app's バージョン page; lets the app tell which branch/commit it is."""
    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True, check=True).stdout.strip() or None
        except (OSError, subprocess.CalledProcessError):
            return None
    branch = os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME") or git("rev-parse", "--abbrev-ref", "HEAD")
    sha = os.environ.get("GITHUB_SHA") or git("rev-parse", "HEAD")
    return {"branch": branch, "sha": sha, "runId": os.environ.get("GITHUB_RUN_ID"),
            "builtAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def icon_argument(work):
    if sys.platform.startswith("win"):
        return ["--icon", str(ROOT / "web" / "icon.ico")]
    if sys.platform == "darwin":
        sys.path.insert(0, str(ROOT / "tools"))
        from make_mac_app import make_icns
        icns = Path(work) / "icon.icns"
        make_icns(ROOT / "web" / "icon.png", icns)
        return ["--icon", str(icns)]
    return []


def build():
    if not (sys.platform.startswith("win") or sys.platform == "darwin"):
        raise SystemExit("Windows / macOS 専用のツールです。")
    shutil.rmtree(BUILD, ignore_errors=True)
    separator = ";" if sys.platform.startswith("win") else ":"
    BUILD.mkdir(parents=True)
    info = BUILD / "build-info.json"
    info.write_text(json.dumps(build_info(), ensure_ascii=False), encoding="utf-8")
    with tempfile.TemporaryDirectory() as work:
        command = [sys.executable, "-m", "PyInstaller", str(ROOT / "app" / "desktop.py"),
                   "--name", NAME, "--windowed", "--noconfirm", "--clean",
                   "--paths", str(ROOT / "app"),
                   "--add-data", str(ROOT / "web") + separator + "web",
                   "--add-data", str(ROOT / "VERSION") + separator + ".",
                   "--add-data", str(info) + separator + ".",
                   "--distpath", str(DIST), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD),
                   "--osx-bundle-identifier", BUNDLE_ID] + icon_argument(work)
        subprocess.run(command, check=True, cwd=str(ROOT))
    if not executable_path().exists():
        raise SystemExit("ビルド結果が見つかりません: %s" % executable_path())
    if sys.platform.startswith("win"):
        dotnet_config_path().write_text(DOTNET_CONFIG, encoding="utf-8")
    PACKAGE.mkdir(parents=True)
    if sys.platform == "darwin":
        # ditto keeps the bundle's permissions and symlinks; GitHub's artifact zip does not.
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(app_path()), str(PACKAGE / (NAME + ".zip"))], check=True)
    else:
        shutil.copytree(app_path(), PACKAGE / NAME)
    for doc in DOCS:
        shutil.copy2(ROOT / doc, PACKAGE / doc)
    return PACKAGE


if __name__ == "__main__":
    print(build())
