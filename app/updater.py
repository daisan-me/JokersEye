"""Update this app to the newest build of main and restart it (standard library only).

Packaged app (tools/build_package.py): download main's newest package for this OS from GitHub
Actions (the same artifacts as the バージョン page, with the same token), unpack it, then hand
over to a small helper script and quit. The helper waits for the app to exit, puts the new build
where the old one was (so shortcuts, the taskbar and the Dock keep working), keeps the old build
as the previous version, records the outcome and starts the app again.

Source checkout (python launch.py): `git fetch` + fast-forward to origin/main, then the same
helper restarts the app through launch.py (which installs changed requirements).

Files live in <data>/updates/: plan.json (what is being done), result.json (written by the
helper), the helper script, and on macOS the download and the previous version.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone

from versions import APP_NAME, REPO, WORKFLOW, unpack_package

BRANCH = "main"
HELPER_TIMEOUT = 120  # seconds the helper waits for the app to exit
# The helpers are PowerShell / sh, not Python (an exception to AGENTS.md): they run after the app has
# exited, and a packaged app carries no Python interpreter that could run on its own.
PREVIOUS_SUFFIX = ".previous"
UPDATE_SUFFIX = ".update"

BUSY = "データの取得・削除・ロックの処理中は更新できません。終わってから更新してください。"

POWERSHELL_HELPER = r"""param([Parameter(Mandatory=$true)][string]$PlanPath)
# Joker's eye update helper, written by app/updater.py. Waits for the app to exit, swaps the
# install folder with the downloaded build, records the outcome and starts the app again.
$ErrorActionPreference = 'Stop'
$plan = [IO.File]::ReadAllText($PlanPath) | ConvertFrom-Json
function Save-Result([string]$status, [string]$detail) {
  $json = @{ status = $status; detail = $detail } | ConvertTo-Json -Compress
  [IO.File]::WriteAllText($plan.result, $json, (New-Object Text.UTF8Encoding $false))
}
$deadline = (Get-Date).AddSeconds([double]$plan.timeout)
while ((Get-Process -Id ([int]$plan.pid) -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $deadline)) { Start-Sleep -Milliseconds 200 }
if (Get-Process -Id ([int]$plan.pid) -ErrorAction SilentlyContinue) { Save-Result 'busy' ''; exit 1 }
$status = 'restarted'; $detail = ''
if ($plan.install) {
  try {
    if (Test-Path -LiteralPath $plan.backup) { Remove-Item -LiteralPath $plan.backup -Recurse -Force }
    # Windows keeps a folder locked for a moment after the app (and its WebView2 helpers) exit.
    for ($i = 1; ; $i++) {
      try { Move-Item -LiteralPath $plan.install -Destination $plan.backup; break }
      catch { if ($i -ge 60) { throw }; Start-Sleep -Milliseconds 500 }
    }
    try { Move-Item -LiteralPath $plan.staged -Destination $plan.install }
    catch { Move-Item -LiteralPath $plan.backup -Destination $plan.install; throw }
    $status = 'ok'
  } catch { $status = 'failed'; $detail = $_.Exception.Message }
}
if ($plan.work -and (Test-Path -LiteralPath $plan.work)) { Remove-Item -LiteralPath $plan.work -Recurse -Force -ErrorAction SilentlyContinue }
Save-Result $status $detail
$start = @{ FilePath = $plan.exe; WorkingDirectory = $plan.cwd }
if ($plan.argline) { $start.ArgumentList = $plan.argline }
Start-Process @start
"""

SHELL_HELPER = r"""#!/bin/sh
# Joker's eye update helper, written by app/updater.py. Waits for the app to exit, swaps the
# install with the downloaded build, records the outcome and starts the app again.
# Arguments: pid timeout result install staged backup work cwd command...
pid=$1; timeout=$2; result=$3; install=$4; staged=$5; backup=$6; work=$7; cwd=$8; shift 8
waited=0
while kill -0 "$pid" 2>/dev/null; do
  if [ "$waited" -ge $((timeout * 5)) ]; then printf '{"status":"busy","detail":""}\n' > "$result"; exit 1; fi
  sleep 0.2; waited=$((waited + 1))
done
status=restarted
if [ -n "$install" ]; then
  status=failed
  rm -rf "$backup"; mkdir -p "$(dirname "$backup")"
  if mv "$install" "$backup"; then
    if mv "$staged" "$install"; then status=ok; else mv "$backup" "$install"; fi
  fi
