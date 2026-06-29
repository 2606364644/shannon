"""LLM 轨移植 TS 两个对账机制的 prompt 内容断言。

Spec: docs/superpowers/specs/2026-06-29-llm-track-reconciliation-port-design.md
守 CLAUDE.md §1: 新 partial 不引入确定性产物（forbidden 由
test_static_dataflow_hints_decoupling.py 的 rglob 自动覆盖，此处只断言结构）。
"""
from pathlib import Path

# parents[4] = repo root（持有 prompts/），对齐 test_static_dataflow_hints_decoupling.py:20
PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"


def test_enumeration_completeness_partial_exists_and_has_core_sections():
    p = PROMPTS_DIR / "shared" / "_enumeration-completeness.txt"
    assert p.exists(), "missing prompts/shared/_enumeration-completeness.txt"
    text = p.read_text()
    # EC-A: 5 角度（含补的 frontend + gateway）
    assert "frontend" in text.lower(), "EC-A 缺 frontend-call 角度"
    assert "gateway" in text.lower(), "EC-A 缺 gateway 角度"
    # EC-B: anchor-count 对账表
    assert "Enumeration Reconciliation" in text, "EC-B 缺对账表标题"
    assert "true-miss" in text, "EC-B 缺 true-miss 分类"
    # EC-C: prefix-family gap
    assert "prefix" in text.lower(), "EC-C 缺 prefix-family gap 检查"
    # EC-D/E 交叉引用（不重复已有机制）
    assert "_cross-route-enumeration" in text, "EC-D 未交叉引用 _cross-route-enumeration.txt"
    # self-check 硬阻
    assert "RECONNAISSANCE COMPLETE" in text, "缺 self-check 硬阻（do NOT announce RECONNAISSANCE COMPLETE）"


def test_coverage_reconciliation_partial_exists_and_has_core_sections():
    p = PROMPTS_DIR / "shared" / "_coverage-reconciliation.txt"
    assert p.exists(), "missing prompts/shared/_coverage-reconciliation.txt"
    text = p.read_text()
    # CR-A: USER 端点全集 F
    assert "USER" in text, "CR-A 缺 USER 端点全集 F"
    # CR-C: G = F \ C 覆盖差集
    assert ("F \\ C" in text) or ("G = F" in text), "CR-C 缺 G = F \\ C 差集判定"
    # CR-D: 数据所有权判向量（tenant/region selector 也是向量）
    assert "tenant" in text.lower(), "CR-D 缺 tenant/region selector 向量分类"
    # CR-E: 每端点粒度（禁止全局合并）
    assert "per-endpoint" in text.lower() or "N independent" in text, "CR-E 缺每端点粒度规则"
    # self-check 硬阻
    assert "AUTHORIZATION ANALYSIS COMPLETE" in text, "缺 self-check 硬阻"
