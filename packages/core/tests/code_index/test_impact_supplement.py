"""impact_supplement 单元测试。"""
import pytest
from supernova_core.code_index.impact_supplement import (
    impact_upstream, impact_downstream,
)


class FakeImpactMCP:
    def __init__(self, response=None, error=False):
        self._response = response
        self._error = error
        self.calls = []

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        if self._error:
            raise RuntimeError("timeout")
        return self._response


@pytest.mark.asyncio
async def test_impact_upstream_returns_bydepth_risk():
    """必带 file_path 消歧（Go 仓纯 name ambiguous 率高）。"""
    mcp = FakeImpactMCP(response={
        "byDepth": {"1": [{"name": "caller"}]},
        "risk": "HIGH",
        "affected_processes": [{"name": "Flow"}],
    })
    out = await impact_upstream(mcp, name="Save", file_path="repo.go")
    assert out["risk"] == "HIGH"
    assert "1" in out["byDepth"]
    assert len(out["affected_processes"]) == 1
    # 确认带了 file_path
    args = mcp.calls[0][1]
    assert args["target"] == "Save"
    assert args["file_path"] == "repo.go"
    assert args["direction"] == "upstream"


@pytest.mark.asyncio
async def test_impact_downstream_empty_on_none():
    mcp = FakeImpactMCP(response=None)
    assert await impact_downstream(mcp, "x", "f.go") == {}


@pytest.mark.asyncio
async def test_impact_upstream_empty_on_exception():
    """超时/异常 → log + {}，不抛（补充层 best-effort）。"""
    mcp = FakeImpactMCP(error=True)
    assert await impact_upstream(mcp, "x", "f.go") == {}
