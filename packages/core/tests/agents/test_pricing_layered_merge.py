"""定价分层合并（spec 2026-08-28 global-pricing-console §4.1）。

优先级链：``内置 BUILTIN_PRICING_CNY < process 层（SUPERNOVA_PRICING_OVERRIDE，
per-profile）< 全局层（SUPERNOVA_GLOBAL_PRICING，web 管理）< 工作区层（ws 覆盖
SUPERNOVA_PRICING_OVERRIDE，最高）``。币种 = 最高优先非空层的 currency（缺省/空串
回落 CNY）。

兼容不变量（锚点 1）：未设 GLOBAL 键且无 ws 覆盖时，输出与拆层前
（BUILTIN ∪ ws_getenv 单层混合）逐项等价。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from supernova_core.config import scan_env
from supernova_core.config.scan_env import clear_scan_env, set_scan_env, ws_override_get
from supernova_core.agents.pricing import BUILTIN_PRICING_CNY, _pricing, compute_cost


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """隔离三层：清 ws 覆盖层 + 删两个 env 键（防外部环境串台）。"""
    scan_env._SCAN_ENV.clear()
    monkeypatch.delenv("SUPERNOVA_PRICING_OVERRIDE", raising=False)
    monkeypatch.delenv("SUPERNOVA_GLOBAL_PRICING", raising=False)
    yield
    scan_env._SCAN_ENV.clear()


def _usage(input_tokens: int) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens, output_tokens=0,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )


def _write_new_schema(dir, name: str, currency: str, models: dict) -> str:
    """写新 schema 文件 {"currency","models"}，返回路径。"""
    p = dir / name
    p.write_text(json.dumps({"currency": currency, "models": models}), encoding="utf-8")
    return str(p)


def _write_flat(dir, name: str, models: dict) -> str:
    """写旧 flat schema 文件 {model:{...}}，返回路径。"""
    p = dir / name
    p.write_text(json.dumps(models), encoding="utf-8")
    return str(p)


_M = lambda i: {"input": i, "output": 0.0, "cache_read": 0.0, "cache_creation": 0.0}  # noqa: E731


# ---- 锚点 1：兼容等价（未设 GLOBAL 且无 ws 覆盖 → 与拆层前逐项等价）----


def test_no_override_at_all_equivalent_to_builtin():
    """无任何 override → (BUILTIN 表, CNY)，逐项等价。"""
    table, currency = _pricing()
    assert table == BUILTIN_PRICING_CNY
    assert currency == "CNY"


def test_process_layer_only_new_schema(tmp_path, monkeypatch):
    """键只在 os.environ（新 schema）→ process 文件生效（现状 ws_getenv 回落语义）。"""
    path = _write_new_schema(tmp_path, "p.json", "USD", {"glm-5.2": _M(10.0)})
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", path)
    table, currency = _pricing()
    assert currency == "USD"
    assert table["glm-5.2"] == _M(10.0)
    assert table["glm-4.5-air"] == BUILTIN_PRICING_CNY["glm-4.5-air"]  # 其余内置保留


def test_process_layer_only_flat_schema(tmp_path, monkeypatch):
    """键只在 os.environ（旧 flat schema）→ 覆盖内置同 key、币种回落 CNY。"""
    path = _write_flat(tmp_path, "p.json", {"glm-4.5-air": {"input": 100.0, "output": 1.0, "cache_read": 0.5}})
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", path)
    table, currency = _pricing()
    assert currency == "CNY"
    assert table["glm-4.5-air"]["input"] == 100.0


def test_ws_layer_only_equivalent_to_legacy(tmp_path):
    """键只在 ws 覆盖层（os.environ 无键）→ ws 文件生效（现状 ws_getenv 覆盖语义）。"""
    path = _write_new_schema(tmp_path, "ws.json", "CNY", {"ws-only-model": _M(7.0)})
    set_scan_env({"SUPERNOVA_PRICING_OVERRIDE": path})
    table, currency = _pricing()
    assert currency == "CNY"
    assert table["ws-only-model"] == _M(7.0)


def test_both_layers_ws_wins_same_model(tmp_path, monkeypatch):
    """process + ws 两层都有：同模型取 ws 价（现状「ws 有键即 ws 文件生效」对齐点）。"""
    pa = _write_new_schema(tmp_path, "p.json", "CNY", {"both-model": _M(1.0)})
    pb = _write_new_schema(tmp_path, "ws.json", "CNY", {"both-model": _M(2.0)})
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", pa)
    set_scan_env({"SUPERNOVA_PRICING_OVERRIDE": pb})
    table, _ = _pricing()
    assert table["both-model"] == _M(2.0)
    # 分层合并新语义（spec 设计意图）：process 层未被 ws 覆盖的模型保留进表，
    # 区别于拆层前的整表替换——这是全局表快照收编后 ws 选择性覆盖的前提。
    assert table.get("proc-extra-model") is None  # 本例 process 文件只有 both-model


# ---- 锚点 2：GLOBAL 层压过 process 层（接管语义）----


def test_global_layer_overrides_process(tmp_path, monkeypatch):
    """GLOBAL 同模型压过 process 价；GLOBAL 补 process 没有的模型也进表。"""
    pa = _write_new_schema(tmp_path, "p.json", "CNY", {"m-a": _M(1.0), "m-b": _M(2.0)})
    pg = _write_new_schema(tmp_path, "g.json", "CNY", {"m-a": _M(9.0), "m-c": _M(3.0)})
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", pa)
    monkeypatch.setenv("SUPERNOVA_GLOBAL_PRICING", pg)
    table, _ = _pricing()
    assert table["m-a"] == _M(9.0)  # GLOBAL 压过 process
    assert table["m-b"] == _M(2.0)  # process 层未被覆盖的模型保留
    assert table["m-c"] == _M(3.0)  # GLOBAL 补充的模型进表


# ---- 锚点 3：ws 覆盖层压过 GLOBAL 层 ----


def test_ws_layer_overrides_global(tmp_path, monkeypatch):
    pg = _write_new_schema(tmp_path, "g.json", "CNY", {"m-a": _M(5.0), "m-b": _M(5.0)})
    pw = _write_new_schema(tmp_path, "ws.json", "CNY", {"m-a": _M(1.0)})
    monkeypatch.setenv("SUPERNOVA_GLOBAL_PRICING", pg)
    set_scan_env({"SUPERNOVA_PRICING_OVERRIDE": pw})
    table, _ = _pricing()
    assert table["m-a"] == _M(1.0)  # ws 压过 GLOBAL
    assert table["m-b"] == _M(5.0)  # GLOBAL 未被 ws 覆盖的模型保留
    # 经公开 API 抽查行为语义：compute_cost 用 ws 价
    assert compute_cost("m-a", _usage(1_000_000)).cost == pytest.approx(1.0)


# ---- 锚点 4：币种 = 最高优先非空层 currency ----


def test_currency_from_highest_nonempty_layer(tmp_path, monkeypatch):
    pg = _write_new_schema(tmp_path, "g.json", "USD", {"m-a": _M(1.0)})
    pw = _write_new_schema(tmp_path, "ws.json", "CNY", {"m-a": _M(2.0)})
    monkeypatch.setenv("SUPERNOVA_GLOBAL_PRICING", pg)
    set_scan_env({"SUPERNOVA_PRICING_OVERRIDE": pw})
    _, currency = _pricing()
    assert currency == "CNY"  # ws 层（最高非空）压过 GLOBAL 的 USD


def test_currency_falls_back_cny_when_all_empty():
    _, currency = _pricing()
    assert currency == "CNY"


def test_currency_empty_string_falls_back_cny(tmp_path, monkeypatch):
    """currency 为空串 → 回落 CNY（不产出空币种）。"""
    pg = _write_new_schema(tmp_path, "g.json", "", {"m-a": _M(1.0)})
    monkeypatch.setenv("SUPERNOVA_GLOBAL_PRICING", pg)
    _, currency = _pricing()
    assert currency == "CNY"


# ---- 锚点 5：ws_override_get 三态（只读覆盖层、不回落 os.environ）----


def test_ws_override_get_present_in_layer(monkeypatch):
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", "/from/os/environ")
    set_scan_env({"SUPERNOVA_PRICING_OVERRIDE": "/from/ws/layer"})
    assert ws_override_get("SUPERNOVA_PRICING_OVERRIDE") == "/from/ws/layer"


def test_ws_override_get_key_absent_from_layer(monkeypatch):
    """覆盖层存在但无该键 → None（不回落 os.environ 的同名键）。"""
    monkeypatch.setenv("SOME_OTHER_KEY", "x")
    set_scan_env({"SUPERNOVA_PRICING_OVERRIDE": "/ws"})
    assert ws_override_get("SUPERNOVA_LLM_TRACK_ENABLED") is None


def test_ws_override_get_no_layer(monkeypatch):
    """无覆盖层 → None；即使 os.environ 有同名键也不回落。"""
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", "/from/os/environ")
    clear_scan_env()
    assert ws_override_get("SUPERNOVA_PRICING_OVERRIDE") is None


# ---- 锚点 6：GLOBAL 路径文件损坏 → 该层视为空、不抛、其余层照常 ----


def test_global_corrupt_json_ignored(tmp_path, monkeypatch):
    """GLOBAL 非法 JSON → 层空 + 不抛；process 层照常生效。"""
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    pa = _write_new_schema(tmp_path, "p.json", "CNY", {"m-a": _M(4.0)})
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", pa)
    monkeypatch.setenv("SUPERNOVA_GLOBAL_PRICING", str(bad))
    table, currency = _pricing()
    assert table["m-a"] == _M(4.0)  # process 层照常
    assert currency == "CNY"


def test_global_non_dict_top_level_ignored(tmp_path, monkeypatch):
    """GLOBAL 顶层非 dict → 层空 + 不抛 + 内置表兜底。"""
    bad = tmp_path / "arr.json"
    bad.write_text("[1,2,3]", encoding="utf-8")
    monkeypatch.setenv("SUPERNOVA_GLOBAL_PRICING", str(bad))
    table, _ = _pricing()
    assert table == BUILTIN_PRICING_CNY
