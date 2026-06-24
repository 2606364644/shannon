"""End-to-end checks for code_index.json -> recon GitNexus markdown."""

import json

from shannon_core.code_index.recon_gitnexus_track import build_recon_gitnexus_track


def _block(handler_id, source, decorators=None, language="typescript"):
    file_path, func_name, line = handler_id.rsplit(":", 2)
    return {
        "id": handler_id,
        "file_path": file_path,
        "function_name": func_name,
        "start_line": int(line),
        "end_line": int(line) + 5,
        "source_code": source,
        "parameters": [],
        "decorators": decorators or [],
        "language": language,
    }


def _ep(handler_id, route, method="GET", auth=None):
    return {
        "func_block_id": handler_id,
        "entry_type": "http_route",
        "route": route,
        "http_method": method,
        "confidence": 0.9,
        "evidence": "",
        "needs_llm_review": False,
        "authentication": auth,
        "source": "code_index",
    }


def _write_index(tmp_path, eps, blocks):
    (tmp_path / "code_index.json").write_text(
        json.dumps(
            {
                "repository": "r",
                "language": "typescript",
                "total_blocks": len(blocks),
                "total_entry_points": len(eps),
                "total_chains": 0,
                "blocks": blocks,
                "edges": [],
                "entry_points": eps,
                "chains": [],
            }
        )
    )


def test_e2e_shared_group_with_preauth_plus_unowned_endpoint(tmp_path):
    shared = "c.js:index:32"
    unowned = "u.js:list:1"
    _write_index(
        tmp_path,
        [
            _ep(shared, "/preview", auth="required"),
            _ep(shared, "/preview/v2", auth="required"),
            _ep(shared, "/preview/iframe-demo", auth=None),
            _ep(unowned, "/api/users", method="GET"),
        ],
        [
            _block(shared, "@UseGuards(AuthGuard)\nexport function index() {}"),
            _block(unowned, "async function list(req) { return db.user.findMany(); }"),
        ],
    )

    md = build_recon_gitnexus_track(str(tmp_path))

    assert "c.js:index:32" in md
    assert "/preview/iframe-demo" in md
    assert "pre-auth" in md.lower()
    assert "GET /api/users" in md
    assert "none" in md.lower()
    assert "下限" in md
    assert "危险侧" in md


def test_e2e_java_decorator_auth_and_orm_ownership(tmp_path):
    handler = "Ctrl.java:update:20"
    _write_index(
        tmp_path,
        [_ep(handler, "/api/items/:id", method="PUT")],
        [
            _block(
                handler,
                "@PreAuthorize(\"hasRole('USER')\")\n"
                "public void update() { repo.findByOwnerId(req.user.id); }",
                decorators=['@PreAuthorize("hasRole(\'USER\')")'],
                language="java",
            ),
        ],
    )

    md = build_recon_gitnexus_track(str(tmp_path))
    assert "PUT /api/items/:id" in md
    assert "present" in md.lower()
    assert "guarded" in md.lower() or "owner" in md.lower()


def test_e2e_graceful_degradation_when_index_missing(tmp_path):
    md = build_recon_gitnexus_track(str(tmp_path))
    assert isinstance(md, str)
    assert "无" in md or "no" in md.lower()


def test_e2e_unresolved_handler_marked_unknown(tmp_path):
    _write_index(tmp_path, [_ep("ghost.js:f:1", "/api/ghost")], [])
    md = build_recon_gitnexus_track(str(tmp_path))
    assert "/api/ghost" in md
    assert "unknown" in md.lower()
