"""proxy.py 自定义 DNS 插件：按 HOST_MAP_JSON env 做 域名→IP 映射。

每个 proxy.py 子进程持有自己的 env（per-scan 隔离）。命中返回映射 IP，
未命中返回 (None, None) 走 proxy.py 默认 DNS。对 HTTP 请求与 HTTPS CONNECT 均生效。
"""
import os
import json
from typing import Optional, Tuple

from proxy.http.proxy import HttpProxyBasePlugin
from proxy.common.types import HostPort


class HostResolverPlugin(HttpProxyBasePlugin):
    def resolve_dns(
        self, host: str, port: int
    ) -> Tuple[Optional[str], Optional[HostPort]]:
        try:
            mapping = json.loads(os.environ.get("HOST_MAP_JSON", "{}"))
        except Exception:
            mapping = {}
        if host in mapping:
            return mapping[host], None
        return None, None
