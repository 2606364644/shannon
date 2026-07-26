from __future__ import annotations

import asyncio
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import aiofiles

from .git_fetcher import GitFetcher, strip_credentials

_PROGRESS_RE = re.compile(r"(?:Receiving objects|Resolving deltas|Compressing objects|Counting objects):\s+(\d+)%")

# 仓库名：`repo` 或 `group/repo`（至多一层分组）。每段非空、不含 / \ NUL。
_REPO_NAME_RE = re.compile(r"^[^\x00/\\]+(/[^\x00/\\]+)?$")
# 单段目录名（不含 / \ NUL）——clone 时 name/group 各段独立校验
_REPO_SEGMENT_RE = re.compile(r"^[^\x00/\\]+$")


def _validate_repo_segment(seg: str, label: str = "名字段") -> None:
    """校验单段目录名（不含 / \\ NUL，非 . ..，非空）。供 clone 的 name/group 各段校验。"""
    if not seg or "\x00" in seg or not _REPO_SEGMENT_RE.match(seg) or seg in (".", ".."):
        raise ValueError(f"非法{label}：{seg!r}")


def _validate_ws_segment(ws: str) -> None:
    """校验 workspace 路径段（URL {ws} 来的，作 workspaces/<ws>/repos 前缀）。

    与单段 repo 名同档约束：非空、不含 ``/`` ``\\`` NUL、非 ``.`` ``..``。
    第二道防线在 _repos_root：resolve().is_relative_to(self._workspaces_dir)。
    """
    if not ws or "\x00" in ws or not _REPO_SEGMENT_RE.match(ws) or ws in (".", ".."):
        raise ValueError(f"非法 workspace：{ws!r}")


def _validate_repo_name(name: str) -> None:
    """校验仓库名合法且无遍历分量（path-traversal 第一道防线）。

    允许 `repo` 或 `group/repo`（至多一层分组，映射 repos_dir/<group>/<repo>）；
    禁止 `..`/`.` 分量、空分量（`a//b`）、首尾 `/`（`/a`、`a/`）、`\\`、NUL、
    多层嵌套（`a/b/c`）。第二道防线见 _resolve_repo_dir 的 resolve().is_relative_to()。

    raise ValueError if name 非法。
    """
    if not name or "\x00" in name or not _REPO_NAME_RE.match(name):
        raise ValueError(f"非法仓库名：{name!r}")
    # 正则已禁多层/空段/首尾斜杠，再防 `..` / `.` 作为整段（如 `..`、`a/..`）
    for part in name.split("/"):
        _validate_repo_segment(part, "仓库名")


def _resolve_repo_dir(repos_dir: Path, name: str) -> Path:
    """校验仓库名并解析为 repos_dir 内的绝对路径（path-traversal 双重防线）。

    即便 _validate_repo_name 正则有漏，resolve().is_relative_to() 兜底确保
    最终路径不越界 repos_dir。返回 resolve 后的绝对路径。
    """
    _validate_repo_name(name)
    base = Path(repos_dir).resolve()
    p = (base / name).resolve()
    if not p.is_relative_to(base):
        raise ValueError(f"非法仓库名：{name!r}")
    return p


def _is_repo(d: Path) -> bool:
    """目录是仓库 iff 有 `.git`（clone 产物）或 `.supernova-repo.json`（已纳入管理）。

    并集：既识别用户已 clone 的真仓库（有 .git），也识别已纳入管理但 .git 损坏的
    （有 meta）。分组目录两者皆无 → False，不被当仓库。
    """
    return (d / ".git").exists() or (d / ".supernova-repo.json").exists()


class TooManyClones(Exception):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"并发 clone 上限 {limit}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


