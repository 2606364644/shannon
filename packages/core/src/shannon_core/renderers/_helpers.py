"""renderer 共享 helpers —— placeholder + render_table。

Plan 2 (recon) / Plan 3 (vuln) 共用。纯函数,无 GitNexus / 确定性层依赖(守 §1)。
对齐 TS apps/worker/src/services/vuln-renderer.ts 的 placeholder/renderTable 行为;
render_table 在 TS 基础上额外 escape newline,避免单元值换页破坏 table。
"""
from __future__ import annotations


def placeholder(section_label: str, tool_name: str) -> str:
    """缺 section 的显式占位提示(对齐 TS placeholder)。

    返回:  _[{section_label}: not provided — `{tool_name}` was not called]_
    """
    return f"_[{section_label}: not provided — `{tool_name}` was not called]_"


def _escape_cell(value: str) -> str:
    """escape 管道符和换行,防单元值破坏 markdown table 结构。"""
    return value.replace("|", "\\|").replace("\n", " ")


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    """GitHub-flavored markdown table(header + --- 分隔 + 数据行)。

    空 rows → 返回空串(由 caller 决定 placeholder / "None identified" 文案)。
    """
    if not rows:
        return ""
    header_row = "| " + " | ".join(_escape_cell(h) for h in headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join(
        "| " + " | ".join(_escape_cell(str(cell)) for cell in row) + " |"
        for row in rows
    )
    return f"{header_row}\n{separator}\n{body}"
