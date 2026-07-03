# packages/core/tests/agents/test_recon_context_summarizer.py
import pytest
from shannon_core.agents.recon_context_summarizer import summarize_recon_context


@pytest.mark.asyncio
async def test_summarize_returns_structured_summary():
    recon_md = """
## 4. API Endpoint Inventory
| Method | Endpoint Path | Required Role | Object ID Parameters |
| GET | /api/orders/{id} | user | order_id |
| DELETE | /api/users/{id} | admin | user_id |

## 8. Authorization Vulnerability Candidates
### 8.1 Horizontal Privilege Escalation Candidates
| Priority | Endpoint Pattern | Object ID Param | Ownership Check |
| High | DELETE /api/orders/{order_id} | order_id | none detected |
"""

    async def fake_llm(prompt: str) -> str:
        # 模拟 LLM 返回结构化摘要
        assert "orders" in prompt and "Horizontal" in prompt, "summarizer prompt 应含 recon 内容"
        return ("- GET /api/orders/{id} (user, object-id=order_id)\n"
                "- DELETE /api/users/{id} (admin, object-id=user_id)\n"
                "IDOR candidate: DELETE /api/orders/{order_id} — no ownership check")

    result = await summarize_recon_context(recon_md, fake_llm)
    assert "orders" in result
    assert "IDOR" in result or "ownership" in result.lower()


@pytest.mark.asyncio
async def test_summarizer_degrades_gracefully_on_llm_failure():
    """LLM 失败时降级为截取 recon md §4/§8 原文（不崩）。"""

    async def failing_llm(prompt: str) -> str:
        raise RuntimeError("LLM unavailable")

    recon_md = "## 4. API Endpoint Inventory\n| GET | /api/x | user | - |\n"
    result = await summarize_recon_context(recon_md, failing_llm)
    # 降级：返回原文 §4 段（非空）
    assert "API Endpoint Inventory" in result or "/api/x" in result


@pytest.mark.asyncio
async def test_summarizer_decoupled_from_deterministic():
    """守铁律：summarizer prompt 不引确定性产物。"""
    import inspect
    src = inspect.getsource(__import__("shannon_core.agents.recon_context_summarizer", fromlist=["x"]))
    for tok in ("parameter_graph", "SinkCallSite", "static_dataflow_hints"):
        assert tok not in src, f"summarizer 引确定性 token: {tok}"