fi
if [ -n "$work" ]; then rm -rf "$work"; fi
printf '{"status":"%s","detail":""}\n' "$status" > "$result"
cd "$cwd" 2>/dev/null
"$@" </dev/null >/dev/null 2>&1 &
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def install_layout(executable, platform):
    """The packaged app being run: (folder or bundle to replace, executable inside it)."""
    executable = Path(executable)
    if platform == "macos":
        bundle = executable.parents[2] if len(executable.parents) > 2 else None
        if not bundle or bundle.suffix != ".app" or executable.parent.name != "MacOS":
            raise ValueError("アプリの場所を確認できません。")
        if "/AppTranslocation/" in str(bundle):
            raise ValueError("ダウンロードした場所から直接開いているため更新できません。Finderで「アプリケーション」フォルダーへ移してから開き直してください。")
        return bundle, executable
    if platform == "windows":
        return executable.parent, executable
    raise ValueError("この機能はWindowsとmacOSで使えます。")


def parse_time(value):
    """GitHub ('...Z') and build-info ('...+00:00') timestamps, comparable; None when missing."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def newest_main_build(runs, artifacts, platform):
    """main's newest successful package for this OS (runs: newest first; artifacts: run id -> list)."""
    for run in runs:
        if run.get("head_branch") != BRANCH or run.get("conclusion") != "success" or run.get("event") == "pull_request":
            continue
        artifact = next((a for a in artifacts.get(run["id"], [])
                         if a.get("name", "").startswith("JokersEye-") and a["name"].endswith("-" + platform) and not a.get("expired")), None)
        if artifact:
            return {"sha": run["head_sha"], "builtAt": run.get("updated_at") or run.get("created_at"), "runUrl": run.get("html_url"),
                    "artifactId": artifact["id"], "size": artifact.get("size_in_bytes", 0),
                    "version": artifact["name"][len("JokersEye-"):-len("-" + platform)]}
    return None


def relaunch_arguments(argv):
    """The app's own options to start again with (never the one-off --diagnostics)."""
    result, skip = [], False
    for value in argv:
        if skip:
            skip = False
        elif value == "--diagnostics":
            skip = True
        elif not value.startswith("--diagnostics="):
            result.append(value)
    return result


