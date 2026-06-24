from shannon_core.services.framework_analyzer import InferredEndpoint
from shannon_core.services.framework_endpoint_renderer import render_framework_endpoints


def _ep(**overrides):
    base = dict(
        method="DELETE",
        path="/api/Feedbacks/:id",
        source="framework-auto-generated",
        model="Feedback",
        middleware=("isAuthenticated",),
        vulnerability_indicators=("no-ownership-check",),
    )
    base.update(overrides)
    return InferredEndpoint(**base)


def test_render_empty_endpoints():
    out = render_framework_endpoints([])
    assert "无" in out or "no" in out.lower()


def test_render_lists_endpoints_with_origin():
    out = render_framework_endpoints([_ep()])
    assert "DELETE /api/Feedbacks/:id" in out
    assert "framework-auto-generated" in out
    assert "Feedback" in out


def test_render_includes_lower_bound_disclaimer():
    out = render_framework_endpoints([_ep()])
    # 下限非上限：recon LLM 仍须独立检查其他端点
    assert "下限" in out or "独立" in out
