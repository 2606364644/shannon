"""端到端：两扫描各持不同映射、同域名 ``target.test`` 不同 IP，互不影响。

对齐 ``scripts/validate_host_proxy_probe/`` 的实测断言（serverA/B hits 互不串）。
本单测锁缩窄不变量：两个 proxy 子进程拿不同端口 / proxy_url / process（per-scan
端口级隔离基石）。真正的「两扫描并发同域名落不同 IP」完整端到端（起目标 server +
跑完整 workflow）超出单测范围，由 probe 脚本覆盖（2026-08-12 全通过，见该目录
``README.md``）。
"""
import pytest

# 必须在 import supernova_core.services.host_proxy 之前：该模块 import 时即执行
# ``from proxy.http.proxy import HttpProxyBasePlugin``，proxy.py 未装则 ImportError
# → 整文件跳过（real-machine 隔离仍由 probe 脚本保证）。
pytest.importorskip("proxy")

from supernova_core.services.host_proxy import (  # noqa: E402
    start_host_proxy,
    stop_host_proxy,
)


@pytest.mark.asyncio
async def test_two_proxies_same_host_different_ip_isolated():
    """两个独立 proxy：同 host 不同 IP，端口 / proxy_url / process 各异。

    per-scan 隔离基石：每个扫描起独立 proxy.py 子进程（``--port 0`` OS 分配），
    独立端口 + 独立映射 env（``HOST_MAP_JSON``）。try/finally 保证断言失败时
    两个子进程都被回收（``stop_host_proxy`` best-effort、绝不 raise）。
    """
    hA = await start_host_proxy({"target.test": "10.0.0.1"})
    try:
        hB = await start_host_proxy({"target.test": "10.0.0.2"})
        try:
            assert hA.port != hB.port
            assert hA.proxy_url != hB.proxy_url
            # 各自 env 独立 → 各自 process 独立（per-scan 隔离基石）
            assert hA.process is not hB.process
        finally:
            await stop_host_proxy(hB)
    finally:
        await stop_host_proxy(hA)
