"""Per-scan 本地代理：proxy.py 子进程 + 自定义 DNS 插件。

每个黑盒扫描起一个独立 proxy.py 子进程（bind 127.0.0.1:<OS 分配端口>），
持该扫描的域名→IP 映射（经 env ``HOST_MAP_JSON`` 注入），让黑盒所有 HTTP
出口统一走该代理，per-scan 端口级隔离。实测（2026-08-12，见
``scripts/validate_host_proxy_probe/``）``resolve_dns`` 对 HTTP 请求与
HTTPS CONNECT 隧道均生效；agent-browser / playwright-cli 经代理落点正确。

设计要点：
- 映射表经 env ``HOST_MAP_JSON``（JSON dict）注入，每个 proxy 子进程独立 env →
  per-scan 隔离。插件 **stateless + per-request**——proxy.py 多 worker 进程
  不共享 Python 全局变量，故映射必须走 env（不能是模块级变量）。
- 必需 flag ``--num-workers 1 --num-acceptors 1 --local-executor 1``：否则
  proxy.py 按 CPU 核数 fork N×2 进程，per-scan 进程爆炸。
- 探活失败 raise ``PentestError(category="preflight", error_code=PROXY_UNREACHABLE)``
  → 扫描 fail-fast（preflight 是不可重试的配置/环境类错误）。
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from proxy.common.types import HostPort
from proxy.http.proxy import HttpProxyBasePlugin

from supernova_core.models.errors import ErrorCode, PentestError

# 必需 flag：否则 proxy.py 按 CPU fork N×2 进程，per-scan 进程爆炸
_REQUIRED_FLAGS = ["--num-workers", "1", "--num-acceptors", "1", "--local-executor", "1"]
# 插件完整模块路径（supernova-core editable-install → 子进程可 import 到）
_PLUGIN_REF = "supernova_core.services.host_proxy.HostResolverPlugin"
# port-file 出现后写端口的等待上限（每轮 0.5s）
_PORT_FILE_TIMEOUT_ITER = 40


class HostResolverPlugin(HttpProxyBasePlugin):
    """按域名查映射表返回指定 IP；未命中走 proxy.py 默认 DNS。

    映射表经 env ``HOST_MAP_JSON`` 注入（每个 proxy 子进程独立 env → per-scan
    隔离）。插件 stateless + per-request 实例，**不能用全局变量**（proxy.py
    多 worker 进程不共享）。命中返回 ``(映射 IP, None)``；未命中返回
    ``(None, None)`` 走 proxy.py 默认 DNS。
    """

    def resolve_dns(
        self, host: str, port: int
    ) -> tuple[str | None, HostPort | None]:
        try:
            mapping = json.loads(os.environ.get("HOST_MAP_JSON", "{}"))
        except (json.JSONDecodeError, TypeError, ValueError):
            mapping = {}
        return (mapping.get(host), None)


@dataclass
class ProxyHandle:
    """一个 per-scan proxy 子进程的句柄。"""

    proxy_url: str
    process: asyncio.subprocess.Process
    port: int
    port_file: str


async def start_host_proxy(mappings: dict[str, str]) -> ProxyHandle:
    """起 proxy.py 子进程，bind ``127.0.0.1:<OS 分配端口>``，加载映射。

    探活失败 / 子进程早死 / port-file 超时均 raise
    ``PentestError(category="preflight", error_code=PROXY_UNREACHABLE)`` →
    扫描 fail-fast。
    """
    # proxy.py 要求 port-file 不存在；NamedTemporaryFile 拿唯一路径后立刻删
    port_file = tempfile.NamedTemporaryFile(
        suffix=".port", delete=False, prefix="host_proxy_"
    ).name
    os.unlink(port_file)

    env = {
        **os.environ,
        "HOST_MAP_JSON": json.dumps(mappings),
    }
    # supernova-core 是 editable-install，子进程（继承本 env）能直接 import 到
    # supernova_core.services.host_proxy；PYTHONPATH 仅作防御性兜底。
    core_src = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = core_src + os.pathsep + os.environ.get("PYTHONPATH", "")

    cmd = [
        "proxy",
        "--plugins", _PLUGIN_REF,
        "--hostname", "127.0.0.1",
        "--port", "0",
        "--port-file", port_file,
        "--log-level", "WARNING",
        *_REQUIRED_FLAGS,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # 等 port-file 写出（proxy.py bind 完端口后写文件）
    for _ in range(_PORT_FILE_TIMEOUT_ITER):
        if Path(port_file).exists() and Path(port_file).stat().st_size > 0:
            break
        if proc.returncode is not None:  # 子进程已死
            stderr = await proc.stderr.read() if proc.stderr else b""
            await _safe_cleanup_proc(proc)
            Path(port_file).unlink(missing_ok=True)
            raise PentestError(
                f"host proxy exited prematurely: {stderr.decode(errors='replace')[:500]}",
                category="preflight",
                error_code=ErrorCode.PROXY_UNREACHABLE,
            )
        await asyncio.sleep(0.5)
    else:
        await _safe_cleanup_proc(proc)
        Path(port_file).unlink(missing_ok=True)
        raise PentestError(
            "host proxy port-file timeout (proxy.py did not bind in time)",
            category="preflight",
            error_code=ErrorCode.PROXY_UNREACHABLE,
        )

    try:
        port = int(Path(port_file).read_text().strip())
    except (ValueError, OSError) as exc:
        await _safe_cleanup_proc(proc)
        Path(port_file).unlink(missing_ok=True)
        raise PentestError(
            f"host proxy port-file unreadable: {exc}",
            category="preflight",
            error_code=ErrorCode.PROXY_UNREACHABLE,
        ) from exc

    handle = ProxyHandle(f"http://127.0.0.1:{port}", proc, port, port_file)

    if not await _probe(handle):
        await stop_host_proxy(handle)
        raise PentestError(
            f"host proxy probe failed on {handle.proxy_url}",
            category="preflight",
            error_code=ErrorCode.PROXY_UNREACHABLE,
        )
    return handle


async def _probe(handle: ProxyHandle) -> bool:
    """探活：代理端口 TCP 可连即视为存活（proxy.py accept 成立）。

    不发完整 HTTP——代理 await 目标 CONNECT/GET，端口 accept 即足以证明
    proxy.py 监听就位（避免单测里再起一个目标 server）。
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", handle.port), timeout=3
        )
    except (OSError, asyncio.TimeoutError):
        return False
    try:
        writer.close()
        await writer.wait_closed()
    except OSError:
        pass
    return True


async def stop_host_proxy(handle: ProxyHandle) -> None:
    """SIGTERM→wait→SIGKILL 升级，best-effort（**绝不 raise**）。

    删 port-file。供 ``start_host_proxy`` 探活失败回滚与扫描结束清理共用。
    """
    await _safe_cleanup_proc(handle.process)
    try:
        Path(handle.port_file).unlink(missing_ok=True)
    except OSError:
        pass


async def _safe_cleanup_proc(proc: asyncio.subprocess.Process) -> None:
    """SIGTERM→wait(5s)→SIGKILL→wait，所有异常吞掉（best-effort）。"""
    try:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
    except Exception:
        pass
