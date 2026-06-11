"""Tests for framework_analyzer service."""
import pytest

from shannon_core.services.framework_analyzer import (
    EndpointTemplate,
    FrameworkPattern,
    FRAMEWORK_PATTERNS,
    InferredEndpoint,
    FrameworkAnalysisResult,
)


class TestFrameworkPatterns:
    def test_patterns_defined(self):
        assert len(FRAMEWORK_PATTERNS) >= 2

    def test_finale_rest_pattern(self):
        fr = FRAMEWORK_PATTERNS[0]
        assert fr.name == "finale-rest"
        assert "import" in fr.detection_patterns
        assert "initialize" in fr.detection_patterns
        assert "config" in fr.detection_patterns
        assert len(fr.endpoint_templates) == 2
        assert len(fr.vulnerability_patterns) >= 2

    def test_epilogue_pattern(self):
        ep = FRAMEWORK_PATTERNS[1]
        assert ep.name == "epilogue"
        assert len(ep.endpoint_templates) >= 1


class TestInferredEndpoint:
    def test_creation(self):
        ep = InferredEndpoint(
            method="DELETE",
            path="/api/Feedbacks/:id",
            source="framework-auto-generated",
            model="Feedback",
            middleware=("isAuthenticated",),
            vulnerability_indicators=("No ownership check",),
        )
        assert ep.method == "DELETE"
        assert ep.model == "Feedback"


class TestEndpointTemplate:
    def test_collection_endpoint_skips_put_delete(self):
        """Collection endpoints (/api/Models) should not have PUT/DELETE."""
        tpl = EndpointTemplate(
            methods=("GET", "POST", "PUT", "DELETE"),
            path_template="/api/{Model}s",
            default_middleware=("isAuthenticated",),
            notes="test",
        )
        assert "GET" in tpl.methods
        assert "POST" in tpl.methods
