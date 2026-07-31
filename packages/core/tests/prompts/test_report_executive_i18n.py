"""report-executive 双语：{{REPORT_HEADER_BLOCK}} / {{ATTACK_CHAIN_HEADING}}
按 current_lang 注入；_build_vuln_summary_subsections 双语。"""
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


def test_vuln_summary_subsections_en(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")
    mgr = PromptManager(PROMPTS)
    out = mgr._build_vuln_summary_subsections(["injection"])
    assert "攻击链" not in out
    assert "attack chain" in out.lower() or "Attack Chains" in out
