"""MrScanWorkflow 失败收尾回归锚点（2026-09-04 shorturl MR !99 事故）。

根因：MrScanWorkflow.run 旧版无 try/except——前置 activity（run_mr_repo_prepare
的 PentestError，如「源分支已删除 → git rev-parse 失败」）直接上抛 → workflow
FAILED 零落盘 → 用户等 15s 才见 web _watch 兜底的零信息 "workflow FAILED"。

对齐 WhiteboxScanWorkflow 三段收尾（正常 / CancelledError / Exception），锚点式
源码检查（inspect.getsource 子串/顺序断言，对齐 test_session_status_sync.py 模式；
WorkflowEnvironment 在本机有预存挂起，不引入挂起测试）。
"""
import inspect


def _mr_wf_src() -> str:
    from supernova_whitebox.pipeline.workflows import MrScanWorkflow
    return inspect.getsource(MrScanWorkflow)


def test_mr_run_has_three_branch_try_structure():
    """run 必须有 try 包住主体 + except CancelledError + except Exception（顺序 CE < EE）."""
    src = _mr_wf_src()
    run_at = src.find("async def run")
    assert run_at != -1, "MrScanWorkflow 须有 run"
    body = src[run_at:]
    tr = body.find("try:")
    ce = body.find("except CancelledError")
    ee = body.find("except Exception as e:")
    assert tr != -1, "run 主体须有 try（前置 activity 失败要有收尾点）"
    assert ce != -1 and ee != -1, "CancelledError 与 Exception 分支都应存在"
    assert tr < ce < ee, (
        f"结构须为 try → except CancelledError → except Exception，"
        f"实际 try={tr}, CancelledError={ce}, Exception={ee}")


def test_mr_except_exception_finalizes_failed():
    """except Exception 分支(worker_path)必须调 finalize_summary 写 failed 终态."""
    src = _mr_wf_src()
    ee = src.find("except Exception as e:")
    assert ee != -1
    branch = src[ee:]
    assert "finalize_summary" in branch, (
        "except Exception 分支(worker_path)必须调 activities.finalize_summary "
        "写 scan_end 带真实错误 + session.status=failed（否则用户只见 15s 后兜底的"
        "零信息 workflow FAILED）")
    assert '"failed"' in branch or "'failed'" in branch, (
        "except Exception 分支须设 state.status='failed'")
    assert "errors" in branch, (
        "except Exception 分支须记录异常到 state.errors（summary error 字段的数据源）")


def test_mr_except_exception_reraises():
    """except Exception 分支末尾必须裸 raise（Temporal 标 FAILED 供 web _watch 兜底）."""
    src = _mr_wf_src()
    ee = src.find("except Exception as e:")
    branch = src[ee:]
    assert "\n        raise\n" in branch or "\n            raise\n" in branch, (
        "except Exception 分支末尾必须裸 raise")


def test_mr_worker_path_gate():
    """finalize 收尾须按 is_worker_path 门控（CLI 路径 event_file=None 由外层收尾）."""
    src = _mr_wf_src()
    assert "is_worker_path" in src, (
        "须有 is_worker_path 门控（event_file 非 None = web 提交才走 finalize_summary）")
