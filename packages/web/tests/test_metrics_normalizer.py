"""metrics_normalizer: 归一化 session.json 里 metrics.agents 的两种落盘 schema 到
前端 types.ts SessionMetrics 契约。

背景:metrics.agents 在历史上有两种 schema(同一份 session.json 因写入器版本不同):
- 新(metrics_tracker.end_agent,当前): {duration_ms, cost_usd, success, attempt_number, model[/error]}
- 旧(2026-06 版本): {final_duration_ms, total_cost_usd, status, attempts[][/model/checkpoint]}

前端 OverviewTab AgentTable 读 a.cost_usd.toFixed() 等,旧格式 agent 这些字段全 undefined → 崩。
归一化在 web 层做(对齐 types.ts,前端零改动,core 不动)。
"""
from shannon_web.components.metrics_normalizer import _normalize_agent, normalize_metrics


# === 新格式(NodeGoat,当前 metrics_tracker 产出)===

def test_new_format_passthrough():
    """新格式 agent(duration_ms/cost_usd/cost_currency/success/token...)→ 字段值原样。"""
    a = {"duration_ms": 805974, "cost_usd": 3.74, "cost_currency": "CNY", "success": True,
         "attempt_number": 1, "model": "GLM-5.2[1m]",
         "input_tokens": 1000, "output_tokens": 500, "cache_read_tokens": 100, "cache_creation_tokens": 0}
    out = _normalize_agent(a)
    assert out == {"duration_ms": 805974, "cost_usd": 3.74, "cost_currency": "CNY", "success": True,
                   "attempt_number": 1, "model": "GLM-5.2[1m]",
                   "input_tokens": 1000, "output_tokens": 500, "cache_read_tokens": 100, "cache_creation_tokens": 0}


def test_new_format_with_error_preserved():
    """新格式带 error(重试成功)→ error 保留。"""
    a = {"duration_ms": 434233, "cost_usd": 1.14, "success": True,
         "attempt_number": 2, "model": "GLM-5.2[1m]", "error": "SDK result failure: 429"}
    out = _normalize_agent(a)
    assert out["error"] == "SDK result failure: 429"
    assert out["attempt_number"] == 2


# === 旧格式(juice-shop_whitebox-*,2026-06 旧写入器)===

def test_legacy_format_success():
    """旧格式成功 agent(status=success + attempts[1项])→ 映射到新 schema。"""
    a = {
        "status": "success",
        "attempts": [{"attempt_number": 1, "duration_ms": 1021609, "cost_usd": 8.79,
                      "success": True, "timestamp": "2026-06-04T15:56:50.774Z",
                      "model": "claude-opus-4-7"}],
        "final_duration_ms": 1021609,
        "total_cost_usd": 8.79,
        "model": "claude-opus-4-7",
        "checkpoint": "fd681797",
    }
    out = _normalize_agent(a)
    assert out["duration_ms"] == 1021609      # final_duration_ms
    assert out["cost_usd"] == 8.79            # total_cost_usd
    assert out["success"] is True             # status == "success"
    assert out["attempt_number"] == 1         # attempts[-1].attempt_number
    assert out["model"] == "claude-opus-4-7"
    assert "attempts" not in out and "final_duration_ms" not in out  # 旧 key 不透出


def test_legacy_format_in_progress():
    """旧格式 in-progress agent(status=in-progress)→ success=False(尚未成功)。"""
    a = {
        "status": "in-progress",
        "attempts": [{"attempt_number": 1, "duration_ms": 664096, "cost_usd": 0,
                      "success": False, "timestamp": "...", "model": "GLM-5-Turbo"}],
        "final_duration_ms": 664096,
        "total_cost_usd": 0,
    }
    out = _normalize_agent(a)
    assert out["success"] is False            # status != "success"
    assert out["duration_ms"] == 664096
    assert out["cost_usd"] == 0


def test_legacy_format_missing_model():
    """旧格式缺 model(juice-shop_whitebox-1780512094838 实例)→ model=""。"""
    a = {"status": "success", "attempts": [{"attempt_number": 1, "duration_ms": 100,
          "cost_usd": 1.0, "success": True}], "final_duration_ms": 100, "total_cost_usd": 1.0}
    out = _normalize_agent(a)
    assert out["model"] == ""


