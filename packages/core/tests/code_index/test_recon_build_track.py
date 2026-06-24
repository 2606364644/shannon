import json

from shannon_core.code_index.recon_gitnexus_track import build_recon_gitnexus_track


def _index_json(entry_points, blocks):
    return json.dumps(
        {
            "repository": "r",
            "language": "typescript",
            "total_blocks": len(blocks),
            "total_entry_points": len(entry_points),
            "total_chains": 0,
            "blocks": blocks,
            "edges": [],
            "entry_points": entry_points,
            "chains": [],
        }
    )


def _block(handler_id, source):
    file_path, func_name, line = handler_id.rsplit(":", 2)
    return {
        "id": handler_id,
        "file_path": file_path,
        "function_name": func_name,
        "start_line": int(line),
        "end_line": int(line) + 3,
        "source_code": source,
        "parameters": [],
        "decorators": [],
        "language": "typescript",
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


def test_build_from_code_index(tmp_path):
    handler = "c.js:index:32"
    eps = [
        _ep(handler, "/preview", auth="required"),
        _ep(handler, "/preview/v2", auth="required"),
        _ep(handler, "/preview/iframe-demo", auth=None),
        _ep("u.js:update:10", "/api/users/:id", method="PUT"),
    ]
    blocks = [
        _block(handler, "router.use(requireAuth);\nexport function index() {}"),
        _block(
            "u.js:update:10",
            "async function update(req){ db.user.findFirst({where:{userId:req.user.id}}) }",
        ),
    ]
    (tmp_path / "code_index.json").write_text(_index_json(eps, blocks))

    md = build_recon_gitnexus_track(str(tmp_path))
    assert "c.js:index:32" in md
    assert "/preview/iframe-demo" in md
    assert "PUT /api/users/:id" in md
    assert "userId" in md
    assert "下限" in md


def test_build_missing_code_index_returns_empty_notice(tmp_path):
    md = build_recon_gitnexus_track(str(tmp_path))
    assert "无" in md or "no" in md.lower()
    assert isinstance(md, str)


def test_build_empty_entry_points_returns_notice(tmp_path):
    (tmp_path / "code_index.json").write_text(_index_json([], []))
    md = build_recon_gitnexus_track(str(tmp_path))
    assert "无" in md or "no" in md.lower()


def test_build_invalid_json_returns_empty_notice(tmp_path):
    (tmp_path / "code_index.json").write_text("not json")
    md = build_recon_gitnexus_track(str(tmp_path))
    assert isinstance(md, str)
