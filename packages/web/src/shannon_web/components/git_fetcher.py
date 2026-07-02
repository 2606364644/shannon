from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

_TOKEN_RE = re.compile(r"https?://[^/]+:[^@]+@")


class GitFetcher:
    def __init__(self, repos_dir: Path, gitlab_user: str | None, gitlab_token: str | None) -> None:
        self._dir = Path(repos_dir)
        self._user = gitlab_user
        self._token = gitlab_token

    def available(self) -> bool:
        return bool(self._user and self._token)

    @staticmethod
    def repo_name(url: str) -> str:
        last = url.rstrip("/").split("/")[-1]
        return last[:-4] if last.endswith(".git") else last

    @staticmethod
    def redact(text: str) -> str:
        return _TOKEN_RE.sub("https://***:***@", text)

    def _inject_auth(self, url: str) -> str:
        return url.replace("https://", f"https://{self._user}:{self._token}@", 1)

    async def _run(self, args: list[str], cwd: str | Path | None = None) -> tuple[int, str, str]:
        self._dir.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    async def fetch(self, url: str, branch: str | None = None,
                    commit: str | None = None, force_reclone: bool = False) -> Path:
        if not self.available():
            raise PermissionError("GitLab credentials missing")
        name = self.repo_name(url)
        target = self._dir / name
        authed = self._inject_auth(url)

        if target.exists() and not force_reclone:
            rc, _, _ = await self._run(["git", "pull", "--ff-only"], cwd=target)
            if rc != 0:
                shutil.rmtree(target, ignore_errors=True)  # fallback 重 clone
        if force_reclone and target.exists():
            shutil.rmtree(target, ignore_errors=True)

        if not target.exists():
            cmd = ["git", "clone"]
            if branch and not commit:
                cmd += ["--branch", branch]
            cmd += [authed, str(target)]
            rc, _, err = await self._run(cmd)
            if rc != 0:
                raise RuntimeError(f"clone failed: {self.redact(err)}")

        if commit:
            await self._run(["git", "fetch", "--all"], cwd=target)
            rc, _, err = await self._run(["git", "checkout", commit], cwd=target)
            if rc != 0:
                raise RuntimeError(f"checkout failed: {self.redact(err)}")
        return target
