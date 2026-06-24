from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[3] / "prompts"


def _read(name: str) -> str:
    return (PROMPTS / f"{name}.txt").read_text(encoding="utf-8")


def test_recon_static_has_endpoint_security_context():
    """离线 recon deliverable 必须含 Endpoint Security Context section,
    供 vuln-authz 的 Section 0 读取(交叉点①闭环)。"""
    src = _read("recon-static")
    assert "Endpoint Security Context" in src, \
        "recon-static 须含 Endpoint Security Context section"
    # 必须含 framework origin 维度(供 authz 识别 finale-rest/epilogue 端点)
    assert "Framework Origin" in src or "framework origin" in src.lower(), \
        "recon-static 的 Endpoint Security Context 须含 Framework Origin 维度"
    assert "finale-rest" in src, "须覆盖 finale-rest 框架端点识别"
    assert "epilogue" in src, "须覆盖 epilogue 框架端点识别"


def test_recon_static_still_marks_no_browser():
    """静态分析约束保留(回归锚点)。"""
    src = _read("recon-static")
    assert "browser" in src.lower() and ("no" in src.lower() or "not" in src.lower())


def test_recon_static_has_section_8_authorization_candidates():
    """离线 recon deliverable 必须含 Section 8 Authorization Vulnerability Candidates
    及 8.1/8.2/8.3 三类子节,闭合 vuln-authz 对 'section 8' 的引用(离线闭环)。"""
    src = _read("recon-static")
    assert "8. Authorization Vulnerability Candidates" in src, \
        "recon-static 须含 ## 8. Authorization Vulnerability Candidates 节"
    assert "8.1 Horizontal Privilege Escalation Candidates" in src, \
        "须含 8.1 Horizontal Privilege Escalation Candidates 子节"
    assert "8.2 Vertical Privilege Escalation Candidates" in src, \
        "须含 8.2 Vertical Privilege Escalation Candidates 子节"
    assert "8.3 Context-Based Authorization Candidates" in src, \
        "须含 8.3 Context-Based Authorization Candidates 子节"
