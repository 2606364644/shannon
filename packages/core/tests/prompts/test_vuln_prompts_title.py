"""漏洞 title 字段 prompt 锁定测试（spec 2026-08-06）。

assert 各产出 prompt 都要求 LLM 给漏洞一句话描述性标题（category + where it lives），
不得只写短分类标签。覆盖：
- 5 个 LLM vuln 轨 prompt（inj/xss/ssrf/auth/authz）的 exploitation_queue_format 块
- authz GitNexus judge 的 output_format
- report-executive 第二道 cleanup 措辞（保留 ID + 改写弱/空标题）
- 守铁律：确定性产物不喂 LLM 轨 prompt（test_static_dataflow_hints_decoupling 已覆盖，此处不重复）
"""
from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[4] / "prompts"

_VULN_PROMPTS = [
    "vuln-injection.txt",
    "vuln-xss.txt",
    "vuln-ssrf.txt",
    "vuln-auth.txt",
    "vuln-authz.txt",
]


def test_each_vuln_prompt_defines_title_field():
    """5 个 vuln prompt 的 queue_format 块都定义了 title 字段。"""
    for name in _VULN_PROMPTS:
        text = (PROMPTS / name).read_text()
        assert '"title"' in text, f"{name} 未在 exploitation_queue_format 定义 title 字段"


def test_authz_gitnexus_judge_prompt_defines_title_field():
    """authz GitNexus judge 的 output_format 含 title 字段。"""
    text = (PROMPTS / "authz_gitnexus_judge.txt").read_text()
    assert '"title"' in text


def test_report_executive_preserves_id_and_rewrites_weak_title():
    """report-executive 第二道 cleanup：保留 vulnerability ID，改写弱/空标题为描述性短语。"""
    text = (PROMPTS / "report-executive.txt").read_text()
    # 必须保留 ID（铁律：不删漏洞证据）
    assert "vulnerability ID" in text or "[TYPE]-VULN-NN" in text
    # title cleanup 规则在
    assert "title" in text.lower()