def test_legacy_format_empty_attempts():
    """旧格式 attempts=[] → fallback 默认值(duration_ms=0/cost_usd=0/attempt_number=1),
    仍从 status 推 success,不崩。"""
    a = {"status": "success", "attempts": [], "final_duration_ms": 100, "total_cost_usd": 1.0}
    out = _normalize_agent(a)
    assert out["duration_ms"] == 100          # final_duration_ms 仍可用
    assert out["cost_usd"] == 1.0
    assert out["success"] is True
    assert out["attempt_number"] == 1         # attempts 空 → 默认 1
    assert out["model"] == ""


def test_legacy_format_status_failed():
    """status 非 success(failed/in-progress/...)→ success=False。"""
    a = {"status": "failed", "attempts": [{"attempt_number": 2, "duration_ms": 50,
          "cost_usd": 0.5, "success": False}], "final_duration_ms": 50, "total_cost_usd": 0.5}
    out = _normalize_agent(a)
    assert out["success"] is False
    assert out["attempt_number"] == 2


# === 混合 / 边界 ===

def test_mixed_new_key_present_but_null_falls_back():
    """新 key 存在但 None → fallback 旧 key / attempts[-1](防御混合/损坏数据)。"""
    a = {"duration_ms": None, "cost_usd": None, "success": None,
         "final_duration_ms": 999, "total_cost_usd": 2.5,
         "status": "success",
         "attempts": [{"attempt_number": 3, "duration_ms": 999, "cost_usd": 2.5,
                       "success": True, "model": "X"}]}
    out = _normalize_agent(a)
    assert out["duration_ms"] == 999
    assert out["cost_usd"] == 2.5
    assert out["success"] is True
    assert out["attempt_number"] == 3         # attempts[-1]


def test_totally_empty_agent():
    """完全空的 agent dict → 全默认值(cost_currency=USD, token=0),不崩(防御)。"""
    out = _normalize_agent({})
    assert out == {"duration_ms": 0, "cost_usd": 0.0, "cost_currency": "USD", "success": True,
                   "attempt_number": 1, "model": "",
                   "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}


# === normalize_metrics 整体 ===

def test_normalize_metrics_agents_dict():
    """normalize_metrics 对 agents dict 逐项归一化,phases/顶层透传不动。"""
    metrics = {
        "total_duration_ms": 1000,
        "total_cost_usd": 5.0,
        "phases": {"recon": {"duration_ms": 1000, "duration_percentage": 100.0,
                             "cost_usd": 5.0, "agent_count": 1}},
        "agents": {
            "recon": {"duration_ms": 1000, "cost_usd": 5.0, "success": True,
                      "attempt_number": 1, "model": "M"},
            "legacy-agent": {"status": "success", "attempts": [{"attempt_number": 1,
                          "duration_ms": 500, "cost_usd": 2.0, "success": True}],
                          "final_duration_ms": 500, "total_cost_usd": 2.0},
        },
    }
    out = normalize_metrics(metrics)
    assert out["total_duration_ms"] == 1000           # 顶层透传
    assert out["phases"] == metrics["phases"]         # phases 不动
    assert out["agents"]["recon"]["cost_usd"] == 5.0  # 新格式直通
    assert out["agents"]["legacy-agent"]["cost_usd"] == 2.0  # 旧格式归一化
    assert "final_duration_ms" not in out["agents"]["legacy-agent"]


def test_normalize_metrics_top_level_currency_and_tokens():
    """顶层 cost_currency + token 汇总:新 schema 透传,旧 schema 缺失 → 默认(USD/0)。"""
    metrics = {"total_cost_usd": 0.0886, "cost_currency": "CNY",
               "total_input_tokens": 1000, "total_output_tokens": 500,
               "total_cache_read_tokens": 100, "total_cache_creation_tokens": 0,
               "agents": {}}
    out = normalize_metrics(metrics)
    assert out["cost_currency"] == "CNY"
    assert out["total_input_tokens"] == 1000
    assert out["total_cache_read_tokens"] == 100
    # 旧 schema(无 cost_currency/token)→ 默认
    out2 = normalize_metrics({"total_cost_usd": 0.5, "agents": {}})
    assert out2["cost_currency"] == "USD"
    assert out2["total_input_tokens"] == 0


def test_normalize_metrics_empty_or_missing_agents():
    """空 metrics → 原样返回;非空无 agents → 补默认 cost_currency/token,不崩。"""
    assert normalize_metrics({}) == {}
    out = normalize_metrics({"total_cost_usd": 0})
    assert out["total_cost_usd"] == 0
    assert out["cost_currency"] == "USD"  # 缺失 → 默认
    assert normalize_metrics({"agents": {}})["agents"] == {}
