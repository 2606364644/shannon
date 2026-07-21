"""非-session 上下文的格式化 print helper。

spec: docs/superpowers/specs/2026-07-08-unified-logging-facade-design.md (组件 2)

给拿不到 audit_session 的地方用（scan_runner 信号 handler、非 activity CLI 进度）：
复用 display.formatters.tag（等宽标签列）+ symbols（状态符号）+ format_log_time（时间戳），
行格式对齐 display 流（file_renderer 的 [timestamp] [TAG] body），不再手拼字面量。

display 流经 AuditSession.log_info/log_step -> renderer；此处绕过 session 走裸 print，
但格式同源（tag/symbols/format_log_time 单一来源），视觉融入扫描日志流。
"""
from __future__ import annotations

from supernova_core.display.formatters import format_log_time, tag


def print_line(tag_label: str, symbol: str, body: str) -> None:
    """打一行 [timestamp] [TAG  ] symbol body，复用 display 格式 helper。

    Args:
        tag_label: 标签列内容（如 "SCAN"/"POC"/"CANCEL"），经 tag() 等宽到 LABEL_WIDTH=5。
        symbol: 状态符号（来自 symbols.py，如 STEP_DONE='✓'）；空串则只打 body。
        body: 正文。
    """
    sym = f"{symbol} " if symbol else ""
    print(f"[{format_log_time()}] [{tag(tag_label)}] {sym}{body}", flush=True)
