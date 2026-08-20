"""executor 失败诊断（spec 2026-08-19 §3.2）：

1. structured_output=None 时跳过 queue 写盘必须留 warning（现状零日志）；
2. validate_deliverable 防线 raise 的 PentestError.context 携带 stop_reason /
   文本长度与末尾片段 / structured_output_present / cost（现状只有 agent_name）。
"""
import asyncio

import pytest

from supernova_core.models.errors import PentestError


def _run(coro):
    return asyncio.run(coro)


def _stub_result():
    """成功但无 structured_output 的 result——模拟网关断流后兜底解析失败。"""
    truncated = '{"vulnerabilities": [{"ID": "AUTH-VULN-01"}, {"ID": "AU'

    class _R:
        success = True
        turns = 3
        cost = 0.42
        cost_currency = "CNY"
        text = truncated
        error = None
        retryable = True
        model = "stub-model"
        stop_reason = "end_turn"

        class tokens:
            input_tokens = 100
            output_tokens = 50
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

        structured_output = None

    return _R()


def _setup_executor(tmp_path, monkeypatch):
    from supernova_core.agents import executor as exec_mod
    from supernova_core.models.agents import AGENTS, AgentName
    from supernova_core.prompts.manager import PromptManager

    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # 预置 deliverable md，让 validate_deliverable 走到 queue 检查（validators.py:41 防线）
    defn = AGENTS[AgentName.INJECTION_VULN]
    (deliverables / defn.deliverable_filename).write_text("placeholder", encoding="utf-8")

    async def fake_run(**kw):
        return _stub_result()

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    monkeypatch.setattr(exec_mod.GitManager, "ensure_repository",
                        classmethod(lambda cls, p: asyncio.sleep(0)))
    monkeypatch.setattr(exec_mod.GitManager, "create_checkpoint",
                        lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(exec_mod.GitManager, "commit",
                        lambda *a, **k: asyncio.sleep(0))
    # 隔离 md 渲染（executor.py:20 render_deliverable）：本测试聚焦诊断断言，
    # 不依赖 vuln renderer 对空 collector 的行为
    monkeypatch.setattr(exec_mod, "render_deliverable", lambda *a, **k: None)

    pm = PromptManager.__new__(PromptManager)
    pm.prompts_dir = tmp_path
    monkeypatch.setattr(pm, "load_sync", lambda *a, **k: "PROMPT")

    return exec_mod.AgentExecutor(pm), exec_mod, deliverables


def test_missing_queue_error_carries_diagnostics(tmp_path, monkeypatch):
    ax, exec_mod, deliverables = _setup_executor(tmp_path, monkeypatch)

    with pytest.raises(PentestError) as ei:
        _run(ax.execute(
            agent_name=exec_mod.AgentName.INJECTION_VULN,
            repo_path=str(deliverables), deliverables_path=str(deliverables),
            structured_output_schema={"type": "object"},
        ))

    ctx = ei.value.context
    # 现有键保留（validators 原始 context）
    assert ctx["agent_name"] == "injection-vuln"
    # 诊断增补键（spec §3.2）
    assert ctx["stop_reason"] == "end_turn"
    assert ctx["collected_text_len"] == len(_stub_result().text)
    assert ctx["collected_text_tail"].endswith('{"ID": "AU')
    assert ctx["structured_output_present"] is False
    # cost/tokens 合并（_result_cost_context 字段）
    assert ctx["cost_usd"] == 0.42
    assert ctx["input_tokens"] == 100


def test_skipped_queue_write_logs_warning(tmp_path, monkeypatch, caplog):
    """structured_output=None 跳过写盘必须留 warning（现状零日志，排障靠猜）。"""
    import logging
    ax, exec_mod, deliverables = _setup_executor(tmp_path, monkeypatch)

    with caplog.at_level(logging.WARNING, logger="supernova_core.agents.executor"):
        with pytest.raises(PentestError):  # validate 防线照常 raise（另一断言覆盖）
            _run(ax.execute(
                agent_name=exec_mod.AgentName.INJECTION_VULN,
                repo_path=str(deliverables), deliverables_path=str(deliverables),
                structured_output_schema={"type": "object"},
            ))

    warnings = [r for r in caplog.records
                if "NOT written" in r.getMessage()]
    assert len(warnings) == 1
    assert "injection_exploitation_queue.json" in warnings[0].getMessage()
