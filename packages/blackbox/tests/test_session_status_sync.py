"""session-status 同步:blackbox workflow 也要有 except Exception 分支(防御性对齐 whitebox).

blackbox CLI worker.py:201-211 已有 except Exception 兜底(写 session.failed),但 workflow
本身无 except Exception → state.status 不被设为 failed → 兜底 summary 的 status 回落逻辑
依赖 worker 层。本任务让 workflow 本源也设 state.status=failed,对齐 whitebox。
"""
import inspect


def _wf_src() -> str:
    from shannon_blackbox.pipeline import workflows
    return inspect.getsource(workflows)


def test_blackbox_workflow_has_except_exception_branch():
    """blackbox workflow.run 必须有 except Exception 分支(在 except CancelledError 之后)."""
    src = _wf_src()
    assert "except Exception as e:" in src, (
        "blackbox workflow 必须有 'except Exception as e:' 分支(对齐 whitebox)")


def test_blackbox_workflow_except_branch_sets_failed_state():
    """except Exception 分支必须设 state.status='failed' + return self._state(不调 activity)."""
    src = _wf_src()
    ee = src.find("except Exception as e:")
    assert ee != -1
    fin = src.find("finally:", ee)
    branch = src[ee:fin if fin != -1 else len(src)]
    assert '"failed"' in branch or "'failed'" in branch, (
        "except Exception 分支必须设 state.status='failed'")
    assert "return self._state" in branch, (
        "except Exception 分支必须 return self._state(让 CLI worker 拿到 failed state)")
