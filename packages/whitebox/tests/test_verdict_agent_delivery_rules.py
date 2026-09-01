# packages/whitebox/tests/test_verdict_agent_delivery_rules.py
"""交付纪律统一注入（2026-08-28 收口）：write_file 通道错配的治本修复。

背景（2026-08-28 royechen 现场实证）：多轮 verdict agent 把最终 JSON
write_file 进文件交付，宿主只收最终消息（structured_output / text）→
108s 成果全灭 "unparseable output" 0/11。该契约原本散落在每个 prompt 各自
措辞（poc-agent 的 <output_discipline> 是事故后单点补丁、endpoint_enrichment
的 <delivery_rules> 是第二个补丁）——逐 prompt 补 = 无穷无尽。

收口：run_gitnexus_verdict_agent 是全部 JSON 契约多轮 agent 的唯一入口
（endpoint/gn enrich、authz judge/explore、chain verdict、discovery 四模板），
注入条件 = structured_output_schema 非 None（= 有工具的多轮 ∧ 要收 JSON 的
交集）。一处注入，全消费方覆盖，未来新 prompt 自动免疫。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from supernova_whitebox.pipeline.activities import run_gitnexus_verdict_agent


def _fake_result():
    return SimpleNamespace(
        success=True, text="{}", structured_output={}, turns=1, cost=0.0,
        cost_currency="USD", model="test-model", error=None, tokens=None,
    )


def _run(prompt="BASE PROMPT", schema=None):
    with patch("supernova_core.agents.runner.run_claude_prompt",
               new=AsyncMock(return_value=_fake_result())) as mock_run:
        import asyncio
        result = asyncio.new_event_loop().run_until_complete(
            run_gitnexus_verdict_agent(
                prompt=prompt, repo_path="/repo",
                structured_output_schema=schema,
                audit_session=None, agent_name="t-agent"))
    return result, mock_run


def test_delivery_rules_injected_with_schema():
    """有 structured_output_schema（要收 JSON 的多轮 agent）→ prompt 尾部
    注入交付纪律：最终消息即 JSON / 禁止写文件（点名 write_file 与重定向）/
    宿主不读 agent 创建的文件。"""
    _, mock_run = _run(schema={"type": "object"})
    prompt = mock_run.call_args.kwargs["prompt"]
    assert "BASE PROMPT" in prompt                      # 原 prompt 保留
    assert "Your final message must BE the JSON object" in prompt
    assert "Do NOT write the JSON to a file" in prompt
    assert "write_file" in prompt
    assert "never reads files you create" in prompt
    # 注入在尾部（原 prompt 之后），不是头部
    assert prompt.index("BASE PROMPT") < prompt.index("Your final message")


def test_no_injection_without_schema():
    """无 schema（非 JSON 契约路径）→ 不注入，prompt 原样透传。"""
    _, mock_run = _run(schema=None)
    prompt = mock_run.call_args.kwargs["prompt"]
    assert prompt == "BASE PROMPT"


def test_max_turns_fallback_reads_ws_override(monkeypatch):
    """不显式传 max_turns（authz 深判等调用方）→ 回落键
    SUPERNOVA_GITNEXUS_VERDICT_MAX_TURNS 读 ws_getenv：工作区覆盖层优先于
    进程 env（2026-09-01 准入，per-workspace 预算旋钮与 CONCURRENCY 同族）。"""
    from supernova_core.config import scan_env

    monkeypatch.setenv("SUPERNOVA_GITNEXUS_VERDICT_MAX_TURNS", "20")
    scan_env.set_scan_env({"SUPERNOVA_GITNEXUS_VERDICT_MAX_TURNS": "9"})
    try:
        _, mock_run = _run()
        assert mock_run.call_args.kwargs["max_turns"] == 9
    finally:
        scan_env.clear_scan_env()
    _, mock_run = _run()
    assert mock_run.call_args.kwargs["max_turns"] == 20  # 清层 → 回落进程 env
