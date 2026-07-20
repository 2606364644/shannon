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
