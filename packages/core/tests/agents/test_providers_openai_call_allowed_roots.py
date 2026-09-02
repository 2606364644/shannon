"""call() 不传 allowed_roots（默认 None）不得崩：ToolContext None 迭代回归。

回归 2026-09-02 NodeGoat-20260902-032328：527d21f5 引入 allowed_roots（跨仓拓扑），
openai 引擎 ToolContext 构造 ``tuple(Path(root).resolve() for root in allowed_roots)``
无 None 容错（anthropic 引擎 providers_anthropic.py 同款写法带 ``(allowed_roots or [])``
容错）——executor→run_claude_prompt 主链路不传 allowed_roots → 全部 openai 引擎 agent
（pre-recon / gn-discovery-sink/source/storage-*）18-25ms 秒挂
"'NoneType' object is not iterable"、0 tokens（请求未发出，LLM 全轨瘫痪）。

修复对齐 anthropic 容错 ``(allowed_roots or [])``；空 tuple 语义 = 不限制
（tools_openai/fs.py::_within_allowed_roots ``not roots or ...``），与旧行为一致。
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from supernova_core.agents.providers_openai import OpenAIProvider
from supernova_core.agents.runner import ProviderConfig


def _provider():
    return OpenAIProvider(ProviderConfig(
        type="openai_compatible", api_key="test", base_url="https://x.example.com"))


def _streaming_result_ok(final_text="done"):
    """伪造 Runner.run_streamed 正常完成：stream_events 空 + final_output 文本。"""
    result = MagicMock()
    result.final_output = final_text
    result.context_wrapper = MagicMock()
    usage = MagicMock()
    usage.input_tokens = 0
    usage.output_tokens = 0
    usage.input_tokens_details = None
    result.context_wrapper.usage = usage

    async def _stream():
        if False:  # 保证是 async generator
            yield

    result.stream_events = _stream
    return result


@pytest.mark.asyncio
async def test_call_without_allowed_roots_does_not_crash(monkeypatch):
    """不传 allowed_roots（主链路 executor→run_claude_prompt 现状）→ 不得 TypeError，
    ToolContext.allowed_roots 收敛为空 tuple（不限制，同旧行为）。"""
    p = _provider()
    run_streamed = MagicMock(return_value=_streaming_result_ok())
    monkeypatch.setattr(
        "supernova_core.agents.providers_openai.Runner.run_streamed", run_streamed)

    ret = await p.call(prompt="P", cwd="/tmp", model_tier="medium")

    assert ret.success is True, f"call 不传 allowed_roots 秒挂: {ret.error}"
    assert "NoneType" not in (ret.error or "")
    ctx = run_streamed.call_args.kwargs["context"]
    assert ctx.allowed_roots == ()


@pytest.mark.asyncio
async def test_call_with_allowed_roots_resolves_to_tuple(monkeypatch):
    """传 allowed_roots → resolve 成 tuple[Path,...]（跨仓拓扑语义不变）。"""
    p = _provider()
    run_streamed = MagicMock(return_value=_streaming_result_ok())
    monkeypatch.setattr(
        "supernova_core.agents.providers_openai.Runner.run_streamed", run_streamed)

    ret = await p.call(prompt="P", cwd="/tmp", model_tier="medium",
                       allowed_roots=["/tmp/a", "/tmp/b"])

    assert ret.success is True, f"call 带 allowed_roots 失败: {ret.error}"
    ctx = run_streamed.call_args.kwargs["context"]
    assert ctx.allowed_roots == tuple(Path(x).resolve() for x in ("/tmp/a", "/tmp/b"))
