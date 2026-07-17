"""make_collector / render_deliverable 双函数 RECON 分发测试（Plan 2 Task 3）。"""
from shannon_core.collectors import make_collector
from shannon_core.renderers import render_deliverable
from shannon_core.models.agents import AgentName


def test_make_collector_returns_recon_collector():
    c = make_collector(AgentName.RECON)
    assert c is not None
    # 9 个 section（8 set_* + 1 set_endpoints append）
    assert len(c.section_schemas) == 9
    assert "set_endpoints" in c.tool_names()


def test_make_collector_recon_independent_instance():
    a = make_collector(AgentName.RECON)
    b = make_collector(AgentName.RECON)
    assert a is not b  # 每次新实例（per-agent-run，非全局）


def test_render_deliverable_recon_returns_md():
    md = render_deliverable(AgentName.RECON, {})
    assert md is not None
    assert "# Reconnaissance Deliverable" in md
    assert "## 0) HOW TO READ THIS" in md


def test_render_deliverable_recon_uses_endpoints_data():
    md = render_deliverable(AgentName.RECON, {
        "endpoints": [{"method": "GET", "path": "/api/users/me",
                       "required_role": "user", "object_id_parameters": "None",
                       "authorization_mechanism": "Bearer", "description_code_pointer": "x.ts:1"}]
    })
    assert "/api/users/me" in md
    assert "## 4. API Endpoint Inventory" in md


def test_unmapped_agent_returns_none_for_both():
    # 一个无 collector 通道的 agent（如 VALIDATE_AUTH，若有；否则用 RECON_BLACKBOX）
    # 确认返 None（走 self-Write 路径）
    from shannon_core.models.agents import AgentName as A
    # RECON_BLACKBOX 是黑盒，不在 core host-render 范围
    assert make_collector(A.RECON_BLACKBOX) is None
    assert render_deliverable(A.RECON_BLACKBOX, {}) is None


def test_pre_recon_regression_unchanged():
    # 回归：PRE_RECON 分发不受 RECON 分支影响
    c = make_collector(AgentName.PRE_RECON)
    assert c is not None
    assert "set_executive_summary" in c.tool_names()
    md = render_deliverable(AgentName.PRE_RECON, {})
    assert "# Penetration Test Scope" in md
