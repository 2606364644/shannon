"""Tests for frontend_mapper service."""
import pytest

from shannon_core.services.frontend_mapper import (
    FrontendRoute,
    ApiCall,
    UserInputPoint,
    XssAttackChain,
    FrontendAnalysisResult,
    identify_xss_chains,
    extract_base_path,
)


class TestDataModels:
    def test_frontend_route(self):
        r = FrontendRoute(path="/dashboard", component="DashboardComponent", authenticated=True)
        assert r.path == "/dashboard"
        assert r.api_calls == ()
        assert r.user_inputs == ()

    def test_xss_attack_chain(self):
        c = XssAttackChain(
            entry_point="/input",
            storage_endpoint="/api/data",
            render_endpoint="/view",
            sink="DataView",
            confidence="medium",
        )
        assert c.confidence == "medium"


class TestIdentifyXssChains:
    def test_no_chains_when_no_post(self):
        routes = [
            FrontendRoute(path="/view", component="View", authenticated=False,
                          api_calls=(ApiCall(endpoint="/api/data", method="GET", purpose="fetch"),)),
        ]
        chains = identify_xss_chains(routes)
        assert len(chains) == 0

    def test_chain_detected_when_post_get_share_base(self):
        routes = [
            FrontendRoute(
                path="/input",
                component="InputForm",
                authenticated=True,
                api_calls=(ApiCall(endpoint="/api/data", method="POST", purpose="save"),),
                user_inputs=(UserInputPoint(type="body", field="content"),),
            ),
            FrontendRoute(
                path="/view",
                component="DataView",
                authenticated=False,
                api_calls=(ApiCall(endpoint="/api/data", method="GET", purpose="fetch"),),
            ),
        ]
        chains = identify_xss_chains(routes)
        assert len(chains) == 1
        assert chains[0].entry_point == "/input"
        assert chains[0].render_endpoint == "/view"

    def test_no_chain_when_bases_differ(self):
        routes = [
            FrontendRoute(
                path="/input",
                component="InputForm",
                authenticated=True,
                api_calls=(ApiCall(endpoint="/api/posts", method="POST", purpose="save"),),
                user_inputs=(UserInputPoint(type="body", field="content"),),
            ),
            FrontendRoute(
                path="/view",
                component="DataView",
                authenticated=False,
                api_calls=(ApiCall(endpoint="/api/comments", method="GET", purpose="fetch"),),
            ),
        ]
        chains = identify_xss_chains(routes)
        assert len(chains) == 0


class TestExtractBasePath:
    def test_strips_id_segment(self):
        assert extract_base_path("/api/Users/:id") == "/api/Users"

    def test_no_id_segment(self):
        assert extract_base_path("/api/Users") == "/api/Users"
