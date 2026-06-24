from shannon_core.code_index.models import EntryPoint
from shannon_core.code_index.recon_gitnexus_track import detect_shared_route_groups


def _ep(func_block_id, route, method="GET", auth=None):
    return EntryPoint(
        func_block_id=func_block_id,
        entry_type="http_route",
        route=route,
        http_method=method,
        confidence=0.9,
        evidence="",
        needs_llm_review=False,
        authentication=auth,
    )


def test_no_groups_when_no_shared_handlers():
    eps = [
        _ep("app.py:handlerA:10", "/a"),
        _ep("app.py:handlerB:20", "/b"),
    ]
    assert detect_shared_route_groups(eps) == []


def test_groups_routes_by_handler():
    eps = [
        _ep("controller.js:index:32", "/preview", auth="required"),
        _ep("controller.js:index:32", "/preview/v2", auth="required"),
        _ep("controller.js:index:32", "/preview/iframe-demo", auth=None),
    ]
    groups = detect_shared_route_groups(eps)
    assert len(groups) == 1
    g = groups[0]
    assert g.handler_id == "controller.js:index:32"
    assert len(g.routes) == 3
    paths = {r.path for r in g.routes}
    assert paths == {"/preview", "/preview/v2", "/preview/iframe-demo"}
    assert g.auth_conflict is True


def test_auth_conflict_false_when_all_routes_have_auth():
    eps = [
        _ep("u.js:getProfile:45", "/api/users/me", auth="required"),
        _ep("u.js:getProfile:45", "/api/admin/users/profile", auth="required"),
    ]
    groups = detect_shared_route_groups(eps)
    assert len(groups) == 1
    assert groups[0].auth_conflict is False


def test_skips_routes_without_route():
    eps = [
        EntryPoint(
            func_block_id="x.py:f:1",
            entry_type="rpc",
            route=None,
            confidence=0.9,
            evidence="",
            needs_llm_review=False,
        ),
        EntryPoint(
            func_block_id="x.py:f:1",
            entry_type="rpc",
            route=None,
            confidence=0.9,
            evidence="",
            needs_llm_review=False,
        ),
    ]
    assert detect_shared_route_groups(eps) == []


def test_dedups_identical_route_within_handler():
    eps = [
        _ep("c.js:h:1", "/x", method="GET"),
        _ep("c.js:h:1", "/x", method="GET"),
        _ep("c.js:h:1", "/y", method="GET"),
    ]
    groups = detect_shared_route_groups(eps)
    assert len(groups) == 1
    assert len(groups[0].routes) == 2
