"""Workflow 级 run_timeout —— 整个扫描的 wall-clock 总闸门。

防 token 超标的最外层兜底:内层任一 activity 放大(如 LLM agent 整体超时 × retry)
最终都被它兜住。默认 3h(env SUPERNOVA_WORKFLOW_TIMEOUT_HOURS 可配),覆盖正常扫描
(blackbox ~30min / whitebox ~40-60min)的 ~3-6× 余量,只拦真失控。触发即整个
workflow fail(不可恢复,Temporal 撤销该 run),故值给足余量别误杀慢扫描。

注:AuthValidationWorkflow(认证管理页"测试登录",非扫描)不套此超时。
"""
import os
from datetime import timedelta


def workflow_run_timeout() -> timedelta:
    """整个扫描 workflow 的 run_timeout,默认 3h,env 可配。"""
    return timedelta(hours=int(os.getenv("SUPERNOVA_WORKFLOW_TIMEOUT_HOURS", "3")))
