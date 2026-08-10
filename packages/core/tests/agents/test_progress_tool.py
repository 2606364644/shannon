"""Tests for supernova_core.agents.progress_tool — log_milestone 里程碑工具。

agent 在登录各阶段调 log_milestone → handler 经 session.log_step 发 StepEvent(complete)
→ 前端步骤条推进。双引擎（openai FunctionTool / claude SdkMcpTool）对称。
"""
import json

import pytest


# ---------------------------------------------------------------------------
# recording session stub —— 镜像 AuditSession.log_step 签名，记录调用
# ---------------------------------------------------------------------------


class _RecordingSession:
    def __init__(self):
        self.log_step_calls = []

    async def log_step(self, name, phase, event, duration_ms=None,
                       error=None, intent=None):
        self.log_step_calls.append({
            "name": name, "phase": phase, "event": event,
            "duration_ms": duration_ms, "error": error, "intent": intent,
        })


# ---------------------------------------------------------------------------
# emit_milestone —— 纯核心逻辑（builder 包一层 get_audit_session）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_milestone_valid_step_logs_step_complete_with_intent():
    from supernova_core.agents.progress_tool import (
        AUTH_VALIDATION_PROGRESS, emit_milestone,
    )

    session = _RecordingSession()
    result = await emit_milestone("navigate", AUTH_VALIDATION_PROGRESS, session)

    # 发了一条 StepEvent(complete)，step_key / phase / intent 正确
    assert len(session.log_step_calls) == 1
    call = session.log_step_calls[0]
    assert call["name"] == "navigate"
    assert call["phase"] == "auth-validation"
    assert call["event"] == "complete"
    assert call["intent"] == "导航到登录页"
    # 返回确认串
    assert "navigate" in result


@pytest.mark.asyncio
async def test_emit_milestone_invalid_step_returns_error_and_logs_nothing():
    from supernova_core.agents.progress_tool import (
        AUTH_VALIDATION_PROGRESS, emit_milestone,
    )

    session = _RecordingSession()
    result = await emit_milestone("bogus", AUTH_VALIDATION_PROGRESS, session)

    assert session.log_step_calls == []          # 不发事件
    assert "unknown step" in result              # 返错误串
    assert "navigate" in result                  # 提示合法值


# ---------------------------------------------------------------------------
# openai builder —— build_openai_progress_tool(spec) -> FunctionTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_progress_tool_name_and_schema():
    from supernova_core.agents.progress_tool import (
        AUTH_VALIDATION_PROGRESS, build_openai_progress_tool,
    )

    tool = build_openai_progress_tool(AUTH_VALIDATION_PROGRESS)
    assert tool.name == "log_milestone"
    schema = tool.params_json_schema
    assert schema["properties"]["step"]["enum"] == ["navigate", "fill_credentials",
                                                     "submit", "verify_session"]


@pytest.mark.asyncio
async def test_openai_progress_tool_invocation_emits_step_event():
    from agents import RunContextWrapper

    from supernova_core.agents.progress_tool import (
        AUTH_VALIDATION_PROGRESS, build_openai_progress_tool,
    )
    from supernova_core.audit.session_registry import (
        clear_audit_session, get_audit_session, set_audit_session,
    )

    session = _RecordingSession()
    set_audit_session(session)                   # 注册当前 run 的 session（_cli 兜底）
    try:
        tool = build_openai_progress_tool(AUTH_VALIDATION_PROGRESS)
        assert get_audit_session() is session    # 注册生效
        result = await tool.on_invoke_tool(
            RunContextWrapper(context=None), json.dumps({"step": "submit"}))
    finally:
        clear_audit_session()

    assert "submit" in str(result)
    assert len(session.log_step_calls) == 1
    assert session.log_step_calls[0]["name"] == "submit"
    assert session.log_step_calls[0]["event"] == "complete"


@pytest.mark.asyncio
async def test_openai_progress_tool_invalid_json_arguments_returns_error():
    from agents import RunContextWrapper

    from supernova_core.agents.progress_tool import (
        AUTH_VALIDATION_PROGRESS, build_openai_progress_tool,
    )
    from supernova_core.audit.session_registry import (
        clear_audit_session, set_audit_session,
    )

    session = _RecordingSession()
    set_audit_session(session)
    try:
        tool = build_openai_progress_tool(AUTH_VALIDATION_PROGRESS)
        result = await tool.on_invoke_tool(RunContextWrapper(context=None), '{"step":')
    finally:
        clear_audit_session()

    assert "not valid JSON" in str(result)       # 对齐 collector bridge：不静默兜底
    assert session.log_step_calls == []


@pytest.mark.asyncio
async def test_openai_progress_tool_no_registered_session_does_not_crash():
    """无 session（NullAuditSession）时 handler 不崩、no-op。"""
    from agents import RunContextWrapper

    from supernova_core.agents.progress_tool import (
        AUTH_VALIDATION_PROGRESS, build_openai_progress_tool,
    )
    from supernova_core.audit.session_registry import clear_audit_session

    clear_audit_session()                        # 确保无注册 session
    tool = build_openai_progress_tool(AUTH_VALIDATION_PROGRESS)
    result = await tool.on_invoke_tool(
        RunContextWrapper(context=None), json.dumps({"step": "navigate"}))
    assert "navigate" in str(result)             # 正常返回，不抛


