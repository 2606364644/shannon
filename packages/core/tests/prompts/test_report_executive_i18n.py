"""report-executive 双语：{{REPORT_HEADER_BLOCK}} / {{ATTACK_CHAIN_HEADING}}
按 current_lang 注入；_build_vuln_summary_subsections 双语。"""
import re
from pathlib import Path

from supernova_core.prompts.manager import PromptManager

PROMPTS = Path(__file__).resolve().parents[4] / "prompts"


def test_header_block_zh(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    mgr = PromptManager(PROMPTS)
    out = mgr._interpolate("{{REPORT_HEADER_BLOCK}}", {}, None, "report-executive")
    assert "# 安全评估报告" in out
    assert "## 执行摘要" in out


def test_header_block_en(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")
    mgr = PromptManager(PROMPTS)
    out = mgr._interpolate("{{REPORT_HEADER_BLOCK}}", {}, None, "report-executive")
    assert "# Security Assessment Report" in out
    assert "## Executive Summary" in out
    assert "安全评估报告" not in out


def test_attack_chain_heading_placeholder_en(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")
    mgr = PromptManager(PROMPTS)
    out = mgr._interpolate("{{ATTACK_CHAIN_HEADING}}", {}, None, "report-executive")
    assert "Attack Chains" in out


def test_vuln_summary_subsections_zh(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    mgr = PromptManager(PROMPTS)
    out = mgr._build_vuln_summary_subsections(["injection"])
    assert "攻击链" in out  # zh 指令
    # 2026-08-25 F3：Count 从速查表读取（与执行摘要②同口径）+ 类名映射表
    assert "漏洞速查表" in out
    assert "禁止自行清点漏洞卡片" in out
    assert "### 注入漏洞" in out
    assert "### Injection" not in out


def test_vuln_summary_subsections_en(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")
    mgr = PromptManager(PROMPTS)
    out = mgr._build_vuln_summary_subsections(["injection"])
    assert "攻击链" not in out
    assert "attack chain" in out.lower() or "Attack Chains" in out
    # 2026-08-25 F3：en 版对应（速查表读取 + 映射类名）
    assert "Vulnerability Summary Table" in out
    assert "do NOT count vulnerability cards" in out
    assert "### Injection Vulnerabilities" in out


def test_keep_rules_no_dead_heading_placeholders():
    """KEEP 白名单无死变量残留 + 反冗余子标题指令。

    REPORT_VULN_HEADING / REPORT_VULN_SUBHEADING 是 2026-05-30 从 TS port
    带入的死变量（manager.py 从未注入），既触发 _interpolate 末尾的残留
    WARNING，又诱导 LLM 逐字发明「已确认漏洞」/ Confirmed Vulnerabilities
    冗余 h2（2026-08-12 前端 MarkdownView 已兜底降级，此处关 prompt 根因）。
    去变量化为单花括号自然语言模式后，两件事都应成立。"""
    mgr = PromptManager(PROMPTS)
    out = mgr.load_sync(
        "report-executive",
        {"web_url": "http://t", "deliverables_path": "/d", "repo_path": "/r", "scratchpad_path": "/s"},
        None,
    )
    # 与 manager._interpolate 末尾 WARNING 检测同口径：无残留全大写占位符
    assert not re.findall(r"\{\{[A-Z][A-Z0-9_]*\}\}", out)
    # KEEP 白名单仍保留 per-class 标题模式语义
    assert "[Type]" in out
    # 反冗余子标题指令（点名已被证实的坏行为）
    assert "Confirmed Vulnerabilities" in out
