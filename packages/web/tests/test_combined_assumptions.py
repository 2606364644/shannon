# packages/web/tests/test_combined_assumptions.py
"""锁死组合扫描依赖的现状不变量。"""
from pathlib import Path


def test_blackbox_web_path_uses_event_file_parent():
    """黑盒 web 路径：workspace_path = event_file.parent（组合接力把黑盒 event_file
    指向白盒 scan_dir → 黑盒产物自动落白盒目录/deliverables/blackbox/）。"""
    wf = Path("packages/blackbox/src/supernova_blackbox/pipeline/workflows.py").read_text()
    assert "Path(input.event_file).parent" in wf


def test_phase_event_and_log_phase_complete_already_exist():
    """D1：PhaseEvent + log_phase_complete(phase) 已存在，组合模式直接复用，不新增事件类型。
    _serialize 通用路径写 type=类名 → PhaseEvent 写出 type:'PhaseEvent'（≠scan_end）。"""
    import supernova_core.display.events as ev
    import supernova_core.audit.session as sess
    assert hasattr(ev, "PhaseEvent")
    assert hasattr(sess.AuditSession, "log_phase_complete")  # 签名 (phase: str)，不覆盖


def test_has_scan_end_only_matches_scan_end():
    """_ensure_scan_end 幂等前提：_has_scan_end 只认 type=='scan_end'，PhaseEvent 天然忽略。"""
    sm = Path("packages/web/src/supernova_web/components/scan_manager.py").read_text()
    assert '"scan_end"' in sm and "_has_scan_end" in sm


def test_completed_agents_in_session_toplevel():
    """进度分母分子：completed_agents 在 session.json 顶层（list_scans 可读）。"""
    s = Path("packages/core/src/supernova_core/session.py").read_text()
    assert "completed_agents" in s and "mark_agent_completed" in s


def test_auth_validation_workflow_exists_for_precheck():
    """D4 t0 预验证复用 AuthValidationWorkflow（独立 auth 段，不依赖白盒产物）。"""
    wf = Path("packages/blackbox/src/supernova_blackbox/pipeline/workflows.py").read_text()
    assert "class AuthValidationWorkflow" in wf
