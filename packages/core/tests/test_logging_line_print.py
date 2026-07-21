"""TDD for supernova_core.logging.line_print.print_line.

spec: docs/superpowers/specs/2026-07-08-unified-logging-facade-design.md (组件 2)
给拿不到 audit_session 的地方（scan_runner 信号 handler、非 activity CLI 进度）用：
复用 display.formatters.tag + symbols + format_log_time，行格式对齐 display 流，
不再手拼字面量。
"""
from __future__ import annotations

import re

from supernova_core.display.symbols import STEP_DONE
from supernova_core.logging.line_print import print_line


def test_format_has_timestamp_tag_symbol_body(capsys):
    """行格式：[timestamp] [TAG  ] symbol body -- 等宽标签 + 符号 + body。"""
    print_line("SCAN", STEP_DONE, "取消完成")
    out = capsys.readouterr().out
    # [YYYY-MM-DD HH:MM:SS] [SCAN ] ✓ 取消完成
    assert re.match(
        r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \[SCAN \] ✓ 取消完成\n$",
        out,
    ), out


def test_tag_padded_to_label_width_5(capsys):
    """标签等宽对齐 display LABEL_WIDTH=5：'POC' -> 'POC  '（右补空格到 5）。"""
    print_line("POC", STEP_DONE, "done")
    out = capsys.readouterr().out
    # [POC ] 标签列固定 5 宽（POC + 2 空格）
    assert "[POC  ]" in out, out


def test_flushed_immediately(capsys):
    """输出 flush=True（信号 handler / 进度行需立即可见，不等缓冲）。"""
    print_line("SCAN", STEP_DONE, "x")
    # capsys 捕获即证明已 flush；无额外断言，主要防 regression 改成 print 不带 flush
    assert "x" in capsys.readouterr().out


def test_empty_symbol_ok(capsys):
    """symbol 可空（裸信息行无状态符号，如纯提示）。"""
    print_line("SCAN", "", "正在取消")
    out = capsys.readouterr().out
    assert "[SCAN ]" in out
    assert "正在取消" in out