# ---------------------------------------------------------------------------
# claude builder —— build_claude_progress_server + _make_progress_sdk_tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claude_progress_server_is_in_process_sdk_config():
    from supernova_core.agents.progress_tool import (
        AUTH_VALIDATION_PROGRESS, build_claude_progress_server,
    )

    server = build_claude_progress_server(AUTH_VALIDATION_PROGRESS)
    assert server["type"] == "sdk"
    assert server["name"] == "supernova-progress"


@pytest.mark.asyncio
async def test_claude_progress_sdk_tool_handler_emits_step_event():
    from supernova_core.agents.progress_tool import (
        AUTH_VALIDATION_PROGRESS, _make_progress_sdk_tool,
    )
    from supernova_core.audit.session_registry import (
        clear_audit_session, set_audit_session,
    )

    session = _RecordingSession()
    set_audit_session(session)
    try:
        sdk_tool = _make_progress_sdk_tool(AUTH_VALIDATION_PROGRESS)
        assert sdk_tool.name == "log_milestone"
        res = await sdk_tool.handler({"step": "verify_session"})
    finally:
        clear_audit_session()

    assert res["content"][0]["type"] == "text"
    assert "verify_session" in res["content"][0]["text"]
    assert res.get("is_error") is not True
    assert len(session.log_step_calls) == 1
    assert session.log_step_calls[0]["name"] == "verify_session"


@pytest.mark.asyncio
async def test_claude_progress_sdk_tool_invalid_step_is_error_envelope():
    from supernova_core.agents.progress_tool import (
        AUTH_VALIDATION_PROGRESS, _make_progress_sdk_tool,
    )
    from supernova_core.audit.session_registry import (
        clear_audit_session, set_audit_session,
    )

    session = _RecordingSession()
    set_audit_session(session)
    try:
        sdk_tool = _make_progress_sdk_tool(AUTH_VALIDATION_PROGRESS)
        res = await sdk_tool.handler({"step": "bogus"})
    finally:
        clear_audit_session()

    assert res.get("is_error") is True
    assert session.log_step_calls == []


# ---------------------------------------------------------------------------
# make_progress 工厂 —— 按 agent_name 分发 ProgressSpec（镜像 make_collector）
# ---------------------------------------------------------------------------


def test_make_progress_validate_auth_returns_spec():
    from supernova_core.agents.progress_tool import (
        AUTH_VALIDATION_PROGRESS, make_progress,
    )
    from supernova_core.models.agents import AgentName

    assert make_progress(AgentName.VALIDATE_AUTH) is AUTH_VALIDATION_PROGRESS


def test_make_progress_other_agents_return_none():
    from supernova_core.agents.progress_tool import make_progress
    from supernova_core.models.agents import AgentName

    assert make_progress(AgentName.RECON) is None
    assert make_progress(AgentName.PRE_RECON) is None


# ---------------------------------------------------------------------------
# compose helper —— 合并 collector 工具 + progress 工具（provider.call 委托）
# ---------------------------------------------------------------------------


def test_compose_openai_extra_tools_progress_only():
    from supernova_core.agents.progress_tool import (
        AUTH_VALIDATION_PROGRESS, compose_openai_extra_tools,
    )

    tools = compose_openai_extra_tools(None, AUTH_VALIDATION_PROGRESS)
    assert [t.name for t in tools] == ["log_milestone"]


def test_compose_openai_extra_tools_none_returns_empty():
    from supernova_core.agents.progress_tool import compose_openai_extra_tools

    assert compose_openai_extra_tools(None, None) == []


def test_compose_openai_extra_tools_collector_plus_progress():
    from supernova_core.collectors.base import CollectorBase, SectionSchema
    from supernova_core.agents.progress_tool import (
        AUTH_VALIDATION_PROGRESS, compose_openai_extra_tools,
    )

    coll = CollectorBase([SectionSchema(
        tool_name="set_alpha", section_key="alpha", description="d",
        json_schema={"type": "object", "properties": {"x": {"type": "string"}},
                     "required": ["x"]})])
    tools = compose_openai_extra_tools(coll, AUTH_VALIDATION_PROGRESS)
    names = [t.name for t in tools]
    assert names[:1] == ["set_alpha"]          # collector 工具在前
    assert "log_milestone" in names            # progress 追加，不丢 collector


def test_compose_claude_mcp_progress_only():
    from supernova_core.agents.progress_tool import (
        AUTH_VALIDATION_PROGRESS, compose_claude_mcp,
    )

    servers, allowed = compose_claude_mcp(None, AUTH_VALIDATION_PROGRESS)
    assert "supernova-progress" in servers
    assert servers["supernova-progress"]["type"] == "sdk"
    assert allowed == ["log_milestone"]


def test_compose_claude_mcp_none_returns_empty():
    from supernova_core.agents.progress_tool import compose_claude_mcp

    servers, allowed = compose_claude_mcp(None, None)
    assert servers == {}
    assert allowed == []


def test_compose_claude_mcp_collector_plus_progress():
    from supernova_core.collectors.base import CollectorBase, SectionSchema
    from supernova_core.agents.progress_tool import (
        AUTH_VALIDATION_PROGRESS, compose_claude_mcp,
    )

    coll = CollectorBase([SectionSchema(
        tool_name="set_alpha", section_key="alpha", description="d",
        json_schema={"type": "object", "properties": {"x": {"type": "string"}},
                     "required": ["x"]})])
    servers, allowed = compose_claude_mcp(coll, AUTH_VALIDATION_PROGRESS)
    assert "shannon-collector" in servers      # collector server
    assert "supernova-progress" in servers     # progress server
    assert allowed == ["set_alpha", "log_milestone"]

