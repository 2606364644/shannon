"""白盒 browser 解耦铁律测试(类比 static-dataflow-hints 解耦铁律)。

锁定两条不变量:
1. 白盒专用 prompt 模板不得注入 browser 命令。
2. 白盒 workflow 不得 resolve/check browser engine。
反向断言:黑盒专用模板必须保留 browser 占位符(防回归误删)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"
WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "src/shannon_whitebox/pipeline/workflows.py"
)

# 白盒专用模板(Phase 2 须清干净 browser)
WHITEBOX_TEMPLATES = [
    "recon",
    "vuln-auth",
    "vuln-authz",
    "vuln-injection",
    "vuln-ssrf",
    "vuln-xss",
]

# 黑盒专用 / 黑白共用模板(必须保留 browser 占位符,绝不动)
BLACKBOX_TEMPLATES = [
    "authz-exploit",
    "injection-exploit",
    "ssrf-exploit",
    "xss-exploit",
    "recon-blackbox",
    "validate-authentication",
]

BROWSER_MARKERS = [
    "{{BROWSER_COMMANDS}}",
    "{{BROWSER_SESSION_FLAG}}",
    "@include(shared/_shared-session.txt)",
]


class TestWhiteboxNoBrowserInPrompts:
    """铁律 1: 白盒专用模板不含任何 browser 占位符/include。"""

    @pytest.mark.parametrize("name", WHITEBOX_TEMPLATES)
    def test_whitebox_template_has_no_browser(self, name):
        content = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
        for marker in BROWSER_MARKERS:
            assert marker not in content, (
                f"白盒模板 {name}.txt 含 browser 标记 {marker}(应已移除)"
            )

    @pytest.mark.parametrize("name", BLACKBOX_TEMPLATES)
    def test_blackbox_template_keeps_browser(self, name):
        """反向断言: 黑盒专用模板仍含 browser 占位符(防回归误删)。"""
        content = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
        assert any(m in content for m in BROWSER_MARKERS), (
            f"黑盒模板 {name}.txt 丢失 browser 标记(误删? 黑盒需 browser)"
        )


class TestWhiteboxWorkflowNoBrowserEngine:
    """铁律 2: 白盒 workflow 不 resolve/check/write_config browser engine。"""

    def test_workflow_source_has_no_browser_engine_refs(self):
        src = WORKFLOW_PATH.read_text(encoding="utf-8")
        forbidden = [
            "BrowserEngineFactory.get_engine",
            "engine.check_available",
            "engine.write_config",
            "engine.cleanup_config",
        ]
        for token in forbidden:
            assert token not in src, (
                f"白盒 workflow 仍引用 browser engine: {token}"
            )
