"""mr_scan.select_vuln_classes — 按 diff 特征选 vuln 类（spec 2026-09-03 §4.5）。

确定性小表启发式，非 prompt；判不准 → 全类兜底。
"""

from supernova_core.mr_scan.diff_manifest import DiffHunk, DiffLine, DiffManifest, DiffStats
from supernova_core.mr_scan.incremental_scope import ALL_VULN_CLASSES, select_vuln_classes


def _diff(*files: str) -> DiffManifest:
    hunks = [
        DiffHunk(file_path=f, old_start=1, old_lines=1, new_start=1, new_lines=2,
                 added=[DiffLine(text="x", head_line_no=1)])
        for f in files
    ]
    return DiffManifest(base_commit="b", head_commit="h", hunks=hunks,
                        stats=DiffStats(files=len(files), insertions=len(files)))


def test_frontend_render_files_select_xss_only():
    assert select_vuln_classes(_diff("ui/components/UserBadge.tsx")) == ["xss"]


def test_backend_route_files_select_taint_and_authz_classes():
    classes = select_vuln_classes(_diff("app/routes.py"))
    assert "injection" in classes and "ssrf" in classes and "authz" in classes
    assert "xss" not in classes and "auth" not in classes


def test_auth_paths_select_auth_and_authz():
    classes = select_vuln_classes(_diff("app/middleware/auth.py"))
    assert "auth" in classes and "authz" in classes
    assert "injection" not in classes


def test_unrecognized_changes_fall_back_to_all_classes():
    assert select_vuln_classes(_diff("README.md")) == list(ALL_VULN_CLASSES)


def test_mixed_diff_takes_union_in_canonical_order():
    classes = select_vuln_classes(_diff("ui/App.tsx", "app/api/orders.py"))
    # 并集且按全类顺序
    assert classes == ["injection", "xss", "ssrf", "authz"]