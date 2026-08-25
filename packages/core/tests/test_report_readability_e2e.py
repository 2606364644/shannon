"""端到端：LLM 1 条 + GN 9 条（同单位笛卡尔积）→ 收敛/合并 → 渲染 → 速查表 →
报告级断言（spec 验收口径，plan Task 10）。

锁定全链「9 GN + 1 LLM → 1 张四要素卡」：GN 轨 9 条原始链（3 参数 × 3 行
sink 笛卡尔积）先按单位收敛（gn_collapse），再与 LLM 轨同单位条目按
(parameter, sink_location) 跨轨去重合并成 1 条 both；四要素卡片与速查表
均按归并单位渲染（1 卡 / 1 行，非 10 行笛卡尔积平铺），内部判定标签
（llm-pass-failed / needs_review / unparseable-llm）零泄漏。

构造自包含（复制改写自 packages/core/tests/code_index/test_dual_track_merger.py
的 _llm_inj / _gn_inj，勿跨测试模块 import）。
"""

import re

import pytest

from supernova_core.code_index.dual_track_merger import merge_dual_track_queues
from supernova_core.models.queue_schemas import InjectionVulnerability
from supernova_core.services.findings_renderer import render_vuln_card
from supernova_core.services.report_assembler import render_summary_table

# 问题代码 fence 内容（spec §5 四要素之「问题代码」——渲染层仅在 snippet
# 非空时出该节，e2e 传 snippet 走完整四要素路径）。
SNIPPET = "preTax = eval(req.body.preTax);\ncontributions.preTax = preTax;"


def _llm() -> InjectionVulnerability:
    """LLM 轨 1 条（叙述权威）：POST /contributions 的 eval 命令注入。"""
    return InjectionVulnerability(
        ID="INJ-VULN-01", vulnerability_type="injection",
        externally_exploitable=True, confidence="high",
        title="命令注入：POST /contributions 直接 eval()（RCE）",
        source="preTax & req.body",
        path="POST /contributions → handleContributionsUpdate → eval(req.body.preTax)",
        sink_function="eval", verdict="vulnerable", severity="high",
        affected_entries=[{"parameter": "preTax",
                           "sink_location": "app/routes/contributions.js:32",
                           "chain_id": None, "track": "llm"}])


def _gn_9() -> list[InjectionVulnerability]:
    """GN 轨 9 条：同单位 3 参数 × 3 行 sink 的笛卡尔积（收敛前原始链形态）。"""
    return [
        InjectionVulnerability(
            ID=f"INJ-GN-{i:02d}", vulnerability_type="injection",
            externally_exploitable=True, confidence="low",
            source=f"{param} (app/routes/contributions.js:ContributionsHandler:7)",
            path="POST /contributions → chain",
            sink_call=(f"app/routes/contributions.js:ContributionsHandler:"
                       f"eval:{line}:{line}"),
            verdict="vulnerable", source_track="gitnexus")
        for i, (param, line) in enumerate(
            [(p, ln) for p in ("preTax", "afterTax", "roth")
             for ln in (32, 33, 34)], start=1)
    ]


async def test_e2e_nine_gn_one_llm_become_one_card(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    merged = merge_dual_track_queues([_llm()], _gn_9())

    # 收敛 + 跨轨去重：9 条 GN 收敛为 1 单位，与 LLM 同单位条目合并成 1 条 both
    assert len(merged) == 1
    m = merged[0]
    assert m.merge_source == "both"
    assert m.ID == "INJ-VULN-01"            # LLM 轨为 base（叙述权威）
    assert m.severity == "critical"          # GN 兜底 eval=critical 取高
    # LLM 1 行 + GN 收敛 9 行按 (parameter, sink_location) 去重：LLM 行
    # (preTax, contributions.js:32) 与 GN 收敛 9 行中首行同键 → 9 行非 10
    # （控制器裁决：brief 原文 10 未计该重复）。
    assert len(m.affected_entries) == 9
    assert set(m.affected_parameters) == {"preTax", "afterTax", "roth"}

    card = render_vuln_card(m, "injection", SNIPPET)
    # 正文四节齐 + 受影响入口 + 卡标题带 LLM title
    assert card.startswith("### INJ-VULN-01 注入漏洞：命令注入")
    for s in ("**漏洞成因（研判依据）**", "**危害**", "**问题点**",
              "**修复建议**", "**受影响入口**", "#### 漏洞细节"):
        assert s in card, f"卡片缺正文小节: {s}"
    assert "双轨确认" in card                    # merge_source=both 的元信息
    assert SNIPPET in card                       # 问题点 fence
    # 受影响入口表按归并单位渲染：9 行数据（GN 笛卡尔积不平铺成 9 张卡）
    entry_rows = re.findall(
        r"^\| (?:preTax|afterTax|roth) \| app/routes/contributions\.js:\d+ \|",
        card, re.M)
    assert len(entry_rows) == 9
    # 内部标签零出现（spec §9）
    for banned in ("llm-pass-failed", "needs_review", "unparseable-llm"):
        assert banned not in card, banned

    # 速查表按归并单位计数（1 行非 10 行）
    table = render_summary_table({"injection": merged})
    assert table.count("\n| INJ-") == 1
    assert "## 漏洞速查表" in table
    row = [l for l in table.splitlines() if l.startswith("| INJ-")][0]
    assert row.startswith("| INJ-VULN-01")
    assert "POST /contributions" in row      # endpoint 从 path 提取
    assert "preTax" in row and "roth" in row  # 参数列
    assert "严重" in row                       # critical 中文档位
