"""归一化 session.json 里 ``metrics.agents`` 的两种落盘 schema 到前端 ``types.ts``
``SessionMetrics.agents`` 契约。

背景
----
``metrics.agents`` 历史上有两种 schema(不同时期的 metrics 写入器落盘):
- **新**(当前 ``audit/metrics_tracker.py::end_agent``):
  ``{duration_ms, cost_usd, success, attempt_number, model[, error]}``
- **旧**(2026-06 版本写入器,见于 ``juice-shop_whitebox-*`` workspace):
  ``{final_duration_ms, total_cost_usd, status, attempts[][, model, checkpoint]}``

前端 ``OverviewTab.AgentTable`` 读 ``a.cost_usd.toFixed()`` 等,旧格式 agent 这些字段
全 ``undefined`` → ``Cannot read properties of undefined (reading 'toFixed')``。

``SessionManager.get_session_data`` 只 ``json.loads`` 透传,不归一化,故在 web 层补:
对齐 ``types.ts``(前端零改动,core 不动)。phases schema 两种格式一致,不动。
"""
from __future__ import annotations


def _pick(a: dict, last: dict | None, new_key: str, old_key: str | None,
          sub_key: str, default):
    """字段级 fallback:new key(非 None)→ 旧 key(非 None)→ attempts[-1][sub_key](非 None)→ default。

    防御混合 / 损坏数据(某新 key 存在但 None → 继续找旧 key / attempts[-1])。
    """
    if a.get(new_key) is not None:
        return a[new_key]
    if old_key is not None and a.get(old_key) is not None:
        return a[old_key]
    if last is not None and last.get(sub_key) is not None:
        return last[sub_key]
    return default


def _normalize_agent(a: dict) -> dict:
    """归一化单个 agent dict 到 ``SessionMetrics.agents[name]`` schema。

    新格式直通;旧格式(``final_duration_ms`` / ``total_cost_usd`` / ``status`` /
    ``attempts[]``)映射;混合格式按字段级 fallback 兜底。
    """
    attempts = a.get("attempts")
    last = attempts[-1] if isinstance(attempts, list) and attempts else None

    duration_ms = _pick(a, last, "duration_ms", "final_duration_ms", "duration_ms", 0)
    cost_usd = _pick(a, last, "cost_usd", "total_cost_usd", "cost_usd", 0.0)
    attempt_number = _pick(a, last, "attempt_number", None, "attempt_number", 1)
    model = _pick(a, last, "model", "model", "model", "")

    # success:优先新格式 bool success;否则旧格式 status == "success";
    # 否则 attempts[-1].success;默认 True(无信号视为成功,前端按 success 上色)。
    if isinstance(a.get("success"), bool):
        success = a["success"]
    elif "status" in a:
        success = a.get("status") == "success"
    elif last is not None and "success" in last:
        success = bool(last["success"])
    else:
        success = True

    out: dict = {
        "duration_ms": duration_ms,
        "cost_usd": cost_usd,
        "success": success,
        "attempt_number": attempt_number,
        "model": model,
    }
    err = a.get("error") or (last.get("error") if last else None)
    if err:
        out["error"] = err
    return out


def normalize_metrics(metrics: dict) -> dict:
    """归一化整棵 metrics 子树:``agents`` 逐项归一化,其余(顶层 / phases)透传不动。

    空 metrics / 无 agents / agents 非 dict → 原样返回,不崩。
    """
    if not metrics:
        return metrics
    out = dict(metrics)
    agents = metrics.get("agents")
    if isinstance(agents, dict):
        out["agents"] = {name: _normalize_agent(a) for name, a in agents.items()}
    return out
