from shannon_core.agents.runner import ClaudeRunResult, run_claude_prompt
import pytest


def test_claude_run_result_defaults():
    result = ClaudeRunResult()
    assert result.text == ""
    assert result.success is False
    assert result.duration == 0
    assert result.turns == 0
    assert result.cost == 0.0
    assert result.model is None
    assert result.structured_output is None
    assert result.error is None
    assert result.retryable is True


def test_claude_run_result_with_values():
    result = ClaudeRunResult(
        text="hello",
        success=True,
        duration=5000,
        turns=3,
        cost=0.05,
        model="claude-sonnet-4-6",
        structured_output={"key": "value"},
        error=None,
        retryable=False,
    )
    assert result.text == "hello"
    assert result.success is True
    assert result.cost == 0.05
    assert result.structured_output == {"key": "value"}


@pytest.mark.asyncio
async def test_run_claude_prompt_not_implemented():
    with pytest.raises(NotImplementedError, match="Claude Agent SDK"):
        await run_claude_prompt(prompt="test", repo_path="/tmp")


def test_run_claude_prompt_accepts_structured_output_schema():
    """Verify run_claude_prompt signature accepts structured_output_schema parameter."""
    import inspect
    from shannon_core.agents.runner import run_claude_prompt as _run
    sig = inspect.signature(_run)
    assert "structured_output_schema" in sig.parameters
    assert sig.parameters["structured_output_schema"].default is None
