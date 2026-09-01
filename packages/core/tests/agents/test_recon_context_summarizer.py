# packages/core/tests/agents/test_recon_context_summarizer.py
from pathlib import Path

import pytest
from supernova_core.agents.recon_context_summarizer import (
    _INPUT_CHAR_LIMIT,
    RECON_CONTEXT_SUMMARIZER_PROMPT_VERSION,
    build_deterministic_sections,
    build_summarizer_input,
    extract_recon_context_sections,
    parse_sections,
    summarize_recon_context,
)


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
    src = inspect.getsource(__import__("supernova_core.agents.recon_context_summarizer", fromlist=["x"]))
    for tok in ("parameter_graph", "SinkCallSite", "static_dataflow_hints"):
        assert tok not in src, f"summarizer 引确定性 token: {tok}"


# ── build_summarizer_input：六节抽取 + 对账元数据（spec 2026-09-01 §4.1）──────────

_FIXTURE = Path(__file__).parent / "fixtures" / "nodegoat_recon_deliverable.md"


def test_build_summarizer_input_real_scale_nodegoat():
    """真实规模（31 端点 NodeGoat 副本）：六节齐全、rows=31、无截断。

    会失败的原因：截断 bug 修复后摘要输入应覆盖 §9 注入源与全部 31 端点。
    """
    recon_md = _FIXTURE.read_text(encoding="utf-8")
    text, meta = build_summarizer_input(recon_md)

    for heading in ("## 3.", "## 4.", "### 6.3", "## 7.", "## 8.", "## 9."):
        assert heading in text, f"六节抽取缺节: {heading}"
    # 未选节不进输入（§1/§5/§6 其余子节被排除）
    for excluded in ("## 1. Executive Summary", "## 5. Potential Input Vectors",
                     "### 6.1", "### 6.4", "## 0)"):
        assert excluded not in text, f"未选节泄入输入: {excluded}"
    assert meta["source_endpoint_rows"] == 31
    assert meta["input_truncated"] is False
    assert meta["input_chars"] == len(text)


def test_build_summarizer_input_skips_missing_sections_silently():
    """任一节缺失静默跳过（§6.3 无出站流时常见空）。"""
    recon_md = ("## 3. Authentication Flow\n- session cookie\n\n"
                "## 4. API Endpoint Inventory\n"
                "| Method | Path |\n|---|---|\n| GET | /a |\n\n"
                "## 8. Authorization Candidates\n- GET /a no ownership check\n")
    text, meta = build_summarizer_input(recon_md)

    assert "### 6.3" not in text and "## 7." not in text and "## 9." not in text
    assert "## 3. Authentication Flow" in text
    assert "## 4. API Endpoint Inventory" in text
    assert "## 8. Authorization Candidates" in text
    assert meta["source_endpoint_rows"] == 1


def test_build_summarizer_input_endpoint_row_counting():
    """rows = §4 中以 | 开头的行数 − 2（扣表头与分隔行）。"""
    recon_md = ("## 4. API Endpoint Inventory\n"
                "| Method | Path |\n|---|---|\n"
                "| GET | /a |\n| POST | /b |\n| GET | /c |\n")
    _, meta = build_summarizer_input(recon_md)
    assert meta["source_endpoint_rows"] == 3


def test_build_summarizer_input_truncates_at_defense_cap():
    """100K 防御上限：按原序截尾 + input_truncated=True。"""
    big = "## 9. Injection Sources\n" + ("lead line\n" * 20_000)
    assert len(big) > _INPUT_CHAR_LIMIT
    text, meta = build_summarizer_input(big)

    assert meta["input_truncated"] is True
    assert len(text) == _INPUT_CHAR_LIMIT
    assert meta["input_chars"] == len(text)


# ── parse_sections：六节解析 + 坏输出容错（spec 2026-09-01 §4.3）──────────

def test_parse_sections_six_headings():
    """六节全命中：内容按 heading 归位。"""
    raw = ("## endpoints\n- GET /a (user)\n- POST /b (admin)\n"
           "## authz\n- GET /a: no ownership check\n"
           "## injection\n- cmd: user.name in exec\n"
           "## xss\n- render template t.html\n"
           "## ssrf\n- /research fetches user-controlled URL\n"
           "## auth\n- session cookie, no rotation\n")
    sections = parse_sections(raw)

    assert set(sections) == {"endpoints", "authz", "injection", "xss", "ssrf", "auth"}
    assert "- GET /a (user)" in sections["endpoints"]
    assert "no ownership check" in sections["authz"]
    assert "/research" in sections["ssrf"]


def test_parse_sections_heading_aliases():
    """节名漂移容错：### 级、大小写、复数、括号/破折号尾巴、中文。"""
    raw = ("### Endpoints (API inventory)\n- GET /a\n"
           "### Authorization Candidates\n- x\n"
           "## Injection Sources — command leads\n- y\n"
           "### 认证\n- z\n")
    sections = parse_sections(raw)

    assert "- GET /a" in sections["endpoints"]
    assert "- x" in sections["authz"]
    assert "- y" in sections["injection"]
    assert "- z" in sections["auth"]


