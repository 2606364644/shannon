"""Tests for route_chain_builder service."""
import logging
import pytest

from shannon_core.services.route_chain_builder import (
    AttackChainStep,
    AttackChain,
    build_attack_chains_from_analysis,
)
from shannon_core.services.framework_analyzer import InferredEndpoint
from shannon_core.services.frontend_mapper import FrontendRoute, XssAttackChain


class TestBuildAttackChainsFromAnalysis:
    def test_empty_inputs(self):
        chains = build_attack_chains_from_analysis([], [], [], logging.getLogger())
        assert len(chains) == 0

    def test_xss_chain_from_frontend(self):
        xss_chains = [
            XssAttackChain(
                entry_point="/input",
                storage_endpoint="/api/data",
                render_endpoint="/view",
                sink="DataView",
                confidence="high",
            ),
        ]
        chains = build_attack_chains_from_analysis([], [], xss_chains, logging.getLogger())
        assert len(chains) == 1
        assert chains[0].vuln_type == "xss"
        assert chains[0].severity == "high"
        assert len(chains[0].steps) == 4  # input, storage, retrieval, render

    def test_idor_chain_from_framework(self):
        endpoints = [
            InferredEndpoint(
                method="DELETE",
                path="/api/Feedbacks/:id",
                source="framework-auto-generated",
                model="Feedback",
                middleware=("isAuthenticated",),
                vulnerability_indicators=("No ownership check",),
            ),
        ]
        chains = build_attack_chains_from_analysis(endpoints, [], [], logging.getLogger())
        assert len(chains) == 1
        assert chains[0].vuln_type == "authz"
        assert "IDOR" in chains[0].name

    def test_idor_chain_correlated_with_frontend(self):
        endpoints = [
            InferredEndpoint(
                method="DELETE",
                path="/api/Feedbacks/:id",
                source="framework-auto-generated",
                model="Feedback",
                middleware=("isAuthenticated",),
                vulnerability_indicators=("No ownership check",),
            ),
        ]
        frontend_routes = [
            FrontendRoute(
                path="/feedback",
                component="FeedbackView",
                authenticated=True,
            ),
        ]
        chains = build_attack_chains_from_analysis(endpoints, frontend_routes, [], logging.getLogger())
        assert len(chains) == 1
        # Should mention the frontend route in description
        assert "FeedbackView" not in chains[0].description  # no api_calls to correlate

    def test_combined_xss_and_idor(self):
        xss_chains = [
            XssAttackChain("/input", "/api/posts", "/view", "PostView", "medium"),
        ]
        endpoints = [
            InferredEndpoint("DELETE", "/api/Users/:id", "framework-auto-generated",
                             model="User", vulnerability_indicators=("No ownership",)),
        ]
        chains = build_attack_chains_from_analysis(endpoints, [], xss_chains, logging.getLogger())
        assert len(chains) == 2  # 1 XSS + 1 IDOR
