import json

from shannon_core.code_index.pre_recon_gitnexus_track import build_pre_recon_gitnexus_track


def test_build_from_real_code_index_and_templates(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    (repo / "v.ejs").write_text("<%- user.name %>")
    (deliverables / "code_index.json").write_text(
        json.dumps(
            {
                "repository": "r",
                "language": "javascript",
                "total_blocks": 1,
                "total_entry_points": 1,
                "total_chains": 0,
                "blocks": [],
                "edges": [],
                "chains": [],
                "entry_points": [
                    {
                        "func_block_id": "app.js:h:1",
                        "entry_type": "http_route",
                        "route": "/render",
                        "http_method": "GET",
                        "confidence": 0.9,
                        "evidence": "app.get",
                        "needs_llm_review": False,
                        "authentication": "public",
                    }
                ],
                "sink_call_sites": [
                    {
                        "id": "app.js:h:render:12:4",
                        "caller_id": "app.js:h:1",
                        "callee_name": "render",
                        "callee_receiver": "res",
                        "category": "template",
                        "sink_subtype": "template_render",
                        "file_path": "app.js",
                        "line": 12,
                        "column": 4,
                        "dangerous_slots": [],
                        "rule_id": "express.render",
                    }
                ],
                "file_manifest": {
                    "entries": [
                        {"file_path": "v.ejs", "file_type": "template", "size_bytes": 20}
                    ]
                },
            }
        )
    )

    md = build_pre_recon_gitnexus_track(repo, deliverables)

    assert "/render" in md
    assert "app.js:12" in md
    assert "v.ejs" in md and "unescaped" in md
    assert "下限" in md or "独立" in md