def test_parse_sections_unparsed_segments_kept():
    """识别不了的段落挂 _unparsed（零信息丢失）；首个 heading 前导语同理。"""
    raw = ("Here is the summary:\n"
           "## endpoints\n- GET /a\n"
           "## mystery section\n- unknown content\n")
    sections = parse_sections(raw)

    assert set(sections) == {"endpoints", "_unparsed"}
    assert "Here is the summary" in sections["_unparsed"]
    assert "unknown content" in sections["_unparsed"]


def test_parse_sections_missing_sections_not_fabricated():
    """漏节不补造（missing_sections 由调用方计算）。"""
    sections = parse_sections("## endpoints\n- GET /a\n## authz\n- x\n")
    assert set(sections) == {"endpoints", "authz"}


def test_parse_sections_prose_only_returns_empty():
    """完全未分节（纯散文）→ 空 dict，调用方据此判 unsectioned 退 text。"""
    sections = parse_sections("The API has endpoints but no heading at all.\nJust prose.\n")
    assert sections == {}


# ── 摘要 prompt v2：六节 + 硬约束 + 删 [:8000] 截断（spec 2026-09-01 §4.1/§4.2）──

def test_summarizer_prompt_version_is_2():
    """prompt 语义变化 → 版本指纹升 2，旧 digest 缓存自动失效。"""
    assert RECON_CONTEXT_SUMMARIZER_PROMPT_VERSION == 2


@pytest.mark.asyncio
async def test_summarize_input_covers_sections_beyond_8000_chars():
    """P0 截断修复：§4 起点在 8000 字符后、§9 在更后——都必须进摘要输入。"""
    recon_md = (
        "# Recon\n\n## 1. Executive Summary\n" + ("filler prose line\n" * 800)
        + "\n## 4. API Endpoint Inventory\n"
        "| Method | Path | Role |\n|---|---|---|\n| GET | /deep | user |\n"
        + "\n## 8. Authorization Candidates\n- GET /deep: no ownership check\n"
        + ("filler\n" * 100)
        + "\n## 9. Injection Sources\n- cmd: exec(user.input)\n"
    )
    assert recon_md.find("## 4.") > 8000  # fixture 自证：旧 [:8000] 看不到 §4

    prompts: list[str] = []

    async def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return "## endpoints\n- GET /deep (user)"

    await summarize_recon_context(recon_md, fake_llm)

    assert prompts, "llm client 未被调用"
    prompt = prompts[0]
    assert "/deep" in prompt, "§4 端点未进摘要输入（截断 bug 未修）"
    assert "exec(user.input)" in prompt, "§9 注入源未进摘要输入"
    assert "Executive Summary" not in prompt, "未选节（§1）不应进摘要输入"


@pytest.mark.asyncio
async def test_summarize_prompt_requires_six_sections_and_no_inference():
    """prompt v2：要求六节固定结构 + 只重组原文线索的硬约束。"""
    recon_md = "## 4. API Endpoint Inventory\n| GET | /a | user |\n"

    prompts: list[str] = []

    async def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return "## endpoints\n- GET /a (user)"

    await summarize_recon_context(recon_md, fake_llm)

    prompt = prompts[0]
    for heading in ("endpoints", "authz", "injection", "xss", "ssrf", "auth"):
        assert heading in prompt, f"prompt 缺六节指令: {heading}"
    flat = " ".join(prompt.split())  # 空白不敏感（prompt 内硬约束跨行换行）
    assert "Do NOT infer" in flat
    assert "(none found)" in flat
    assert "Never omit a heading" in flat


# ── 降级链六节同构（spec 2026-09-01 §4.1/§4.4）──────────

_SIX_SECTION_MD = (
    "## 3. Authentication Flow\n- session cookie\n\n"
    "## 4. API Endpoint Inventory\n"
    "| Method | Path |\n|---|---|\n| GET | /a | user |\n\n"
    "## 5. Potential Input Vectors\n- excluded section\n\n"
    "### 6.3 Flows\n- client → api → db\n\n"
    "## 7. Role Architecture\n- 3 roles\n\n"
    "## 8. Authorization Candidates\n- GET /a: no ownership check\n\n"
    "## 9. Injection Sources\n- cmd: exec(user.input)\n"
)


def test_extract_recon_context_sections_covers_six_sections():
    """降级入口与 LLM 输入同构：六节全集（旧版只有 §4+§8）。"""
    text = extract_recon_context_sections(_SIX_SECTION_MD)

    for present in ("## 3.", "## 4.", "### 6.3", "## 7.", "## 8.", "## 9."):
        assert present in text, f"降级抽取缺节: {present}"
    assert "## 5." not in text, "未选节（§5）不应进降级抽取"
    assert "exec(user.input)" in text, "§9 注入源应进降级抽取"


def test_build_deterministic_sections_maps_source_sections():
    """确定性 sections：§3+§7→auth、§4→endpoints、§6.3→ssrf、§8→authz、§9→injection。

    xss 无源节（SSTI 桶在 §9 下），deterministic 模式缺席（missing_sections 观测）。
    """
    sections = build_deterministic_sections(_SIX_SECTION_MD)

    assert set(sections) == {"auth", "endpoints", "ssrf", "authz", "injection"}
    assert "session cookie" in sections["auth"]
    assert "3 roles" in sections["auth"]
    assert "| GET | /a | user |" in sections["endpoints"]
    assert "client → api" in sections["ssrf"]
    assert "no ownership check" in sections["authz"]
    assert "exec(user.input)" in sections["injection"]
