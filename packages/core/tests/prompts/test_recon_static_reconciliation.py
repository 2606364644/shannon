from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"


def _read(name: str) -> str:
    return (PROMPTS_DIR / name).read_text("utf-8")


def test_recon_static_includes_enumeration_completeness_partial():
    """recon-static 必须 @include _enumeration-completeness.txt（白盒主路径对账）。"""
    content = _read("recon-static.txt")
    assert "@include(shared/_enumeration-completeness.txt)" in content


def test_recon_static_includes_cross_route_enumeration_partial():
    """recon-static 必须 @include _cross-route-enumeration.txt（§4.1 shared route group 对账）。"""
    content = _read("recon-static.txt")
    assert "@include(shared/_cross-route-enumeration.txt)" in content


def test_recon_static_has_section_4_3_reconciliation_table():
    """recon-static 必须产出 §4.3 Enumeration Reconciliation（5 角度对账表）。"""
    content = _read("recon-static.txt")
    assert "4.3 Enumeration Reconciliation" in content or "### 4.3" in content
    # 5 角度都要点名
    for angle in ("Route-definition", "Controller-method", "Interface-contract",
                  "Frontend-call", "Gateway"):
        assert angle in content, f"recon-static 缺枚举角度 {angle}"


def test_recon_static_aligns_section_0_to_9_structure():
    """recon-static 产出契约必须含 §0-§9 骨架（与 recon.txt 一致）。"""
    content = _read("recon-static.txt")
    required = [
        "HOW TO READ",
        "Executive Summary",
        "Technology & Service Map",
        "API Endpoint Inventory",
        "Parameter Completeness",
        "Authorization Vulnerability Candidates",
        "Injection Sources",
    ]
    for sec in required:
        assert sec in content, f"recon-static 缺产出节: {sec}"


def test_recon_static_has_five_angle_methodology():
    """recon-static Phase 1 必须是 5 角度枚举（非维度扫描）。"""
    content = _read("recon-static.txt")
    # 5 角度 + anchor count 要求
    assert "anchor count" in content.lower() or "source anchor count" in content.lower()
    for angle in ("Route definitions", "Controller methods", "Interface contracts",
                  "Frontend calls", "Gateway config"):
        assert angle in content, f"recon-static methodology 缺角度: {angle}"


def test_recon_static_has_step_3_5_mandatory_reconciliation():
    """recon-static 必须有 Step 3.5 MANDATORY 对账（产 §4.3 前强制）。"""
    content = _read("recon-static.txt")
    assert "3.5" in content and "MANDATORY" in content.upper(), (
        "recon-static 缺 Step 3.5 强制对账"
    )


def test_recon_static_decoupled_from_deterministic():
    """守铁律：recon-static 不引确定性产物。"""
    content = _read("recon-static.txt")
    forbidden = ["parameter_graph", "SinkCallSite", "static_dataflow_hints", "static-dataflow-hints"]
    for tok in forbidden:
        assert tok not in content, f"recon-static 引确定性产物 token: {tok}"
