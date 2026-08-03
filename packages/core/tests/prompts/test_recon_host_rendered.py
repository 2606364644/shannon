"""Plan 2 / Task 5 — recon prompt host-rendered conversion assertions.

锁定 prompts/recon-static.txt（spec 2026-08-03 白盒去动态:动态 recon.txt 已删）:
- 不再指示 agent self-Write recon_deliverable.md（Write-tool / "synthesize into a
  Markdown report" 模式 gone）;
- 带 MANDATORY 9 set_* 工具指令 + <deliverable_tools> 块;
- 区分 8 one-shot（DuplicateError）vs set_endpoints append;
- 保留插值标记 {{DELIVERABLES_PATH}} + §0-9 结构;
- 不引确定性层产物（守 §1 双轨铁律）。

对齐 test_vuln_host_rendered.py（Plan 3 vuln prompt host-rendered 不变量模板）。
"""
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"

RECON_PROMPTS = ["recon-static.txt"]

# 9 工具(8 one-shot + 1 append)
SET_TOOLS = (
    "set_executive_summary", "set_technology_stack", "set_authentication",
    "set_input_vectors", "set_network_map", "set_role_architecture",
    "set_authz_candidates", "set_injection_sources",  # 8 one-shot
    "set_endpoints",  # append
)

# MUST be absent — self-Write recon_deliverable.md 指令(Plan 2 改造前曾有)
FORBIDDEN_PATTERNS = (
    "write it to `{{DELIVERABLES_PATH}}/recon_deliverable.md` using the Write tool",
    "Use the **Write** tool to create `{{DELIVERABLES_PATH}}/recon_deliverable.md`",
    "Do NOT write the entire report in a single tool call",
)


def _read(name: str) -> str:
    return (PROMPTS_DIR / name).read_text("utf-8")


# ─── host-rendered conversion invariants ────────────────────────────────────

def test_host_rendered_block_present():
    """recon prompt has the MANDATORY set_* + <deliverable_tools> block."""
    for name in RECON_PROMPTS:
        text = _read(name)
        # MANDATORY host-rendered instruction(对齐 recon.txt 的
        # "MANDATORY — Recon Deliverable (host-rendered):" 措辞)
        assert "MANDATORY" in text and "host-rendered" in text.lower(), (
            f"{name}: missing MANDATORY host-rendered instruction"
        )
        # <deliverable_tools> 块
        assert "<deliverable_tools>" in text and "</deliverable_tools>" in text, (
            f"{name}: missing <deliverable_tools> block"
        )
        # 9 个 set_* 工具名都在
        for tool in SET_TOOLS:
            assert tool in text, f"{name}: missing tool name {tool}"


def test_self_write_instructions_gone():
    """recon prompt MUST NOT tell the agent to Write the deliverable md."""
    for name in RECON_PROMPTS:
        text = _read(name)
        for pat in FORBIDDEN_PATTERNS:
            assert pat not in text, (
                f"{name}: forbidden self-Write pattern present: {pat!r}"
            )


def test_one_shot_vs_append_distinction():
    """deliverable_tools 块必须区分 8 one-shot（DuplicateError）vs set_endpoints append。

    对齐 prompts/recon.txt 的「Call semantics — two distinct modes」段:8 one-shot
    重复调返 DuplicateError(首次生效);set_endpoints 是 append(多次调累积)。
    """
    for name in RECON_PROMPTS:
        text = _read(name)
        assert "DuplicateError" in text, (
            f"{name}: missing DuplicateError note for 8 one-shot tools"
        )
        assert "append" in text.lower(), (
            f"{name}: missing append semantics for set_endpoints"
        )


def test_interpolation_markers_intact():
    """`{{DELIVERABLES_PATH}}` 必须保留(无破坏的插值标记)。"""
    for name in RECON_PROMPTS:
        text = _read(name)
        assert "{{DELIVERABLES_PATH}}" in text, (
            f"{name}: {{{{DELIVERABLES_PATH}}}} marker missing"
        )
        # 无明显破坏的标记(未配对的 `{{` 或 `}}`)
        # 软检查:`{{` 数 == `}}` 数(允许 @include 不使用标记)
        left = text.count("{{")
        right = text.count("}}")
        assert left == right, (
            f"{name}: unbalanced {{ / }} (left={left} right={right})"
        )


def test_no_deterministic_hints_added():
    """§1 不变量:prompt 不引确定性层产物。"""
    forbidden = ("parameter_graph", "SinkCallSite", "static_dataflow_hints", "code_index.json")
    for name in RECON_PROMPTS:
        text = _read(name)
        for tok in forbidden:
            assert tok not in text, (
                f"{name}: references deterministic token {tok!r} (§1 violation)"
            )


def test_section_structure_preserved():
    """§0-9 结构描述保留(agent 组织 payload 的参照,renderer 1:1 复刻)。

    recon.txt 用 "## 0) HOW TO READ THIS",recon-static.txt 用 "## 0) HOW TO READ"
    (更短变体)——两者都保留 §0 结构,取共同子串 "HOW TO READ" 断言。
    """
    for name in RECON_PROMPTS:
        text = _read(name)
        assert "HOW TO READ" in text, f"{name}: missing §0 HOW TO READ"
        assert "Executive Summary" in text, f"{name}: missing §1 Executive Summary"
        assert "API Endpoint Inventory" in text, (
            f"{name}: missing §4 API Endpoint Inventory"
        )
        assert "Network" in text, f"{name}: missing §6 Network section"
        assert "Injection Sources" in text, f"{name}: missing §9 Injection Sources"
