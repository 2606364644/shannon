"""CLI workflow 失败友好展示共享层。

黑白盒 CLI 的 start 命令在 run_scan 抛异常时调用本模块：从层层包装的
temporalio 异常（WorkflowFailureError → ActivityError → ApplicationError）里
挖出根因（error_type + message），映射成人话诊断 + 建议；完整 traceback 落
activity_failures.log。worker / activity / retry policy 都不感知本模块。

设计见 docs/superpowers/specs/2026-06-28-cli-workflow-failure-friendly-display-design.md。
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from shannon_core.models.errors import classify_error_for_temporal


@dataclass
class RootCause:
    error_type: str
    message: str


def _walk_cause_chain(exc: Exception) -> list[Exception]:
    """沿 temporalio ``.cause`` 属性 + Python ``__cause__`` 链收集异常（从外到内）。

    temporalio 异常用 ``.cause`` 属性链接（ActivityError.cause → ApplicationError），
    activity 内 ``raise ApplicationFailure(...) from e`` 另设 ``__cause__``；两路都走。
    """
    chain: list[Exception] = []
    seen: set[int] = set()
    cur: Exception | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        chain.append(cur)
        nxt = getattr(cur, "cause", None) or cur.__cause__
        cur = nxt if isinstance(nxt, Exception) else None
    return chain


def extract_root_cause(exc: Exception) -> RootCause:
    """挖到最深层异常：优先取链上带 ``.type`` 的 temporalio 异常的 type，否则 classify 兜底。"""
    chain = _walk_cause_chain(exc)
    deepest = chain[-1]

    error_type: str | None = None
    for err in chain:  # 从外到内，后写者更深、覆盖前者
        t = getattr(err, "type", None)
        if t:
            error_type = t
    if not error_type:
        error_type = classify_error_for_temporal(deepest)[0]

    message = str(deepest) or str(exc)
    return RootCause(error_type=error_type, message=message)


def _invalid_target_hint(message: str) -> str:
    """InvalidTargetError 按 message 子串区分 loopback / SSRF / 不可解析三支。"""
    msg = message.lower()
    if "loopback" in msg:
        return (
            "目标解析到本机 loopback 地址。黑盒扫描不允许扫 loopback/内网地址（SSRF 防护）。\n"
            "  建议：用公网地址，或目标容器在宿主网络可达的地址。"
        )
    if "ssrf" in msg or "169.254" in msg:
        return "目标解析到 SSRF 敏感网段（169.254.x.x）。\n  建议：换非链路本地地址。"
    if "cannot resolve" in msg or "resolve" in msg:
        return "无法解析目标域名。\n  建议：检查 URL 拼写 / DNS / 目标是否启动。"
    return f"目标地址无效：{message}\n  建议：检查目标 URL。"


# error_type → 人话诊断 + 建议。callable 接收原始 message（用于按子串细分）。
FRIENDLY_HINTS: dict[str, str | Callable[[str], str]] = {
    "InvalidTargetError": _invalid_target_hint,
    "ConfigurationError": "配置或必要文件有问题。\n  建议：检查 profile / config 文件。",
    "AuthenticationError": "鉴权失败。\n  建议：检查 API key / profile 配置。",
    "AuthLoginFailedError": "目标登录失败。\n  建议：检查登录流程配置 / 凭据。",
    "GitError": "Git 操作失败。\n  建议：检查仓库路径 / git 可用性。",
    "PermissionError": "权限不足。\n  建议：检查访问权限 / token。",
}


def format_workflow_failure(exc: Exception) -> str:
    """组装多行友好串。落盘 / --debug 提示由 CLI 层补充（保持本函数纯）。"""
    rc = extract_root_cause(exc)
    hint = FRIENDLY_HINTS.get(rc.error_type)
    if callable(hint):
        detail = hint(rc.message)
    elif isinstance(hint, str):
        detail = hint
    else:
        detail = f"扫描因 {rc.error_type} 失败：{rc.message}"
    return f"✗ 扫描失败：{rc.error_type}\n  {detail}"
