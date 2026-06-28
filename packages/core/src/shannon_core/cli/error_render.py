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
