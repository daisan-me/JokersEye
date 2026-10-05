"""Cross-platform entry point for Windows and macOS: `python launch.py`.

First run creates `.venv` and installs `requirements.txt`; later runs start immediately.
Extra arguments are passed to app/desktop.py (e.g. `--data <folder>`).
"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
MINIMUM = (3, 10)


def venv_python(windowed):
    if os.name == "nt":
        name = "pythonw.exe" if windowed else "python.exe"
        return VENV / "Scripts" / name
    return VENV / "bin" / "python"


def ensure_environment():
    if sys.version_info < MINIMUM:
        raise SystemExit("Python %d.%d 以上が必要です（.python-version は 3.12）。" % MINIMUM)
    python = venv_python(False)
    if not python.exists():
        print("初回セットアップ: 仮想環境を作成します…", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    stamp = VENV / ".requirements.sha256"
    digest = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    if not stamp.exists() or stamp.read_text(encoding="utf-8").strip() != digest:
        print("依存パッケージをインストールします…", flush=True)
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQUIREMENTS)], check=True)
        stamp.write_text(digest, encoding="utf-8")


def main(argv):
    windowed = os.path.basename(sys.executable).lower() == "pythonw.exe"
    ensure_environment()
    command = [str(venv_python(windowed)), str(ROOT / "app" / "desktop.py")] + list(argv)
    if os.name == "nt":
        raise SystemExit(subprocess.call(command, cwd=str(ROOT)))
    os.chdir(ROOT)
    os.execv(command[0], command)


if __name__ == "__main__":
    main(sys.argv[1:])
