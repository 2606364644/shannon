from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles

from shannon_web.models import ScanRequest


class TemporalUnavailable(Exception):
    pass


class TooManyScans(Exception):
    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"已有扫描在跑（并发上限 {limit}）")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScanManager:
    def __init__(self, workspaces_dir: Path, repos_dir: Path, config_store: Any,
                 max_concurrent: int = 1, scan_timeout: float = 0.0) -> None:
        self._workspaces_dir = Path(workspaces_dir)
        self._repos_dir = Path(repos_dir)
        self._config_store = config_store
        self._max_concurrent = max(1, max_concurrent)
        self._scan_timeout = scan_timeout
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        # 进行中的 scan 请求快照（ws -> ScanRequest），供 active_repo_sources() 判引用
        self._active_reqs: dict[str, ScanRequest] = {}

    # ---- 公共 API ----
    def active_pids(self) -> dict[str, int]:
        return {ws: p.pid for ws, p in self._procs.items() if p.returncode is None}

    def active_repo_sources(self) -> set[str]:
        """当前正在跑的 scan 引用的 repo 名集合（DELETE /repos 判引用用）。"""
        out: set[str] = set()
        for req in self._active_reqs.values():
            if req.source is not None and req.source.kind == "repo":
                out.add(req.source.value)
        return out

    async def reap_zombies(self) -> None:
        """lifespan 启动时扫无主子进程（本会话 _procs 外的）。v1: 无操作占位，
        真正无主 ws 由 WorkspacesIndexer 判 interrupted 呈现。"""
        return None

    async def start(self, req: ScanRequest) -> str:
        await self._check_temporal()
        if len(self._procs) >= self._max_concurrent:
            raise TooManyScans(self._max_concurrent)

        if req.type == "correlation":
            # correlation 子进程（shannon-multi start -c）忽略 ws 参数，orchestrator
            # 把 correlation_progress/scan_end 写到
            # resolve_workspaces_dir()/config.correlation.out_workspace/events.ndjson。
            # 故 event_file 必须用 yaml 里的 out_workspace，否则 SSE 收不到联动进度
            # 且 _has_scan_end 查错文件（final-review Finding 1）。
            target, yaml_path = await self._resolve_inputs(req)
            ws = self._resolve_out_workspace(yaml_path)
        else:
            target, yaml_path = await self._resolve_inputs(req)
            ws = req.workspace or self._gen_ws_name(req)

        ws_dir = self._workspaces_dir / ws
        ws_dir.mkdir(parents=True, exist_ok=True)
        event_file = ws_dir / "events.ndjson"

        argv = self._build_argv(req, target, ws, yaml_path)
        env = {**os.environ, "SHANNON_WEB_EVENT_FILE": str(event_file)}
        # 在子进程拉起前登记，确保 active_repo_sources() 能看到在途请求（即便
        # proc 尚未赋值回 _procs）。
        self._active_reqs[ws] = req
        proc = await asyncio.create_subprocess_exec(
            *argv, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        self._procs[ws] = proc
        self._tasks[ws] = asyncio.create_task(self._watch(ws, proc, event_file))
        return ws

    async def cancel(self, ws: str) -> bool:
        proc = self._procs.get(ws)
        if proc is None:
            return False
        try:
            proc.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass
        return True

    # ---- 钩子（可 monkeypatch）----
    def _build_argv(self, req: ScanRequest, target: str | None,
                    ws: str, yaml_path: Path | None = None) -> list[str]:
        if req.type == "whitebox":
            return ["shannon-whitebox", "start", "-r", target or "", "--url", req.url or "", "-w", ws]
        if req.type == "blackbox":
            cmd = ["shannon-blackbox", "start", "--url", req.url or "", "--repo", target or "", "-w", ws]
            if req.reuse_latest:
                cmd.append("--latest")
            return cmd
        if req.type == "correlation":
            return ["shannon-multi", "start", "-c", str(yaml_path)]
        raise ValueError(f"unknown scan type: {req.type}")

    async def _check_temporal(self) -> None:
        import socket

        host = os.environ.get("SHANNON_TEMPORAL_HOST", "localhost")
        port = int(os.environ.get("SHANNON_TEMPORAL_PORT", "7233"))

        def _probe() -> bool:
            try:
                with socket.create_connection((host, port), timeout=1.0):
                    return True
            except OSError:
                return False

        loop = asyncio.get_running_loop()
        if not await loop.run_in_executor(None, _probe):
            raise TemporalUnavailable()

    # ---- 内部 ----
    async def _resolve_inputs(self, req: ScanRequest) -> tuple[str | None, Path | None]:
        target: str | None = None
        yaml_path: Path | None = None
        if req.source is not None:
            if req.source.kind == "repo":
                target = self._resolve_repo_path(req.source.value)
            else:  # path
                target = req.source.value
        if req.type == "correlation":
            yaml_path = await self._resolve_correlation_yaml(req)
        return target, yaml_path

    def _resolve_repo_path(self, name: str) -> str:
        """将 repo 名解析为 repos_dir/<name>，并校验 state==ready。

        - 目录不存在 → ValueError（前端 4xx 语义）
        - 元数据缺失或 JSON 损坏 → 视为 ready，不阻塞扫描
        - state 非 ready（cloning/error 等）→ ValueError，提示去 /repos 完成 clone
        """
        repo_dir = self._repos_dir / name
        if not repo_dir.is_dir():
            raise ValueError(f"仓库不存在：{name}")
        meta_file = repo_dir / ".shannon-repo.json"
        state = "ready"
        if meta_file.exists():
            try:
                state = json.loads(meta_file.read_text("utf-8", errors="replace")).get("state", "ready")
            except json.JSONDecodeError:
                state = "ready"  # 元数据损坏不阻塞扫描
        if state != "ready":
            raise ValueError(f"仓库未就绪（state={state}），请先在 /repos 完成 clone")
        return str(repo_dir)

    def _resolve_out_workspace(self, yaml_path: Path | None) -> str:
        """从 correlation yaml 解析 out_workspace，作为 event_file 所在 ws。

        orchestrator 写 correlation_progress/scan_end 到
        resolve_workspaces_dir()/out_workspace/events.ndjson；ScanManager 的
        event_file 必须与之同 ws，否则 SSE 收不到联动进度（final-review Finding 1）。
        """
        from shannon_core.config.parser import parse_multi_repo_config
        if yaml_path is None or not Path(yaml_path).exists():
            raise ValueError("correlation 扫描需可解析的 yaml 以取 out_workspace")
        cfg = parse_multi_repo_config(yaml_path)
        return cfg.correlation.out_workspace

    async def _resolve_correlation_yaml(self, req: ScanRequest) -> Path:
        assert self._config_store is not None, "correlation 需 config_store"
        if req.config_name:
            # 走 store 的校验路径（复用 _path 的遍历校验：禁止 "/" / ".." / 空），
            # 不能直接拼 web-multi-{name}.yaml 否则 config_name="../evil" 路径遍历
            # 绕过 store 校验（final-review Finding 3）。
            return self._config_store.path_for(req.config_name)
        if req.config_content:
            if req.save_as:
                return self._config_store.write(req.save_as, req.config_content)
            return self._config_store.write_temp(req.config_content)
        raise ValueError("correlation 扫描需 config_name 或 config_content")

    def _gen_ws_name(self, req: ScanRequest) -> str:
        base = "scan"
        if req.source:
            base = Path(req.source.value).stem or "scan"
        elif req.config_name:
            base = req.config_name
        return f"{base}_{int(time.time())}"

    async def _watch(self, ws: str, proc: asyncio.subprocess.Process, event_file: Path) -> None:
        stderr_tail = bytearray()

        def stderr_sink(line: bytes) -> None:
            stderr_tail.extend(line)
            if len(stderr_tail) > 2048:
                del stderr_tail[:len(stderr_tail) - 2048]

        async def drain(stream, sink=None):
            while True:
                line = await stream.readline()
                if not line:
                    break
                if sink is not None:
                    sink(line)

        s_out = asyncio.create_task(drain(proc.stdout))
        s_err = asyncio.create_task(drain(proc.stderr, stderr_sink))
        try:
            if self._scan_timeout > 0:
                try:
                    rc = await asyncio.wait_for(proc.wait(), self._scan_timeout)
                except asyncio.TimeoutError:
                    proc.send_signal(signal.SIGINT)
                    rc = await proc.wait()
            else:
                rc = await proc.wait()
        finally:
            await asyncio.gather(s_out, s_err, return_exceptions=True)

        if not self._has_scan_end(event_file):
            status = "killed" if (rc is not None and rc < 0) else "crashed"
            tail_text = bytes(stderr_tail[-2048:]).decode("utf-8", "replace")
            await self._write_scan_end(event_file, status, rc if rc is not None else -1, tail_text)
        self._procs.pop(ws, None)
        self._tasks.pop(ws, None)
        self._active_reqs.pop(ws, None)

    @staticmethod
    def _has_scan_end(event_file: Path) -> bool:
        if not event_file.exists():
            return False
        for line in event_file.read_text("utf-8", errors="replace").splitlines()[-5:]:
            try:
                if json.loads(line).get("type") == "scan_end":
                    return True
            except json.JSONDecodeError:
                continue
        return False

    async def _write_scan_end(self, event_file: Path, status: str,
                              returncode: int, stderr_tail: str) -> None:
        payload = {
            "ts": _now_iso(), "category": "CONTROL", "type": "scan_end",
            "status": status, "returncode": returncode, "stderr_tail": stderr_tail,
        }
        async with aiofiles.open(event_file, "a") as fh:
            await fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
