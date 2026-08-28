"""修 0 wiring 检查（spec 2026-08-28-temporal-native-cancel-design）：

WhiteboxScanWorkflow 的 non-fatal 吞点必须放行取消——2026-08-28 幽灵扫描事故
根因：write_agent_poc 的 except Exception 吞掉 ActivityError(cancelled)，workflow
继续跑 9 分钟（T1 探针 test_swallowed_cancel_leaks_running_workflow 钉死机制）。

源码级 grep 模式（对齐 test_worker_registers_gitnexus_verdict 惯例）：真实取消
行为的机制验证在 core/tests/runtime/test_temporal_cancel_propagation.py（真
Temporal server），此处只锁「每个已知吞点都有放行」不被回归删除。
"""
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "supernova_whitebox" / "pipeline" / "workflows.py"


def test_import_is_cancellation():
    src = _SRC.read_text(encoding="utf-8")
    assert "from supernova_core.runtime.temporal_heartbeat import is_cancellation" in src


def test_main_flow_swallow_points_release_cancellation():
    """8 个主流程 non-fatal 吞点 + gather + 顶层，共 ≥10 处 is_cancellation 放行。"""
    src = _SRC.read_text(encoding="utf-8")
    n = src.count("is_cancellation(")
    assert n >= 10, f"主流程吞点放行不足: {n} 处（8 吞点 + gather + 顶层 = 10）"


def test_vuln_gather_checks_results_for_cancellation():
    """vuln gather(return_exceptions=True) 结果含取消 → raise（不放行则继续下一 phase）。"""
    src = _SRC.read_text(encoding="utf-8")
    assert "return_exceptions=True" in src
    # gather 结果检查紧随其后
    idx = src.index("return_exceptions=True")
    window = src[idx:idx + 800]
    assert "is_cancellation(_r)" in window, "gather 结果缺取消放行检查"
