"""renderer 共享 helpers -- placeholder + render_table + schema 违规止血守卫。

Plan 2 (recon) / Plan 3 (vuln) 共用。纯函数,无 GitNexus / 确定性层依赖(守 §1)。
对齐 TS apps/worker/src/services/vuln-renderer.ts 的 placeholder/renderTable 行为;
render_table 在 TS 基础上额外 escape newline,避免单元值换页破坏 table。
"""
from __future__ import annotations


def placeholder(section_label: str, tool_name: str) -> str:
    """缺 section 的显式占位提示(对齐 TS placeholder)。

    返回:  _[{section_label}: not provided - `{tool_name}` was not called]_
    """
    return f"_[{section_label}: not provided - `{tool_name}` was not called]_"


def _escape_cell(value: str) -> str:
    """escape 管道符和换行,防单元值破坏 markdown table 结构。"""
    return value.replace("|", "\\|").replace("\n", " ")


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    """GitHub-flavored markdown table(header + --- 分隔 + 数据行)。

    空 rows -> 返回空串(由 caller 决定 placeholder / "None identified" 文案)。
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


# ── schema 违规止血守卫(2026-07-20) ────────────────────────────────────
# collector set_section 不做类型校验(schema 仅作 LLM 参数提示),GLM 等模型违规
# 把 object 填成 str / array 填成 str 时,renderer 的 .get() 会崩 AttributeError,
# 经 classify_error_for_temporal default -> TransientError(retryable=True) 被
# PRODUCTION_RETRY 放大重试(曾 50×6min 卡死)。renderer 此处兜底:非 dict/list 一律
# 归一为空容器,让渲染降级到 placeholder/空字段而非崩溃。真正修复在 collector
# schema 强校验(治本 A,单独立项)。
def as_dict(value) -> dict:
    """非 dict(常见:str prose) -> {}。防 .get() 崩。"""
    return value if isinstance(value, dict) else {}


def as_list(value) -> list:
    """非 list -> []。防 for 迭代 str / 对 str join 按字符拆。"""
    return value if isinstance(value, list) else []


def as_dict_list(value) -> list:
    """非 list -> [];元素非 dict -> 跳过(防循环内 .get() 崩)。

    保留元素为 dict(含空 dict,下游 .get 默认值兜底)。
    """
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, dict)]
