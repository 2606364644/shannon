"""Tests for attack_chain_builder service."""
import logging
import pytest

from supernova_core.services.attack_chain_builder import build_attack_chains
from supernova_core.services.framework_analyzer import FrameworkAnalysisResult, InferredEndpoint
from supernova_core.services.frontend_mapper import FrontendAnalysisResult, XssAttackChain


class TestBuildAttackChains:
    @pytest.mark.asyncio
    async def test_empty_results(self):
        framework_result = FrameworkAnalysisResult()
        frontend_result = FrontendAnalysisResult()
        chains = await build_attack_chains(framework_result, frontend_result, logging.getLogger())
        assert len(chains) == 0

    @pytest.mark.asyncio
    async def test_builds_from_xss_chains(self):
        framework_result = FrameworkAnalysisResult()
        frontend_result = FrontendAnalysisResult(
            xss_chains=[
                XssAttackChain("/input", "/api/data", "/view", "DataView", "high"),
            ],
        )
        chains = await build_attack_chains(framework_result, frontend_result, logging.getLogger())
        assert len(chains) == 1
        assert chains[0].vuln_type == "xss"

    @pytest.mark.asyncio
    async def test_builds_from_framework_endpoints(self):
        framework_result = FrameworkAnalysisResult(
            inferred_endpoints=[
                InferredEndpoint(
                    "DELETE", "/api/Users/:id", "framework-auto-generated",
                    model="User", vulnerability_indicators=("No ownership",),
                ),
            ],
        )
        frontend_result = FrontendAnalysisResult()
        chains = await build_attack_chains(framework_result, frontend_result, logging.getLogger())
        assert len(chains) == 1
        assert chains[0].vuln_type == "authz"

    @pytest.mark.asyncio
    async def test_builds_from_both(self):
        framework_result = FrameworkAnalysisResult(
            inferred_endpoints=[
                InferredEndpoint(
                    "DELETE", "/api/Items/:id", "framework-auto-generated",
                    model="Item", vulnerability_indicators=("No ownership",),
                ),
            ],
        )
        frontend_result = FrontendAnalysisResult(
            xss_chains=[
                XssAttackChain("/form", "/api/items", "/display", "DisplayView", "medium"),
            ],
        )
        chains = await build_attack_chains(framework_result, frontend_result, logging.getLogger())
        assert len(chains) == 2  # 1 IDOR + 1 XSS

    @pytest.mark.asyncio
    async def test_builds_xss_chain_zh_lang(self, monkeypatch):
        monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
        framework_result = FrameworkAnalysisResult()
        frontend_result = FrontendAnalysisResult(
            xss_chains=[XssAttackChain("/input", "/api/data", "/view", "DataView", "high")],
        )
        chains = await build_attack_chains(framework_result, frontend_result, logging.getLogger())
        assert chains
        assert "存储型 XSS" in chains[0].name
        assert "用户访问" in chains[0].steps[0].description

    @pytest.mark.asyncio
    async def test_builds_xss_chain_en_lang(self, monkeypatch):
        monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")
        framework_result = FrameworkAnalysisResult()
        frontend_result = FrontendAnalysisResult(
            xss_chains=[XssAttackChain("/input", "/api/data", "/view", "DataView", "high")],
        )
        chains = await build_attack_chains(framework_result, frontend_result, logging.getLogger())
        assert chains
        assert "Stored XSS" in chains[0].name
        assert "User navigates" in chains[0].steps[0].description
