"""model_caps: 模型 context window 配置层 + chunk threshold 派生(spec 2026-07-10)。

复用 pricing.normalize_model 归一化模型名; context 来源优先级:
override JSON(SUPERNOVA_MODEL_CONTEXT_OVERRIDE) > 内置表 > DEFAULT_CONTEXT_WINDOW。
threshold = context × CHUNK_RESERVE_RATIO(留 25% 给 output+system prompt+估算误差)。
"""


def test_builtin_context_table_has_glm():
    from supernova_core.agents.model_caps import MODEL_CONTEXT_WINDOWS

    assert MODEL_CONTEXT_WINDOWS.get("glm-5.2") == 1_000_000
    assert MODEL_CONTEXT_WINDOWS.get("glm-4.5-air") == 128_000


def test_get_context_window_builtin_model():
    from supernova_core.agents.model_caps import get_model_context_window

    assert get_model_context_window("glm-5.2") == 1_000_000


def test_get_context_window_normalizes_model():
    """带后缀 [1m] / 大小写都归一化到 glm-5.2。"""
    from supernova_core.agents.model_caps import get_model_context_window

    assert get_model_context_window("GLM-5.2[1m]") == 1_000_000


def test_get_context_window_unknown_model_falls_back_default():
    from supernova_core.agents.model_caps import DEFAULT_CONTEXT_WINDOW, get_model_context_window

    assert get_model_context_window("some-unknown-model") == DEFAULT_CONTEXT_WINDOW
    assert DEFAULT_CONTEXT_WINDOW == 128_000


def test_get_context_window_none_falls_back_default():
    from supernova_core.agents.model_caps import DEFAULT_CONTEXT_WINDOW, get_model_context_window

    assert get_model_context_window(None) == DEFAULT_CONTEXT_WINDOW


def test_get_chunk_token_threshold_derives_from_context():
    """threshold = context × 0.75。"""
    from supernova_core.agents.model_caps import CHUNK_RESERVE_RATIO, get_chunk_token_threshold

    assert get_chunk_token_threshold("glm-5.2") == int(1_000_000 * CHUNK_RESERVE_RATIO)
    assert get_chunk_token_threshold("glm-5.2") == 750_000


def test_get_chunk_token_threshold_default_model():
    from supernova_core.agents.model_caps import (
        DEFAULT_CONTEXT_WINDOW,
        get_chunk_token_threshold,
    )

    assert get_chunk_token_threshold("unknown") == int(DEFAULT_CONTEXT_WINDOW * 0.75)
    assert get_chunk_token_threshold(None) == int(DEFAULT_CONTEXT_WINDOW * 0.75)


def test_override_json_overrides_builtin(tmp_path, monkeypatch):
    """SUPERNOVA_MODEL_CONTEXT_OVERRIDE JSON: {"models": {model: ctx}} 覆盖内置表。"""
    from supernova_core.agents.model_caps import (
        CHUNK_RESERVE_RATIO,
        get_chunk_token_threshold,
        get_model_context_window,
    )

    f = tmp_path / "caps.json"
    f.write_text('{"models": {"glm-5.2": 500000}}', encoding="utf-8")
    monkeypatch.setenv("SUPERNOVA_MODEL_CONTEXT_OVERRIDE", str(f))
    assert get_model_context_window("glm-5.2") == 500_000
    assert get_chunk_token_threshold("glm-5.2") == int(500_000 * CHUNK_RESERVE_RATIO)


def test_override_json_adds_new_model(tmp_path, monkeypatch):
    """override 可补充内置表没有的模型。"""
    from supernova_core.agents.model_caps import get_model_context_window

    f = tmp_path / "caps.json"
    f.write_text('{"models": {"custom-model": 200000}}', encoding="utf-8")
    monkeypatch.setenv("SUPERNOVA_MODEL_CONTEXT_OVERRIDE", str(f))
    assert get_model_context_window("custom-model") == 200_000


def test_override_invalid_ignored(tmp_path, monkeypatch):
    """override JSON 解析失败 -> 忽略覆盖、用内置表、不崩(spec 容错契约)。"""
    from supernova_core.agents.model_caps import get_model_context_window

    f = tmp_path / "bad.json"
    f.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("SUPERNOVA_MODEL_CONTEXT_OVERRIDE", str(f))
    assert get_model_context_window("glm-5.2") == 1_000_000  # 回落内置


def test_override_non_object_top_ignored(tmp_path, monkeypatch):
    """override 顶层非 object / models 非 dict -> 忽略、不崩。"""
    from supernova_core.agents.model_caps import get_model_context_window

    f = tmp_path / "bad2.json"
    f.write_text('["not", "an", "object"]', encoding="utf-8")
    monkeypatch.setenv("SUPERNOVA_MODEL_CONTEXT_OVERRIDE", str(f))
    assert get_model_context_window("glm-5.2") == 1_000_000


def test_hard_override_env_takes_precedence(monkeypatch):
    """SUPERNOVA_CHUNK_TOKEN_THRESHOLD(hard override)> 派生值, 跳过 context 计算。"""
    from supernova_core.agents.model_caps import get_chunk_token_threshold

    monkeypatch.setenv("SUPERNOVA_CHUNK_TOKEN_THRESHOLD", "50000")
    assert get_chunk_token_threshold("glm-5.2") == 50_000


def test_hard_override_env_invalid_falls_back(monkeypatch):
    """SUPERNOVA_CHUNK_TOKEN_THRESHOLD 畸形(非 int)<=0 -> 回落派生值, 不崩。"""
    from supernova_core.agents.model_caps import get_chunk_token_threshold

    monkeypatch.setenv("SUPERNOVA_CHUNK_TOKEN_THRESHOLD", "not-a-number")
    assert get_chunk_token_threshold("glm-5.2") == int(1_000_000 * 0.75)


def test_hard_override_env_zero_or_negative_falls_back(monkeypatch):
    from supernova_core.agents.model_caps import get_chunk_token_threshold

    monkeypatch.setenv("SUPERNOVA_CHUNK_TOKEN_THRESHOLD", "0")
    assert get_chunk_token_threshold("glm-5.2") == int(1_000_000 * 0.75)
    monkeypatch.setenv("SUPERNOVA_CHUNK_TOKEN_THRESHOLD", "-5")
    assert get_chunk_token_threshold("glm-5.2") == int(1_000_000 * 0.75)