class Updater:
    def __init__(self, folder, root, versions, store, shutdown, executable=None, argv=None, frozen=None):
        self.folder = Path(folder)
        self.home = self.folder / "updates"
        self.root = Path(root)
        self.versions = versions
        self.store = store
        self.shutdown = shutdown
        self.executable = Path(executable or sys.executable)
        self.argv = list(sys.argv if argv is None else argv)
        self.frozen = getattr(sys, "frozen", False) if frozen is None else frozen
        self.platform = versions.platform
        self.lock = threading.Lock()
        self.task = {"state": "idle"}
        self.latest = None
        self.pid = os.getpid()  # the process the helper waits for
        self.timeout = HELPER_TIMEOUT
        self.helper = None

    # ---- what can be done here ----------------------------------------
    def mode(self):
        """('package' | 'source' | None, reason when None)."""
        if self.versions.trial:
            return None, "試用中の版は更新できません。本番のアプリで更新してください。"
        if not self.platform:
            return None, "この機能はWindowsとmacOSで使えます。"
        if self.frozen:
            return "package", ""
        if (self.root / ".git").exists():
            return "source", ""
        return None, "このアプリの置き場所では自動更新を使えません。"

    def status(self):
        mode, reason = self.mode()
        with self.lock:
            task, latest = dict(self.task), self.latest
        return {"mode": mode, "reason": reason, "branch": BRANCH, "platform": self.platform, "current": self.versions.build,
                "tokenRegistered": bool(self.versions.token()), "task": task, "latest": latest, "lastResult": self.last_result()}

    def _set(self, **values):
        with self.lock:
            self.task.update(values)

    # ---- checking ------------------------------------------------------
    def check(self):
        mode, reason = self.mode()
        if not mode:
            raise ValueError(reason)
        latest = self._check_package() if mode == "package" else self._check_source()
        with self.lock:
            self.latest = latest
        return self.status()

    def _check_package(self):
        runs = self.versions.request("/repos/%s/actions/workflows/%s/runs?branch=%s&event=push&status=success&per_page=10"
                                     % (REPO, WORKFLOW, BRANCH)).get("workflow_runs", [])
        found = None
        for run in runs:  # newest first; stop at the first one with a package for this OS
            artifacts = {run["id"]: self.versions.request("/repos/%s/actions/runs/%s/artifacts" % (REPO, run["id"])).get("artifacts", [])}
            found = newest_main_build([run], artifacts, self.platform)
            if found:
                break
        if not found:
            return {"available": False, "checkedAt": now(), "message": "mainの配布物が見つかりません（作成中か、保存期限切れです）。"}
        built, made = parse_time(self.versions.build.get("builtAt")), parse_time(found["builtAt"])
        found.update(checkedAt=now(), available=found["sha"] != self.versions.build.get("sha"),
                     newer=found["sha"] != self.versions.build.get("sha") and (not built or not made or made > built))
        return found

    def _git(self, *args, timeout=60):
        environment = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        try:
            done = subprocess.run(["git", *args], cwd=str(self.root), capture_output=True, text=True, timeout=timeout, env=environment)
        except FileNotFoundError:
            raise ValueError("gitが見つかりません。") from None
        except subprocess.TimeoutExpired:
            raise ValueError("gitの処理が時間内に終わりませんでした。") from None
        if done.returncode:
            raise ValueError("git %s に失敗しました：%s" % (args[0], (done.stderr or done.stdout).strip()[-300:]))
        return done.stdout.strip()

    def _check_source(self):
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        self._git("fetch", "--quiet", "origin", BRANCH)
        head, remote = self._git("rev-parse", "HEAD"), self._git("rev-parse", "origin/" + BRANCH)
        result = {"sha": remote, "checkedAt": now(), "builtAt": self._git("log", "-1", "--format=%cI", "origin/" + BRANCH),
                  "version": self._git("show", "origin/%s:VERSION" % BRANCH), "available": remote != head, "newer": False}
        if remote == head:
            return result
        if branch != BRANCH:
            result["blocked"] = "%s ブランチを開いているため更新できません（mainに切り替えてから更新してください）。" % branch
        elif self._git("status", "--porcelain", "--untracked-files=no"):
            result["blocked"] = "手元に未コミットの変更があるため更新できません。"
        elif subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", "origin/" + BRANCH], cwd=str(self.root),
                            capture_output=True).returncode:
            result["blocked"] = "手元のmainにpushしていないコミットがあるため更新できません。"
        result["newer"] = "blocked" not in result
        return result

    # ---- updating ------------------------------------------------------
    def start(self, backup=False):
        mode, reason = self.mode()
        if not mode:
            raise ValueError(reason)
        if self.store.collector.active or self.store.collector.maintenance:
            raise ValueError(BUSY)
        if not isinstance(backup, bool):
            raise ValueError("バックアップの指定が不正です。")
        with self.lock:
            latest = self.latest
            if self.task.get("state") == "running":
                raise ValueError("更新の処理中です。")
            if not latest or not latest.get("available") or latest.get("blocked"):
                raise ValueError("更新できる版がありません。先に「最新版を確認」を押してください。")
            self.task = {"state": "running", "step": "prepare", "message": "更新を準備しています。", "done": 0,
                         "total": latest.get("size", 0), "sha": latest["sha"], "version": latest.get("version", "")}
        threading.Thread(target=self._run, args=(mode, latest, backup), daemon=True).start()
        return self.status()

    def _run(self, mode, latest, backup):
        try:
            with ExitStack() as stack:
                try:
                    stack.enter_context(self.store.collector.idle())  # no data run starts while the app is being replaced
                except ValueError:
                    raise ValueError(BUSY) from None
                places = self._places() if mode == "package" else None  # checked before the backup and the download
                if backup:
                    self._set(step="backup", message="GDBのバックアップを作成しています。")
                    self.store.backup()
                plan = self._prepare_package(latest, *places) if mode == "package" else self._prepare_source(latest)
                self._set(step="restart", message="新しい版で起動し直します。")
                try:
                    self._launch_helper(plan)
                except Exception:
                    self.acknowledge()  # no helper: nothing will report on this plan
                    raise
            self.shutdown()
        except Exception as ex:
            self._set(state="failed", message="更新できませんでした：%s" % ex)

    def _places(self):
        """(install, work folder, previous-version path), after checking that the install can be swapped."""
        install, _ = install_layout(self.executable, self.platform)
        if self.platform == "windows":
            # The whole folder is swapped: refuse when it holds anything but this app's own files
            # (a PyInstaller folder: the EXE, its .NET config and _internal).
            known = {self.executable.name, self.executable.name + ".config", "_internal"}
            if not (install / "_internal").is_dir() or any(p.name not in known for p in install.iterdir()):
                raise ValueError("%s にアプリ以外のファイルがあるため、フォルダーを入れ替えられません。" % install)
            # Same drive as the install, so the helper can swap the folders by renaming them.
            return install, install.parent / (install.name + UPDATE_SUFFIX), install.parent / (install.name + PREVIOUS_SUFFIX)
        if not os.access(install.parent, os.W_OK):
            raise ValueError("%s に書き込めないため、アプリを入れ替えられません。" % install.parent)
        return install, self.home / "download", self.home / "previous" / install.name

    def _prepare_package(self, latest, install, work, backup):
        shutil.rmtree(work, ignore_errors=True)
        try:
            work.mkdir(parents=True)
        except OSError as ex:
            raise ValueError("%s に書き込めません（%s）。" % (work.parent, ex.strerror or ex)) from None
        try:
            return self._stage(latest, install, work, backup)
        except Exception:
            shutil.rmtree(work, ignore_errors=True)  # do not leave the download next to the app
            raise

    def _stage(self, latest, install, work, backup):
        archive = work / "package.zip"
        self._set(step="download", message="ダウンロードしています。")
        self.versions.download(latest["artifactId"], archive,
                               lambda done, total: self._set(done=done, total=total or latest.get("size", 0)))
        self._set(step="extract", message="展開しています。")
        unpacked = work / "package"
        staged = unpack_package(archive, unpacked, self.platform)
        archive.unlink()
        program = staged / "Contents" / "MacOS" / APP_NAME if self.platform == "macos" else staged / (APP_NAME + ".exe")
        if not program.is_file():
            raise ValueError("ダウンロードした版に実行ファイルがありません。")
        for doc in ("README.md", "CHANGELOG.md"):  # the copies next to the app, when the package was unpacked as a whole
            if self.platform == "windows" and (install.parent / doc).is_file() and (unpacked / doc).is_file():
                shutil.copy2(unpacked / doc, install.parent / doc)
        arguments = relaunch_arguments(self.argv[1:])
        if self.platform == "macos":
            command = ["/usr/bin/open", "-n", str(install)] + (["--args"] + arguments if arguments else [])
        else:
            command = [str(install / self.executable.name)] + arguments
        return self._plan(latest, command, install.parent, install=install, staged=staged, backup=backup, work=work)

    def _prepare_source(self, latest):
        self._set(step="download", message="mainの最新版を取り込んでいます。")
        self._git("merge", "--ff-only", "--quiet", "origin/" + BRANCH)
        if self._git("rev-parse", "HEAD") != latest["sha"]:
            raise ValueError("取り込んだ版が確認した版と一致しません。もう一度確認してください。")
        script = Path(self.argv[0]).resolve() if self.argv else None
        if script and script.name == "desktop.py" and (self.root / "launch.py").is_file():
            command = [str(self.executable), str(self.root / "launch.py")] + relaunch_arguments(self.argv[1:])
        else:
            command = [str(self.executable), str(script)] + relaunch_arguments(self.argv[1:])
        return self._plan(latest, command, self.root)

    def _plan(self, latest, command, cwd, install=None, staged=None, backup=None, work=None):
        self.home.mkdir(parents=True, exist_ok=True)
        plan = {"pid": self.pid, "timeout": self.timeout, "result": str(self.home / "result.json"),
                "install": str(install or ""), "staged": str(staged or ""), "backup": str(backup or ""), "work": str(work or ""),
                "cwd": str(cwd), "exe": command[0], "args": command[1:], "argline": subprocess.list2cmdline(command[1:]),
                "from": {k: self.versions.build.get(k) for k in ("version", "branch", "sha")},
                "to": {"version": latest.get("version", ""), "branch": BRANCH, "sha": latest["sha"]}, "startedAt": now()}
        (self.home / "result.json").unlink(missing_ok=True)
        # ASCII JSON: Windows PowerShell 5.1 reads it without guessing the encoding.
        (self.home / "plan.json").write_text(json.dumps(plan), encoding="ascii")
        return plan

    def _launch_helper(self, plan):
        if os.name == "nt":
            script = self.home / "update-helper.ps1"
            script.write_text(POWERSHELL_HELPER, encoding="ascii")
            powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            command = [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                       "-File", str(script), str(self.home / "plan.json")]
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            options = {"creationflags": flags}
        else:
            script = self.home / "update-helper.sh"
            script.write_text(SHELL_HELPER, encoding="utf-8")
            command = ["/bin/sh", str(script), str(plan["pid"]), str(plan["timeout"]), plan["result"], plan["install"], plan["staged"],
                       plan["backup"], plan["work"], plan["cwd"], plan["exe"], *plan["args"]]
            options = {"start_new_session": True}
        # The helper must not sit in the install folder (Windows could not rename it then).
        self.helper = subprocess.Popen(command, cwd=str(self.home), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, close_fds=True, **options)

    # ---- after the restart ----------------------------------------------
    def last_result(self):
        try:
            plan = json.loads((self.home / "plan.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            result = json.loads((self.home / "result.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            result = {"status": "pending", "detail": ""}
        if result.get("status") == "pending" and plan.get("pid") == self.pid:
            return None  # this app is the one about to be replaced
        return {"status": result.get("status"), "detail": result.get("detail", ""), "from": plan.get("from"), "to": plan.get("to"),
                "backup": plan.get("backup") or None, "startedAt": plan.get("startedAt"),
                "running": dict(self.versions.build)}

    def acknowledge(self):
        for name in ("plan.json", "result.json"):
            (self.home / name).unlink(missing_ok=True)
        return {"ok": True}
