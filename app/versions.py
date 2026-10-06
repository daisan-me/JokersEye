"""Versions: list the packaged build of each branch on GitHub, download one and run it
side by side with a copy of this app's data (standard library only).

Packages are made by .github/workflows/package.yml. A downloaded version lives in
<data>/versions/<id>/ with its own app/ and data/ folders, so a development build can
change its database freely without touching the production data.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

REPO = "daisan-me/JokersEye"
WORKFLOW = "package.yml"
API = "https://api.github.com"
APP_NAME = "Joker's eye"
TRIAL_ENV = "JOKERS_EYE_TRIAL"
TOKEN_PAGE = "https://github.com/settings/personal-access-tokens/new"


def platform_label(platform=None):
    platform = sys.platform if platform is None else platform
    if platform.startswith("win"):
        return "windows"
    if platform == "darwin":
        return "macos"
    return None


def version_id(branch, sha):
    """Folder-safe, stable name for one build of one branch."""
    return (re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-.")[:60] or "branch") + "-" + sha[:7]


def git_head(root):
    """Branch and commit of a source checkout, read without running git."""
    git = Path(root) / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None, None
    if not head.startswith("ref: "):
        return None, head
    ref = head[5:]
    branch = ref.rsplit("refs/heads/", 1)[-1]
    try:
        return branch, (git / ref).read_text(encoding="utf-8").strip()
    except OSError:
        pass
    try:
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return branch, line.split(" ", 1)[0]
    except OSError:
        pass
    return branch, None


def load_build_info(root, version):
    """What is running: written into packages by tools/build_package.py, else read from git."""
    path = Path(root) / "build-info.json"
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        info["packaged"] = True
    except (OSError, ValueError):
        branch, sha = git_head(root)
        info = {"branch": branch, "sha": sha, "builtAt": None, "packaged": False}
    info["version"] = version
    return info


def latest_builds(runs, artifacts, platform):
    """One row per branch: its newest successful package for this OS, plus the newest run's state.

    `runs` is GitHub's list (newest first); `artifacts` maps run id -> that run's artifacts.
    """
    rows, seen = [], set()
    newest = {}
    for run in runs:
        newest.setdefault(run["head_branch"], run)
    for run in runs:
        branch = run["head_branch"]
        if branch in seen or run.get("conclusion") != "success":
            continue
        artifact = next((a for a in artifacts.get(run["id"], [])
                         if a["name"].endswith("-" + platform) and not a.get("expired")), None)
        if artifact is None:
            continue
        seen.add(branch)
        latest = newest[branch]
        rows.append({"id": version_id(branch, run["head_sha"]), "branch": branch, "sha": run["head_sha"],
                     "version": artifact["name"][len("JokersEye-"):-len("-" + platform)] if artifact["name"].startswith("JokersEye-") else "",
                     "builtAt": run.get("updated_at") or run.get("created_at"), "runUrl": run.get("html_url"),
                     "artifactId": artifact["id"], "size": artifact.get("size_in_bytes", 0),
                     "expiresAt": artifact.get("expires_at"),
                     "newer": None if latest["id"] == run["id"] else {"status": latest.get("status"), "conclusion": latest.get("conclusion"), "url": latest.get("html_url")}})
    for branch, run in newest.items():
        if branch not in seen:
            rows.append({"id": None, "branch": branch, "sha": run["head_sha"], "version": "", "builtAt": run.get("created_at"),
                         "runUrl": run.get("html_url"), "artifactId": None, "size": 0, "expiresAt": None,
                         "newer": {"status": run.get("status"), "conclusion": run.get("conclusion"), "url": run.get("html_url")}})
    return rows


def safe_extract(archive, target):
    """Extract a zip, refusing entries that would land outside `target`."""
    target = Path(target).resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            destination = (target / member.filename).resolve()
            if destination != target and target not in destination.parents:
                raise ValueError("ダウンロードした版に不正なファイル名が含まれています。")
        zf.extractall(target)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class VersionManager:
    def __init__(self, folder, root, version, copy_data):
        self.folder = Path(folder)
        self.home = self.folder / "versions"
        self.token_file = self.folder / "github-token.json"
        self.build = load_build_info(root, version)
        try:
            self.trial = json.loads(os.environ.get(TRIAL_ENV) or "null")
        except ValueError:
            self.trial = None
        self.platform = platform_label()
        self.copy_data = copy_data
        self.lock = threading.Lock()
        self.task = {"state": "idle"}
        self.processes = {}

    # ---- GitHub access -------------------------------------------------
    def token(self):
        try:
            return json.loads(self.token_file.read_text(encoding="utf-8")).get("token") or None
        except (OSError, ValueError):
            return None

    def request(self, path, token=None, opener=None):
        token = token or self.token()
        if not token:
            raise ValueError("GitHubのトークンが登録されていません。")
        req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "JokersEye"})
        try:
            with (opener or urllib.request.build_opener()).open(req, timeout=20) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as ex:
            if ex.code in (401, 403, 404):
                raise ValueError("GitHubで %s を読めませんでした（HTTP %s）。トークンの権限を確認してください。" % (REPO, ex.code)) from None
            raise ValueError("GitHubとの通信に失敗しました（HTTP %s）。" % ex.code) from None
        except urllib.error.URLError as ex:
            raise ValueError("GitHubに接続できませんでした（%s）。" % ex.reason) from None

    def set_token(self, token):
        if self.trial:
            raise ValueError("試用中の版ではトークンを登録できません。本番のアプリで登録してください。")
        token = str(token or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{20,255}", token):
            raise ValueError("トークンの形式が正しくありません。")
        self.request("/repos/%s" % REPO, token=token)
        self.folder.mkdir(parents=True, exist_ok=True)
        temp = self.token_file.with_suffix(".tmp")
        handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump({"token": token, "savedAt": datetime.now(timezone.utc).isoformat()}, file)
        os.replace(temp, self.token_file)
        return {"ok": True}

    def delete_token(self):
        self.token_file.unlink(missing_ok=True)
        return {"ok": True}

    # ---- listing -------------------------------------------------------
    def installed(self):
        rows = []
        if self.home.is_dir():
            for meta in sorted(self.home.glob("*/version.json")):
                try:
                    info = json.loads(meta.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                process = self.processes.get(info.get("id"))
                info["running"] = bool(process and process.poll() is None)
                rows.append(info)
        return rows

    def overview(self):
        with self.lock:
            task = dict(self.task)
        return {"current": self.build, "trial": self.trial, "platform": self.platform, "repo": REPO,
                "tokenPage": TOKEN_PAGE, "tokenRegistered": bool(self.token()), "installed": self.installed(), "task": task}

    def remote(self):
        if not self.platform:
            raise ValueError("この機能はWindowsとmacOSで使えます。")
        runs = [run for run in self.request("/repos/%s/actions/workflows/%s/runs?per_page=60" % (REPO, WORKFLOW)).get("workflow_runs", [])
                if run.get("event") != "pull_request"]
        artifacts, branches = {}, set()
        for run in runs:
            if run.get("conclusion") == "success" and run["head_branch"] not in branches:
                found = self.request("/repos/%s/actions/runs/%s/artifacts" % (REPO, run["id"])).get("artifacts", [])
                artifacts[run["id"]] = found
                if any(a["name"].endswith("-" + self.platform) and not a.get("expired") for a in found):
                    branches.add(run["head_branch"])
        installed = {row["id"] for row in self.installed()}
        rows = latest_builds(runs, artifacts, self.platform)
        for row in rows:
            row["installed"] = row["id"] in installed
            row["current"] = bool(row["sha"] and row["sha"] == self.build.get("sha"))
        return {"versions": rows}

    # ---- install / launch ----------------------------------------------
    def status(self):
        with self.lock:
            return dict(self.task)

    def _progress(self, **values):
        with self.lock:
            self.task.update(values)

    def install(self, artifact_id):
        if self.trial:
            raise ValueError("試用中の版では、ほかの版をダウンロードできません。本番のアプリから操作してください。")
        if not self.platform:
            raise ValueError("この機能はWindowsとmacOSで使えます。")
        artifact = self.request("/repos/%s/actions/artifacts/%d" % (REPO, int(artifact_id)))
        run = artifact.get("workflow_run") or {}
        if artifact.get("expired") or not artifact.get("name", "").endswith("-" + self.platform) or not run.get("head_branch") or not run.get("head_sha"):
            raise ValueError("この版はこのOS用ではないか、保存期限が切れています。")
        with self.lock:
            if self.task.get("state") == "running":
                raise ValueError("ほかの版をダウンロード中です。終わるまでお待ちください。")
            self.task = {"state": "running", "step": "download", "message": "ダウンロードを準備しています。", "done": 0,
                         "total": artifact.get("size_in_bytes", 0), "branch": run["head_branch"]}
        threading.Thread(target=self._install, args=(artifact, run), daemon=True).start()
        return self.status()

    def _install(self, artifact, run):
        ident = version_id(run["head_branch"], run["head_sha"])
        target = self.home / ident
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.home) as work:
                archive = Path(work) / "package.zip"
                self._download(artifact, archive)
                self._progress(step="extract", message="展開しています。")
                staging = Path(work) / "app"
                safe_extract(archive, staging)
                if self.platform == "macos":
                    inner = staging / (APP_NAME + ".zip")
                    subprocess.run(["ditto", "-x", "-k", str(inner), str(staging)], check=True)
                    inner.unlink()
                if target.exists():
                    shutil.rmtree(target / "app", ignore_errors=True)
                target.mkdir(exist_ok=True)
                shutil.move(str(staging), str(target / "app"))
            meta = {"id": ident, "branch": run["head_branch"], "sha": run["head_sha"],
                    "version": artifact["name"][len("JokersEye-"):-len("-" + self.platform)],
                    "builtAt": artifact.get("created_at"), "installedAt": datetime.now(timezone.utc).isoformat()}
            (target / "version.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            self.launch(ident)
            self._progress(state="done", step="done", message="%s（%s）を起動しました。" % (meta["branch"], meta["sha"][:7]))
        except Exception as ex:
            self._progress(state="failed", message="版を準備できませんでした：%s" % ex)

    def download_address(self, artifact_id, opener=None):
        """GitHub answers with a short-lived signed address; the token must not be sent there."""
        req = urllib.request.Request(API + "/repos/%s/actions/artifacts/%d/zip" % (REPO, artifact_id),
                                     headers={"Authorization": "Bearer " + (self.token() or ""), "Accept": "application/vnd.github+json",
                                              "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "JokersEye"})
        try:
            (opener or urllib.request.build_opener(_NoRedirect)).open(req, timeout=20).close()
        except urllib.error.HTTPError as ex:
            location = ex.headers.get("Location")
            if ex.code in (301, 302, 303, 307, 308) and location and location.startswith("https://"):
                return location
            raise ValueError("ダウンロード先を取得できませんでした（HTTP %s）。" % ex.code) from None
        raise ValueError("ダウンロード先を取得できませんでした。")

    def _download(self, artifact, archive):
        location = self.download_address(artifact["id"])
        with urllib.request.urlopen(urllib.request.Request(location, headers={"User-Agent": "JokersEye"}), timeout=60) as response, open(archive, "wb") as file:
            total = int(response.headers.get("Content-Length") or artifact.get("size_in_bytes") or 0)
            done = 0
            self._progress(total=total, message="ダウンロードしています。")
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                file.write(chunk)
                done += len(chunk)
                self._progress(done=done)

    def executable(self, ident):
        base = (self.home / ident / "app").resolve()
        if self.platform == "macos":
            path = base / (APP_NAME + ".app") / "Contents" / "MacOS" / APP_NAME
        else:
            path = base / APP_NAME / (APP_NAME + ".exe")
        if self.home.resolve() not in path.parents or not path.is_file():
            raise ValueError("この版の実行ファイルが見つかりません。もう一度ダウンロードしてください。")
        return path

    def launch(self, ident):
        if self.trial:
            raise ValueError("試用中の版から、ほかの版は起動できません。")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", str(ident or "")):
            raise ValueError("版の指定が不正です。")
        try:
            meta = json.loads((self.home / ident / "version.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("この版はダウンロードされていません。") from None
        executable = self.executable(ident)
        data = self.home / ident / "data"
        if not data.exists():
            self._progress(step="copy", message="本番データをこの版用にコピーしています。")
            data.mkdir(parents=True)
            self.copy_data(data)
        process = self.processes.get(ident)
        if process and process.poll() is None:
            return {"ok": True, "running": True}
        environment = dict(os.environ, **{TRIAL_ENV: json.dumps(meta, ensure_ascii=False)})
        environment.pop("JOKERS_EYE_DATA", None)
        self.processes[ident] = subprocess.Popen([str(executable), "--data", str(data)], env=environment, cwd=str(executable.parent),
                                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "running": True}

    def remove(self, ident):
        if not re.fullmatch(r"[A-Za-z0-9._-]+", str(ident or "")) or not (self.home / ident / "version.json").is_file():
            raise ValueError("版の指定が不正です。")
        process = self.processes.get(ident)
        if process and process.poll() is None:
            raise ValueError("この版は起動中です。ウィンドウを閉じてから削除してください。")
        shutil.rmtree(self.home / ident)
        self.processes.pop(ident, None)
        return {"ok": True}
