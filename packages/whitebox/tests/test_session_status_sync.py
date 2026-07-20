"""session-status 同步回归锚点:workflow FAILED 时必须经 finalize_summary 写 session.status=failed.

根因:WhiteboxScanWorkflow.run 旧版无 except Exception 分支,workflow raise(如 GitNexus
fail-fast ApplicationFailure / activity retry 耗尽)→ finalize_summary 永不被调 →
session.json status 永远 running → Web 幽灵卡住。
"""
import inspect
from pathlib import Path


def _wf_src() -> str:
    from shannon_whitebox.pipeline import workflows
    return inspect.getsource(workflows)


def test_workflow_has_except_exception_branch():
    """workflow.run 必须有 except Exception 分支(在 except CancelledError 之后、finally 之前)."""
    src = _wf_src()
    assert "except Exception as e:" in src, (
        "workflow 必须有 'except Exception as e:' 分支捕获 workflow-level 失败")
    ce = src.find("except CancelledError")
    ee = src.find("except Exception as e:")
    fin = src.find("finally:", ee)
    assert ce != -1 and ee != -1, "CancelledError 与 Exception 分支都应存在"
    assert ce < ee, (
        f"except Exception 必须在 except CancelledError 之后(通用分支在后), "
        f"实际 CancelledError={ce}, Exception={ee}")


def test_workflow_except_exception_calls_finalize_summary():
    """except Exception 分支(worker_path)必须调 finalize_summary 写 failed 终态."""
    src = _wf_src()
    ee = src.find("except Exception as e:")
    assert ee != -1, "先要有 except Exception 分支(test_workflow_has_except_exception_branch)"
    # 从 except Exception 到 finally 之间的片段
    fin = src.find("finally:", ee)
    branch = src[ee:fin if fin != -1 else len(src)]
    assert "finalize_summary" in branch, (
        "except Exception 分支(worker_path)必须调 activities.finalize_summary 写 session.status=failed")
    assert '"failed"' in branch or "'failed'" in branch, (
        "except Exception 分支构造的 summary status 必须是 'failed'")


def test_workflow_except_exception_reraises():
    """except Exception 分支末尾必须 raise(让 Temporal 标 FAILED,供 web _watch describe 兜底)."""
    src = _wf_src()
    ee = src.find("except Exception as e:")
    fin = src.find("finally:", ee)
    branch = src[ee:fin if fin != -1 else len(src)]
    # 分支内必含裸 raise(重抛捕获的异常)
    assert "\n        raise\n" in branch or "\n            raise\n" in branch, (
        "except Exception 分支末尾必须裸 raise,让 workflow FAILED 供 web describe 兜底")


def test_worker_has_except_exception_after_scancancelled():
    """whitebox CLI worker.py 内层 try 必须有 except Exception 兜底(抄 blackbox worker.py:201-211)."""
    worker = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/worker.py"
    src = worker.read_text()
    sc = src.find("except ScanCancelled:")
    assert sc != -1, "worker.py 须有 except ScanCancelled"
    ee = src.find("except Exception as e:", sc)
    assert ee != -1 and ee < src.find("finally:", sc), (
        "except Exception 必须紧跟 except ScanCancelled 之后(在 finally 之前),抄 blackbox 兜底模式")


def test_worker_except_branch_logs_failed_summary():
    """except Exception 分支必须调 session.log_workflow_complete 写 failed summary."""
    worker = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/worker.py"
    src = worker.read_text()
    sc = src.find("except ScanCancelled:")
    ee = src.find("except Exception as e:", sc)
    fin = src.find("finally:", ee)
    branch = src[ee:fin]
    assert "log_workflow_complete" in branch, (
        "except Exception 分支必须调 session.log_workflow_complete 落盘 failed 终态")
    assert "_build_final_summary" in branch, (
        "必须经 _build_final_summary 构造 summary(DRY,复用 cost/duration 数据源)")


def test_worker_scancancelled_logs_cancelled_summary():
    """except ScanCancelled 分支也必须落盘 cancelled(原版只 return,session 永远 running)."""
    worker = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/worker.py"
    src = worker.read_text()
    sc = src.find("except ScanCancelled:")
    ee = src.find("except Exception as e:", sc)
    branch = src[sc:ee]
    assert "log_workflow_complete" in branch, (
        "except ScanCancelled 必须调 session.log_workflow_complete 落盘 cancelled 终态")


def test_worker_imports_pipeline_state():
    """worker.py 必须能构造 PipelineState(failed/cancelled summary 需要它)."""
    worker = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/worker.py"
    src = worker.read_text()
    assert "PipelineState" in src, (
        "worker.py 必须引用 PipelineState(构造 failed/cancelled state 传给 _build_final_summary)")
