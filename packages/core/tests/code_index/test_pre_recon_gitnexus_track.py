from shannon_core.code_index.models import EntryPoint
from shannon_core.code_index.parameter_models import SinkCallSite, SinkCategory
from shannon_core.code_index.pre_recon_gitnexus_track import (
    build_pre_recon_gitnexus_track,
    render_pre_recon_gitnexus_track,
)
from shannon_core.code_index.template_escape_detector import TemplateEscapeFinding


def test_render_lists_entry_sinks_template():
    ep = EntryPoint(
        func_block_id="app.py:h:1",
        entry_type="http_route",
        route="/api/x",
        http_method="GET",
        confidence=0.9,
        evidence="router.get",
        needs_llm_review=False,
        authentication="public",
    )
    sink = SinkCallSite(
        id="s1",
        caller_id="app.py:h:1",
        file_path="app.py",
        line=5,
        column=8,
        callee_name="exec",
        callee_receiver=None,
        category=SinkCategory.COMMAND,
        sink_subtype="cmd",
        dangerous_slots=[],
        rule_id="python.exec",
    )
    finding = TemplateEscapeFinding(
        file_path="v.ejs",
        line=2,
        directive="<%-",
        escaping="unescaped",
        engine="ejs-unescaped",
    )

    md = render_pre_recon_gitnexus_track([ep], [sink], [finding])

    assert "/api/x" in md
    assert "app.py:5" in md
    assert "v.ejs:2" in md
    assert "unescaped" in md
    assert "下限" in md or "独立" in md


def test_build_degrades_when_no_code_index(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()

    md = build_pre_recon_gitnexus_track(tmp_path, deliverables)

    assert "降级" in md or "无" in md
