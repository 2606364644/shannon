"""ReportAssembler 已提升至 shannon_core.services.report_assembler。

本模块保留 re-export 以兼容 blackbox 既有 `from shannon_blackbox.services.report_assembler
import ReportAssembler` 写法;blackbox 代码无需改动 import。
"""
from shannon_core.services.report_assembler import ReportAssembler

__all__ = ["ReportAssembler"]
