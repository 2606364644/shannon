from shannon_core.code_index.recon_gitnexus_track import (
    EndpointSecurityContext,
    RouteRow,
    SharedRouteGroup,
    render_recon_gitnexus_track,
)


def _group(handler_id, rows, conflict=False):
    return SharedRouteGroup(handler_id=handler_id, routes=tuple(rows), auth_conflict=conflict)


def test_render_empty_yields_no_data_notice():
    out = render_recon_gitnexus_track([], [])
    assert "无" in out or "no" in out.lower()


def test_render_lists_route_group_table_with_preauth_warning():
    g = _group(
        "controller.js:index:32",
        [
            RouteRow("GET", "/preview", "present"),
            RouteRow("GET", "/preview/iframe-demo", "none"),
        ],
        conflict=True,
    )
    out = render_recon_gitnexus_track([g], [])
    assert "controller.js:index:32" in out
    assert "/preview/iframe-demo" in out
    assert "pre-auth" in out.lower() or "none" in out.lower()


def test_render_lists_endpoint_security_table():
    ctx = EndpointSecurityContext(
        method="PUT",
        path="/api/users/:id",
        handler_id="u.js:update:10",
        auth="present",
        middleware=("requireAuth",),
        ownership="none",
        ownership_evidence=None,
    )
    out = render_recon_gitnexus_track([], [ctx])
    assert "PUT /api/users/:id" in out
    assert "requireAuth" in out
    assert "none" in out.lower()


def test_render_includes_lower_bound_disclaimer_and_merge_rules():
    out = render_recon_gitnexus_track(
        [],
        [EndpointSecurityContext("GET", "/x", "h.js:f:1", "present", (), "none", None)],
    )
    assert "下限" in out or "独立" in out
    assert "危险侧" in out or "none" in out.lower()


def test_render_shows_ownership_evidence_when_present():
    ctx = EndpointSecurityContext(
        method="PUT",
        path="/api/u/:id",
        handler_id="u.js:up:1",
        auth="present",
        middleware=(),
        ownership="guarded",
        ownership_evidence="where: { userId: req.user.id }",
    )
    out = render_recon_gitnexus_track([], [ctx])
    assert "userId" in out
    assert "guarded" in out.lower() or "guarded" in out
