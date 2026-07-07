from __future__ import annotations

import asyncio
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles

from .git_fetcher import GitFetcher

_PROGRESS_RE = re.compile(r"(?:Receiving objects|Resolving deltas|Compressing objects|Counting objects):\s+(\d+)%")


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
    def __init__(self, repos_dir: Path, git_fetcher: GitFetcher,
                 max_concurrent: int = 3) -> None:
        self._dir = Path(repos_dir)
        self._git = git_fetcher
        self._max_concurrent = max(1, max_concurrent)
        self._sem = asyncio.Semaphore(self._max_concurrent)
        self._jobs: dict[str, asyncio.Task] = {}

    # ---- 查询 ----
    def is_busy(self, name: str) -> bool:
        return name in self._jobs

    def list_repos(self) -> list[dict]:
        self._dir.mkdir(parents=True, exist_ok=True)
        out: list[dict] = []
        for sub in sorted(self._dir.iterdir()):
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            out.append(self._repo_view(sub.name))
        return out

    def get_repo(self, name: str) -> dict | None:
        d = self._dir / name
        if not d.is_dir():
            return None
        view = self._repo_view(name)
        view["recent_events"] = self._recent_events(name, 20)
        return view

    def _repo_view(self, name: str) -> dict:
        d = self._dir / name
        meta = self._read_meta(name)
        state = meta.get("state", "ready")
        # stale：磁盘 cloning/pulling 但内存无 job → 重启后未完成
        if state in ("cloning", "pulling") and not self.is_busy(name):
            state = "stale"
        view = {"name": name, **meta, "state": state}
        if self.is_busy(name):
            view["progress"] = self._last_progress(name)
        return view

    def _read_meta(self, name: str) -> dict:
        f = self._dir / name / ".shannon-repo.json"
        if not f.exists():
            return {"name": name, "source": {"kind": "unknown"}, "state": "ready"}
        try:
            return json.loads(f.read_text("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return {"name": name, "source": {"kind": "unknown"}, "state": "ready"}

    def _write_meta(self, name: str, **patch) -> None:
        f = self._dir / name / ".shannon-repo.json"
        meta = self._read_meta(name) if f.exists() else {"name": name}
        meta.update(patch)
        f.write_text(json.dumps(meta, ensure_ascii=False))

    def _last_progress(self, name: str) -> int | None:
        f = self._dir / name / "clone.ndjson"
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

    def _recent_events(self, name: str, n: int) -> list[dict]:
        f = self._dir / name / "clone.ndjson"
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
    async def clone(self, url: str, branch: str | None, commit: str | None,
                    name: str | None) -> str:
        if not self._git.available():
            raise PermissionError("未配置 git 凭证（GITLAB_USER/TOKEN）")
        name = name or self._git.repo_name(url)
        target = self._dir / name
        if target.exists():
            raise ValueError(f"仓库已存在：{name}（可改用更新 pull）")
        if len(self._jobs) >= self._max_concurrent:
            raise TooManyClones(self._max_concurrent)
        target.mkdir(parents=True, exist_ok=False)
        self._write_meta(name, source={"kind": "git", "url": url, "branch": branch, "commit": commit},
                         cloned_at=_now_iso(), state="cloning", last_error=None)
        task = asyncio.create_task(self._clone_task(name, url, branch, commit, target))
        self._jobs[name] = task
        return name

    async def _clone_task(self, name: str, url: str, branch: str | None,
                          commit: str | None, target: Path) -> None:
        async with self._sem:
            ok = await self._run_git_with_progress(
                name, phase="cloning",
                argv=self._build_clone_argv(self._git._inject_auth(url), target, branch))
            if not ok:
                # _mark_failed 已写 state=failed；跳过 ready 收尾，保持 failed 状态
                pass
            else:
                # commit checkout（可选）
                if commit:
                    await self._run_git_with_progress(
                        name, phase="cloning",
                        argv=["git", "-C", str(target), "fetch", "--all"])
                    proc = await asyncio.create_subprocess_exec(
                        "git", "-C", str(target), "checkout", commit,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    await proc.wait()
                head = await self._head_commit(target)
                self._write_meta(name, state="ready", last_pull_at=_now_iso(),
                                 size_bytes=_dir_size(target),
                                 source={**self._read_meta(name).get("source", {}), "commit": head})
                await self._append_event(name, {"ts": _now_iso(), "type": "clone_end", "status": "ready"})
        self._jobs.pop(name, None)

    async def pull(self, name: str) -> None:
        target = self._dir / name
        if not target.is_dir():
            raise ValueError(f"仓库不存在：{name}")
        if name in self._jobs:
            raise ValueError(f"仓库正忙：{name}")
        self._write_meta(name, state="pulling")
        task = asyncio.create_task(self._pull_task(name, target))
        self._jobs[name] = task

    async def _pull_task(self, name: str, target: Path) -> None:
        async with self._sem:
            ok = await self._run_git_with_progress(
                name, phase="pulling", argv=["git", "-C", str(target), "pull", "--ff-only"])
            if ok:
                head = await self._head_commit(target)
                self._write_meta(name, state="ready", last_pull_at=_now_iso(),
                                 size_bytes=_dir_size(target),
                                 source={**self._read_meta(name).get("source", {}), "commit": head})
                await self._append_event(name, {"ts": _now_iso(), "type": "clone_end", "status": "ready"})
            else:
                self._mark_failed(name, "pull 失败")
        self._jobs.pop(name, None)

    # ---- checkout / delete ----
    async def checkout(self, name: str, branch: str) -> None:
        target = self._dir / name
        if not target.is_dir():
            raise ValueError(f"仓库不存在：{name}")
        if name in self._jobs:
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
        src = self._read_meta(name).get("source", {})
        src["branch"] = branch; src["commit"] = head
        self._write_meta(name, source=src)

    async def delete(self, name: str) -> None:
        if name in self._jobs:
            raise ValueError(f"仓库正忙：{name}")
        target = self._dir / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=False)

    # ---- git 子进程 + stderr 进度解析 ----
    def _build_clone_argv(self, authed_url: str, target: Path, branch: str | None) -> list[str]:
        cmd = ["git", "clone", "--progress"]
        if branch:
            cmd += ["--branch", branch]
        cmd += [authed_url, str(target)]
        return cmd

    async def _run_git_with_progress(self, name: str, phase: str, argv: list[str]) -> bool:
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
                    await self._append_event(name, {
                        "ts": _now_iso(), "phase": phase, "progress": int(m.group(1)),
                        "status": "progress"})
                else:
                    await self._append_event(name, {
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
            self._mark_failed(name, f"{phase} 失败（rc={rc}）")
        return rc == 0

    def _mark_failed(self, name: str, msg: str) -> None:
        self._write_meta(name, state="failed", last_error=msg, last_pull_at=_now_iso())
        # clone_end 失败事件（同步写，task 内调用）
        f = self._dir / name / "clone.ndjson"
        with open(f, "a") as fh:
            fh.write(json.dumps({"ts": _now_iso(), "type": "clone_end",
                                 "status": "failed", "error": msg}, ensure_ascii=False) + "\n")

    async def _append_event(self, name: str, payload: dict) -> None:
        f = self._dir / name / "clone.ndjson"
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
    def migrate_legacy(self) -> int:
        """把无 .shannon-repo.json 的旧 repos/<name> 纳入管理。返回迁移数。"""
        if not self._dir.is_dir():
            return 0
        n = 0
        for sub in self._dir.iterdir():
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            meta = sub / ".shannon-repo.json"
            if meta.exists():
                continue
            url, branch = self._infer_from_git(sub)
            import os
            self._write_meta(sub.name,
                source={"kind": "git" if url else "unknown", "url": url, "branch": branch},
                cloned_at=datetime.fromtimestamp(sub.stat().st_mtime, timezone.utc).isoformat(),
                state="ready", last_error=None)
            n += 1
        return n

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
