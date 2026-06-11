"""Tests for framework_analyzer service."""

from shannon_core.services.framework_analyzer import (
    EndpointTemplate,
    FrameworkPattern,
    FRAMEWORK_PATTERNS,
    InferredEndpoint,
    FrameworkAnalysisResult,
    _generate_inferred_endpoints,
    _build_recommendations,
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


class TestGenerateInferredEndpoints:
    def test_collection_template_skips_put_and_delete(self):
        """Collection templates (no :id) must produce only GET and POST."""
        framework = FrameworkPattern(
            name="test-fw",
            detection_patterns={},
            endpoint_templates=(
                EndpointTemplate(
                    methods=("GET", "POST", "PUT", "DELETE"),
                    path_template="/api/{Model}s",
                    default_middleware=(),
                    notes="collection",
                ),
            ),
            vulnerability_patterns=(),
        )
        endpoints = _generate_inferred_endpoints(framework, ["User"])
        methods = [ep.method for ep in endpoints]
        assert "GET" in methods
        assert "POST" in methods
        assert "PUT" not in methods
        assert "DELETE" not in methods

    def test_individual_template_includes_all_methods(self):
        """Templates with :id should produce all four CRUD methods."""
        framework = FrameworkPattern(
            name="test-fw",
            detection_patterns={},
            endpoint_templates=(
                EndpointTemplate(
                    methods=("GET", "POST", "PUT", "DELETE"),
                    path_template="/api/{Model}s/:id",
                    default_middleware=("isAuthenticated",),
                    notes="individual",
                ),
            ),
            vulnerability_patterns=(),
        )
        endpoints = _generate_inferred_endpoints(framework, ["User"])
        methods = [ep.method for ep in endpoints]
        assert "GET" in methods
        assert "POST" in methods
        assert "PUT" in methods
        assert "DELETE" in methods

    def test_no_models_produces_no_endpoints(self):
        framework = FRAMEWORK_PATTERNS[0]
        endpoints = _generate_inferred_endpoints(framework, [])
        assert endpoints == []

    def test_path_substitution(self):
        framework = FrameworkPattern(
            name="test-fw",
            detection_patterns={},
            endpoint_templates=(
                EndpointTemplate(
                    methods=("GET",),
                    path_template="/api/{resource}",
                    default_middleware=(),
                    notes="lowercase resource",
                ),
            ),
            vulnerability_patterns=(),
        )
        endpoints = _generate_inferred_endpoints(framework, ["MyModel"])
        assert endpoints[0].path == "/api/mymodel"


class TestBuildRecommendations:
    def test_includes_delete_count(self):
        framework = FRAMEWORK_PATTERNS[0]
        endpoints = [
            InferredEndpoint(method="DELETE", path="/api/Users/:id", source="framework-auto-generated"),
            InferredEndpoint(method="DELETE", path="/api/Posts/:id", source="framework-auto-generated"),
        ]
        recs = _build_recommendations(framework, endpoints)
        assert any("2 DELETE" in r for r in recs)

    def test_includes_put_count(self):
        framework = FRAMEWORK_PATTERNS[0]
        endpoints = [
            InferredEndpoint(method="PUT", path="/api/Users/:id", source="framework-auto-generated"),
            InferredEndpoint(method="PUT", path="/api/Posts/:id", source="framework-auto-generated"),
            InferredEndpoint(method="PUT", path="/api/Comments/:id", source="framework-auto-generated"),
        ]
        recs = _build_recommendations(framework, endpoints)
        assert any("3 PUT" in r for r in recs)

    def test_no_delete_no_delete_rec(self):
        framework = FRAMEWORK_PATTERNS[0]
        endpoints = [
            InferredEndpoint(method="GET", path="/api/Users", source="framework-auto-generated"),
        ]
        recs = _build_recommendations(framework, endpoints)
        # The dynamically generated recommendation about DELETE count should be absent;
        # vulnerability_patterns may still mention "DELETE" by name.
        assert not any("DELETE endpoint(s)" in r for r in recs)

    def test_includes_vulnerability_patterns(self):
        framework = FRAMEWORK_PATTERNS[0]
        endpoints = []
        recs = _build_recommendations(framework, endpoints)
        for vp in framework.vulnerability_patterns:
            assert vp in recs
