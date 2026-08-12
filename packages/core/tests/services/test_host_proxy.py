"""Per-scan host proxy (proxy.py + HostResolverPlugin) tests.

Validates:
- Plugin maps host->IP via HOST_MAP_JSON env; unmapped hosts fall through.
- start/stop lifecycle: process spawns, port assigned, SIGTERM cleanup, port-file removed.
- Per-scan env isolation: two proxies with different mappings get different ports.
"""
import json
from pathlib import Path

import pytest

pytest.importorskip("proxy")  # lifecycle 测试需 proxy.py 装好；无则整文件跳过

from supernova_core.services.host_proxy import (  # noqa: E402
    HostResolverPlugin,
    start_host_proxy,
    stop_host_proxy,
)


def test_plugin_resolve_dns_hit(monkeypatch):
    """插件命中映射返回 IP；未命中返回 (None,None) 走默认 DNS。"""
    monkeypatch.setenv("HOST_MAP_JSON", json.dumps({"target.test": "127.0.0.1"}))
    p = HostResolverPlugin.__new__(HostResolverPlugin)  # 不走 __init__（需 socket）
    assert p.resolve_dns("target.test", 80) == ("127.0.0.1", None)
    assert p.resolve_dns("unmapped.test", 80) == (None, None)


@pytest.mark.asyncio
async def test_start_stop_proxy_lifecycle(monkeypatch, tmp_path):
    """起代理→拿 proxy_url→停（进程退出、port-file 删）。"""
    mappings = {"target.test": "10.0.0.1"}
    handle = await start_host_proxy(mappings)
    assert handle.proxy_url.startswith("http://127.0.0.1:")
    assert handle.port > 0
    # 子进程存活
    assert handle.process.returncode is None
    await stop_host_proxy(handle)
    # 进程已终止
    assert handle.process.returncode is not None
    assert not Path(handle.port_file).exists()


@pytest.mark.asyncio
async def test_start_proxy_env_isolation(monkeypatch):
    """两个 proxy 各持独立映射 env（per-scan 隔离基石）。"""
    hA = await start_host_proxy({"target.test": "10.0.0.1"})
    hB = await start_host_proxy({"target.test": "10.0.0.2"})
    assert hA.port != hB.port
    await stop_host_proxy(hA)
    await stop_host_proxy(hB)
