"""统一日志入口：root logger 挂 LogBusHandler，散落 getLogger 自动汇入日志总线。

spec: docs/superpowers/specs/2026-07-08-unified-logging-facade-design.md (组件 1)
plan: docs/superpowers/plans/2026-07-08-unified-log-bus.md

改挂 LogBusHandler（替代 StreamHandler(stderr)+FileHandler）——散落 getLogger 的
record 经 LogBusHandler.emit 分流（session 活跃→queue/event-loop drain；无 session
→diagnostic.log fallback），不再直写 stderr，根除 Rich Live footer 鬼影。

- 散落 ~20 处 getLogger 调用点零改动（propagate 走 root 自动进 LogBusHandler）；
- diagnostic.log 由 LogBus 单例的 DiagnosticLog 管（非 logging.FileHandler）；
- temporalio.activity + temporalio.worker 子树 logger 由 temporalio_redirect 独立管
  (propagate=False), 此处跳过它们(不 addHandler、不改 propagate), 避免双重输出;
- _FORMAT/_DATEFMT 常量保留供 format_diagnostic_line 对齐（不再挂 Formatter）。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .log_bus import LogBus, LogBusHandler

_DIAGNOSTIC_FILENAME = "diagnostic.log"
# 对齐 display.formatters.LABEL_WIDTH=5：[%(levelname)5s] 右对齐 5 列。
# format_diagnostic_line 用 {level:>5} 等价实现（handler/renderer 不再挂 Formatter）。
_FORMAT = "[%(asctime)s] [%(levelname)5s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"  # 对齐 display.formatters.format_log_time

_NOISE_LOGGERS = ("httpx", "urllib3", "httpcore", "asyncio", "claude_agent_sdk")

_configured_log_dir: Path | None = None


def configure_logging(log_dir: Path | str | None = None, *, level: str | None = None) -> None:
    """配置 root logger：挂单个 LogBusHandler（汇入日志总线）+ 配置 diagnostic.log。

    幂等：同 log_dir 重复调用 no-op；不同 log_dir 替换 handler + diagnostic 不堆叠。
    跳过 temporalio.activity（独立 redirect，propagate=False）。
    log_dir=None 时无 diagnostic.log（fallback 丢弃），供无 session 上下文用。

    Args:
        log_dir: diagnostic.log 所在目录；不存在自动创建。None 则不写文件。
        level: root logger level；默认读 SUPERNOVA_LOG_LEVEL，再默认 INFO。
    """
    global _configured_log_dir

    # spec 2026-07-14 §模块1：默认压 temporalio Rust core tracing 到 error（去掉良性
    # Activity-not-found WARN 刷屏，保留 error 级）。用户可在 .env 用 RUST_LOG 覆盖。
    os.environ.setdefault("RUST_LOG", "temporalio_sdk_core=error")

    root = logging.getLogger()
    resolved = Path(log_dir) if log_dir is not None else None
    if resolved is not None:
        resolved.mkdir(parents=True, exist_ok=True)

    # 幂等：同 log_dir 已配过 -> no-op
    if resolved is not None and _configured_log_dir == resolved:
        return

    # 移除我们之前加的 handler（log_dir 变更或首次配置），保留调用方/其他机制加的。
    for h in list(root.handlers):
        if getattr(h, "_shannon_configured", False):
            root.removeHandler(h)
            h.close()

    # 挂单个 LogBusHandler（替代 StreamHandler(stderr)+FileHandler）。
    handler = LogBusHandler()
    handler._shannon_configured = True  # type: ignore[attr-defined]
    root.addHandler(handler)

    # 配置 diagnostic.log（LogBus 单例的 DiagnosticLog 句柄）。
    if resolved is not None:
        LogBus.configure_diagnostic(resolved / _DIAGNOSTIC_FILENAME)
        _configured_log_dir = resolved
    else:
        _configured_log_dir = None

    # root level：参数 > env > 默认 INFO
    resolved_level = level or os.getenv("SUPERNOVA_LOG_LEVEL", "INFO").upper()
    root.setLevel(resolved_level)

    # 第三方噪声库固定 WARNING，不随 root level 放开（避免 DEBUG 刷屏）
    for name in _NOISE_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # temporalio.activity + temporalio.worker 子树由 temporalio_redirect 独立管:
    # 不动其 handlers/propagate(redirect 覆盖两者, propagate=False 截断到本 LogBus)。
