"""修 0 wiring 检查（spec 2026-08-28-temporal-native-cancel-design，blackbox 侧）：

BlackboxScanWorkflow exploit gather(return_exceptions=True) 与 AuthValidation/
BatchAuth 的吞点必须放行取消——机制验证在 core/tests/runtime/
test_temporal_cancel_propagation.py（真 Temporal server），此处源码级 grep 锁
「每个已知吞点都有放行」不被回归删除（对齐 whitebox 侧同名测试）。
"""
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "supernova_blackbox" / "pipeline" / "workflows.py"


def test_import_is_cancellation():
    src = _SRC.read_text(encoding="utf-8")
    assert "from supernova_core.runtime.temporal_heartbeat import is_cancellation" in src


def test_swallow_points_release_cancellation():
    """exploit gather + 顶层 + AuthValidation setup_display + BatchAuth setup_display/
    probe per-cred 隔离，共 ≥5 处 is_cancellation 放行。"""
    src = _SRC.read_text(encoding="utf-8")
    n = src.count("is_cancellation(")
    assert n >= 5, f"吞点放行不足: {n} 处（gather+顶层+3 吞点 = 5）"


def test_exploit_gather_checks_results_for_cancellation():
    src = _SRC.read_text(encoding="utf-8")
    assert "return_exceptions=True" in src
    idx = src.index("return_exceptions=True")
    window = src[idx:idx + 800]
    assert "is_cancellation(_r)" in window, "exploit gather 结果缺取消放行检查"