class RepoManager:
    def __init__(self, workspaces_dir: Path, git_fetcher: GitFetcher,
                 max_concurrent: int = 3) -> None:
        self._workspaces_dir = Path(workspaces_dir)
        self._git = git_fetcher
        self._max_concurrent = max(1, max_concurrent)
        self._sem = asyncio.Semaphore(self._max_concurrent)
        self._jobs: dict[tuple[str, str], asyncio.Task] = {}

    # ---- ws 解析 + 校验 ----
    def _repos_root(self, ws: str) -> Path:
        """workspaces/<ws>/repos 的绝对路径（path-traversal 双重防线）。

        ws 段先经 _validate_ws_segment 正则挡掉空/`.``..`/`/`/`\\`/NUL；再用
        resolve().is_relative_to(self._workspaces_dir) 兜底，确保最终路径不越界
        workspaces_dir。
        """
        _validate_ws_segment(ws)
        base = self._workspaces_dir.resolve()
        p = (base / ws / "repos").resolve()
        if not p.is_relative_to(base):
            raise ValueError(f"非法 workspace：{ws!r}")
        return p

    # ---- 查询 ----
    def is_busy(self, ws: str, name: str) -> bool:
        return (ws, name) in self._jobs

    def _repo_dir(self, ws: str, name: str) -> Path:
        """校验仓库名并解析为 ws 内 repos_dir 的绝对路径（path-traversal 双重防线）。"""
        return _resolve_repo_dir(self._repos_root(ws), name)

    def list_repos(self, ws: str) -> list[dict]:
        root = self._repos_root(ws)
        root.mkdir(parents=True, exist_ok=True)
        out: list[dict] = []
        for sub in sorted(root.iterdir()):
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            # 顶层仓库: 必须有 .git（clone 产物）。仅 .supernova-repo.json 不算——
            # 旧版 migrate_legacy 会给分组目录误写 meta，若以 meta 判会把分组目录
            # 当仓库返回并 continue，跳过第二层子仓库扫描（2026-07-08 /repos 把
            # backend/frontend/20260615 当仓库、65 个真仓库全被吞的 bug）。
            if (sub / ".git").exists():
                try:
                    out.append(self._repo_view(ws, sub.name))
                except ValueError:
                    continue
                continue
            # 非仓库目录 → 可能是分组目录，深入一层找 repos/<group>/<repo>
            for sub2 in sorted(sub.iterdir()):
                if not sub2.is_dir() or sub2.name.startswith("."):
                    continue
                if _is_repo(sub2):
                    try:
                        out.append(self._repo_view(ws, f"{sub.name}/{sub2.name}"))
                    except ValueError:
                        continue
        return out

    def get_repo(self, ws: str, name: str) -> dict | None:
        try:
            d = self._repo_dir(ws, name)
        except ValueError:
            return None
        if not d.is_dir() or not _is_repo(d):
            return None
        view = self._repo_view(ws, name)
        view["recent_events"] = self._recent_events(ws, name, 20)
        return view

    def _repo_view(self, ws: str, name: str) -> dict:
        self._ensure_meta(ws, name)  # 读时自愈：运行期拷入的仓库补 meta
        meta = self._read_meta(ws, name)
        state = meta.get("state", "ready")
        # stale：磁盘 cloning/pulling 但内存无 job → 重启后未完成
        if state in ("cloning", "pulling") and not self.is_busy(ws, name):
            state = "stale"
        group = name.split("/", 1)[0] if "/" in name else None
        view = {"name": name, "group": group, **meta, "state": state}
        # 出站兜底：source.url 剥离 userinfo，防止历史/手动写入的带凭据 URL 泄露给前端
        # （get_repo + list_repos 的共同出口）。
        src = view.get("source")
        if isinstance(src, dict) and src.get("url"):
            view["source"] = {**src, "url": strip_credentials(src["url"])}
        if self.is_busy(ws, name):
            view["progress"] = self._last_progress(ws, name)
        return view

    def _read_meta(self, ws: str, name: str) -> dict:
        f = self._repo_dir(ws, name) / ".supernova-repo.json"
        if not f.exists():
            return {"name": name, "source": {"kind": "unknown"}, "state": "ready"}
        try:
            return json.loads(f.read_text("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return {"name": name, "source": {"kind": "unknown"}, "state": "ready"}

    def _ensure_meta(self, ws: str, name: str) -> None:
        """读时自愈：仓库有 .git 但无 .supernova-repo.json（运行期文件系统拷入、未走 clone
        流程）→ 补写 meta（含 size_bytes）。幂等：meta 已存在或非 git 仓库则跳过；补写失败
        （_migrate_one 内部 try/except）不抛，仍由 _read_meta 兜底返回。

        刷新 /repos 即恢复来源/分支/大小三列数据，无需重启或手动扫描。
        """
        repo = self._repo_dir(ws, name)
        if (repo / ".supernova-repo.json").exists() or not (repo / ".git").exists():
            return
        self._migrate_one(ws, repo, name)

    def _write_meta(self, ws: str, name: str, **patch) -> None:
        f = self._repo_dir(ws, name) / ".supernova-repo.json"
        meta = self._read_meta(ws, name) if f.exists() else {"name": name}
        meta.update(patch)
        f.write_text(json.dumps(meta, ensure_ascii=False))

    def _last_progress(self, ws: str, name: str) -> int | None:
        f = self._repo_dir(ws, name) / "clone.ndjson"
        if not f.exists():
            return None
        last_pct = None
        for line in f.read_text("utf-8", errors="replace").splitlines()[-20:]:
            try:
                d = json.loads(line)
                if "progress" in d:
                    last_pct = d["progress"]
            except json.JSONDecodeError:
                continue
        return last_pct

    def _recent_events(self, ws: str, name: str, n: int) -> list[dict]:
        f = self._repo_dir(ws, name) / "clone.ndjson"
        if not f.exists():
            return []
        out: list[dict] = []
        for line in f.read_text("utf-8", errors="replace").splitlines()[-n:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    # ---- clone / pull ----
    async def clone(self, ws: str, url: str, branch: str | None, commit: str | None,
                    name: str | None, group: str | None = None) -> str:
        if not self._git.available():
            raise PermissionError("未配置 git 凭证（GITLAB_USER/TOKEN）")
        name = name or self._git.repo_name(url)
        # name/group 各为单段目录名；组合成 group/repo 后整体由 _repo_dir 校验 + 兜底
        _validate_repo_segment(name, "仓库名")
        if group:
            _validate_repo_segment(group, "分组名")
            final_name = f"{group}/{name}"
        else:
            final_name = name
        target = self._repo_dir(ws, final_name)
        if target.exists():
            raise ValueError(f"仓库已存在：{final_name}（可改用更新 pull）")
        if len(self._jobs) >= self._max_concurrent:
            raise TooManyClones(self._max_concurrent)
        target.mkdir(parents=True, exist_ok=False)
        self._write_meta(ws, final_name, source={"kind": "git", "url": strip_credentials(url), "branch": branch, "commit": commit},
                         cloned_at=_now_iso(), state="cloning", last_error=None)
        task = asyncio.create_task(self._clone_task(ws, final_name, url, branch, commit, target))
        self._jobs[(ws, final_name)] = task
        return final_name

    async def _clone_task(self, ws: str, name: str, url: str, branch: str | None,
                          commit: str | None, target: Path) -> None:
        try:
            async with self._sem:
                ok = await self._run_git_with_progress(
                    ws, name, phase="cloning",
                    argv=self._build_clone_argv(self._git._inject_auth(url), target, branch))
                # _mark_failed 已写 state=failed；跳过 ready 收尾，保持 failed 状态
                if ok:
                    # commit checkout（可选）
                    if commit:
                        await self._run_git_with_progress(
                            ws, name, phase="cloning",
                            argv=["git", "-C", str(target), "fetch", "--all"])
                        proc = await asyncio.create_subprocess_exec(
                            "git", "-C", str(target), "checkout", commit,
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                        await proc.wait()
                        if proc.returncode != 0:
                            self._mark_failed(ws, name, f"commit {commit} checkout 失败")
                            return
                    head = await self._head_commit(target)
                    self._write_meta(ws, name, state="ready", last_pull_at=_now_iso(),
                                     size_bytes=_dir_size(target),
                                     source={**self._read_meta(ws, name).get("source", {}), "commit": head})
                    await self._append_event(ws, name, {"ts": _now_iso(), "type": "clone_end", "status": "ready"})
        finally:
            self._jobs.pop((ws, name), None)

    async def pull(self, ws: str, name: str) -> None:
        target = self._repo_dir(ws, name)
        if not target.is_dir() or not _is_repo(target):
            raise ValueError(f"仓库不存在：{name}")
        if (ws, name) in self._jobs:
            raise ValueError(f"仓库正忙：{name}")
        self._write_meta(ws, name, state="pulling")
        task = asyncio.create_task(self._pull_task(ws, name, target))
        self._jobs[(ws, name)] = task

    async def _pull_task(self, ws: str, name: str, target: Path) -> None:
        try:
            async with self._sem:
                ok = await self._run_git_with_progress(
                    ws, name, phase="pulling", argv=["git", "-C", str(target), "pull", "--ff-only"])
                if ok:
                    head = await self._head_commit(target)
                    self._write_meta(ws, name, state="ready", last_pull_at=_now_iso(),
                                     size_bytes=_dir_size(target),
                                     source={**self._read_meta(ws, name).get("source", {}), "commit": head})
                    await self._append_event(ws, name, {"ts": _now_iso(), "type": "clone_end", "status": "ready"})
                else:
                    self._mark_failed(ws, name, "pull 失败")
        finally:
            self._jobs.pop((ws, name), None)

    # ---- checkout / delete ----
    async def checkout(self, ws: str, name: str, branch: str) -> None:
        target = self._repo_dir(ws, name)
        if not target.is_dir() or not _is_repo(target):
            raise ValueError(f"仓库不存在：{name}")
        if (ws, name) in self._jobs:
            raise ValueError(f"仓库正忙：{name}")
        # checkout 同步（通常快，不写 ndjson）
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(target), "fetch", "origin", branch,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise ValueError(f"分支不存在：{branch}")
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(target), "checkout", branch,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.wait()
        head = await self._head_commit(target)
        src = self._read_meta(ws, name).get("source", {})
        src["branch"] = branch; src["commit"] = head
        self._write_meta(ws, name, source=src)

    async def delete(self, ws: str, name: str) -> None:
        if (ws, name) in self._jobs:
            raise ValueError(f"仓库正忙：{name}")
        target = self._repo_dir(ws, name)
        # 仅删除真仓库目录（有 .git 或 meta），绝不 rmtree 分组目录（含多个子仓库）
        if target.is_dir() and _is_repo(target):
            shutil.rmtree(target, ignore_errors=False)

    # ---- git 子进程 + stderr 进度解析 ----
    def _build_clone_argv(self, authed_url: str, target: Path, branch: str | None) -> list[str]:
        cmd = ["git", "clone", "--progress"]
        if branch:
            cmd += ["--branch", branch]
        cmd += [authed_url, str(target)]
        return cmd

    async def _run_git_with_progress(self, ws: str, name: str, phase: str, argv: list[str]) -> bool:
        """跑 git，异步读 stderr 解析进度写 ndjson。返回 returncode==0。"""
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        assert proc.stderr is not None

        async def drain_stderr():
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace")
                m = _PROGRESS_RE.search(text)
                if m:
                    await self._append_event(ws, name, {
                        "ts": _now_iso(), "phase": phase, "progress": int(m.group(1)),
                        "status": "progress"})
                else:
                    await self._append_event(ws, name, {
                        "ts": _now_iso(), "phase": phase,
                        "message": self._git.redact(text.rstrip()), "status": "progress"})

        async def drain_stdout():
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break

        await asyncio.gather(drain_stderr(), drain_stdout())
        rc = await proc.wait()
        if rc != 0:
            self._mark_failed(ws, name, f"{phase} 失败（rc={rc}）")
        return rc == 0

    def _mark_failed(self, ws: str, name: str, msg: str) -> None:
        self._write_meta(ws, name, state="failed", last_error=msg, last_pull_at=_now_iso())
        # clone_end 失败事件（同步写，task 内调用）
        f = self._repo_dir(ws, name) / "clone.ndjson"
        with open(f, "a") as fh:
            fh.write(json.dumps({"ts": _now_iso(), "type": "clone_end",
                                 "status": "failed", "error": msg}, ensure_ascii=False) + "\n")

    async def _append_event(self, ws: str, name: str, payload: dict) -> None:
        f = self._repo_dir(ws, name) / "clone.ndjson"
        async with aiofiles.open(f, "a") as fh:
            await fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    async def _head_commit(self, target: Path) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(target), "rev-parse", "HEAD",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _ = await proc.communicate()
            return out.decode("utf-8", "replace").strip() or None
        except Exception:
            return None

    # ---- 旧目录迁移 ----
    def _cleanup_miswritten_group_meta(self, ws: str) -> int:
        """清理被旧版误写到分组目录的 .supernova-repo.json。

        旧版 migrate_legacy 会给顶层每个目录补 meta，导致分组目录（下面有真仓库）也被
        写入 meta，使 _is_repo 误判其为仓库（list_repos 把分组目录当仓库返回、pull 对
        其执行 git pull 失败）。新版不再误写，但已存在的脏 meta 须清理：顶层目录有 meta、
        无 .git、且下面有子仓库（含 .git）-> 判定为分组目录，删 meta。

        单个删除失败（PermissionError 等）不影响整体--lifespan 启动期调用。
        """
        root = self._repos_root(ws)
        if not root.is_dir():
            return 0
        n = 0
        for sub in root.iterdir():
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            meta = sub / ".supernova-repo.json"
            if not meta.exists() or (sub / ".git").exists():
                continue
            has_child_repo = any(
                c.is_dir() and not c.name.startswith(".") and (c / ".git").exists()
                for c in sub.iterdir()
            )
            if has_child_repo:
                try:
                    meta.unlink()
                    n += 1
                except OSError:
                    pass
        return n

    def migrate_legacy(self, ws: str) -> int:
        """把已 clone 但未纳入管理的旧仓库（有 .git 无 .supernova-repo.json）补写 meta。

        扫两层（扁平 repos/<name> + 分组 repos/<group>/<name>），只处理有 .git 的目录，
        跳过无 .git 的分组目录（避免把 frontend/backend 这类分组目录误当仓库纳入）。

        单个仓库迁移失败（PermissionError / 损坏符号链接 / .git/config 不可读 / 非法名等）
        不影响其他仓库与整体启动——lifespan 启动期调用，绝不可因一个坏目录 abort。
        """
        root = self._repos_root(ws)
        if not root.is_dir():
            return 0
        self._cleanup_miswritten_group_meta(ws)
        n = 0
        for sub in root.iterdir():
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            if (sub / ".git").exists():
                n += self._migrate_one(ws, sub, sub.name)
                continue
            # 非仓库目录 → 可能分组目录，深入一层找真仓库
            for sub2 in sub.iterdir():
                if not sub2.is_dir() or sub2.name.startswith("."):
                    continue
                if (sub2 / ".git").exists():
                    n += self._migrate_one(ws, sub2, f"{sub.name}/{sub2.name}")
        return n

    def _migrate_one(self, ws: str, repo: Path, name: str) -> int:
        """单个仓库补写 meta（已纳入管理或失败则返回 0，成功返回 1）。"""
        if (repo / ".supernova-repo.json").exists():
            return 0
        try:
            url, branch = self._infer_from_git(repo)
            url = strip_credentials(url)
            self._write_meta(ws, name,
                source={"kind": "git" if url else "unknown", "url": url, "branch": branch},
                cloned_at=datetime.fromtimestamp(repo.stat().st_mtime, timezone.utc).isoformat(),
                size_bytes=_dir_size(repo),
                state="ready", last_error=None)
        except Exception:
            # 单个坏仓库不应阻断迁移或启动；跳过即可（目录仍在，下次启动可重试）
            return 0
        return 1

    @staticmethod
    def _infer_from_git(repo: Path) -> tuple[str | None, str | None]:
        url, branch = None, None
        cfg = repo / ".git" / "config"
        if cfg.exists():
            for line in cfg.read_text("utf-8", errors="replace").splitlines():
                s = line.strip()
                if s.startswith("url = "):
                    url = s[6:]
        head = repo / ".git" / "HEAD"
        if head.exists():
            t = head.read_text("utf-8", errors="replace").strip()
            if t.startswith("ref: refs/heads/"):
                branch = t[len("ref: refs/heads/"):]
        return url, branch
