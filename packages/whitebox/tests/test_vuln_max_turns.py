"""B2: vuln agent 专用 max_turns 决策（纯函数）。

vuln agent 用 SHANNON_VULN_MAX_TURNS(默认 500);其他 agent 返回 None,
executor/run_claude_prompt 收到 None 时沿用全局 env 默认,行为零变更。
"""
import pytest

from shannon_whitebox.pipeline.activities import _vuln_max_turns


class TestVulnMaxTurns:
    def test_vuln_agent_returns_500_default(self, monkeypatch):
        monkeypatch.delenv("SHANNON_VULN_MAX_TURNS", raising=False)
        assert _vuln_max_turns("injection-vuln") == 500
        assert _vuln_max_turns("xss-vuln") == 500

    def test_non_vuln_agent_returns_none(self, monkeypatch):
        monkeypatch.delenv("SHANNON_VULN_MAX_TURNS", raising=False)
        assert _vuln_max_turns("pre-recon") is None
        assert _vuln_max_turns("recon") is None
        assert _vuln_max_turns("report") is None

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SHANNON_VULN_MAX_TURNS", "800")
        assert _vuln_max_turns("ssrf-vuln") == 800
