"""统一日志入口：dictConfig 配置 root logger，散落 getLogger 自动套格式。

spec: docs/superpowers/specs/2026-07-08-unified-logging-facade-design.md (组件 1)

分工 + 统一格式/入口（方案 A 最小收编）：
- display 流（AuditSession.log_info/log_step -> display renderer）管用户进度（stdout）；
- logging 流（散落 getLogger(__name__)）管诊断排障，经此 dictConfig 统一 Formatter
  （[timestamp] [LEVEL5] name: msg，对齐 display 的 format_log_time + 等宽标签列），
  去 stderr + workspaces/<session>/diagnostic.log。
- 散落 ~20 处 getLogger 调用点零改动（propagate 走 root 自动套格式）- A 档核心收益。
- temporalio.activity logger 由 temporalio_redirect 独立管（propagate=False），
  此处跳过它（不 addHandler、不改 propagate），避免双重输出。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_DIAGNOSTIC_FILENAME = "diagnostic.log"
# 对齐 display.formatters.LABEL_WIDTH=5：INFO /WARN /ERROR 占 5 列等宽
_FORMAT = "[%(asctime)s] [%(levelname)5s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"  # 对齐 display.formatters.format_log_time

_NOISE_LOGGERS = ("httpx", "urllib3", "httpcore", "asyncio")

_configured_log_dir: Path | None = None


def configure_logging(log_dir: Path | str | None = None, *, level: str | None = None) -> None:
    """配置 root logger：stderr + diagnostic.log 双 handler，等宽 LEVEL 列 Formatter。

    幂等：同 log_dir 重复调用 no-op；不同 log_dir 替换 FileHandler 不堆叠。
    跳过 temporalio.activity（独立 redirect，propagate=False）。
    log_dir=None 时只配 stderr（无文件），供无 session 上下文（测试/standalone）用。

    Args:
        log_dir: diagnostic.log 所在目录；不存在自动创建。None 则不写文件。
        level: root logger level；默认读 SHANNON_LOG_LEVEL，再默认 INFO。
    """
    global _configured_log_dir

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

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    stderr_handler._shannon_configured = True  # type: ignore[attr-defined]
    root.addHandler(stderr_handler)

    if resolved is not None:
        file_handler = logging.FileHandler(resolved / _DIAGNOSTIC_FILENAME, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler._shannon_configured = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)
        _configured_log_dir = resolved
    else:
        _configured_log_dir = None

    # root level：参数 > env > 默认 INFO
    resolved_level = level or os.getenv("SHANNON_LOG_LEVEL", "INFO").upper()
    root.setLevel(resolved_level)

    # 第三方噪声库固定 WARNING，不随 root level 放开（避免 DEBUG 刷屏）
    for name in _NOISE_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # temporalio.activity 由 temporalio_redirect 独立管：不动其 handlers/propagate。
    # 此处显式什么都不做 -- 仅以注释表明"已知且有意跳过"。

    # stderr handler 级别跟 root（不单独限）；file handler 同。
