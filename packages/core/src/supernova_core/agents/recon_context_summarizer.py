# packages/core/src/supernova_core/agents/recon_context_summarizer.py
"""Lightweight LLM summarizer for recon_deliverable.md → structured {{RECON_CONTEXT}}.

Reads the LLM-track recon deliverable (free-text markdown) and produces a compact
structured summary of §4 endpoint inventory + §8 authz candidates, injected into vuln
prompts so the vuln agent gets structured prior knowledge without re-reading the whole md.

Pattern mirrors chain_verdict.judge_chain_verdict: single run_claude_prompt call, JSON-ish
free-text parse, graceful degradation. Input is LLM-track self-produced md — NOT
deterministic-layer output (CLAUDE.md §1 ironclad rule).
"""

from __future__ import annotations

import logging
import re
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# Digest 缓存指纹的一部分：摘要 prompt 语义变化时递增，强制旧 digest 失效。
# v2（spec 2026-09-01 §4.2）：六节固定结构 + 硬约束 + 输入改为六节全量抽取（删 [:8000]）。
RECON_CONTEXT_SUMMARIZER_PROMPT_VERSION = 2

# 摘要输入防御上限（spec 2026-09-01 §4.1；真实最大样本 ~45K，纯防御）。
_INPUT_CHAR_LIMIT = 100_000

_SUMMARY_PROMPT = """You are a compact summarizer for a static-recon deliverable.
Given the extracted recon sections, produce EXACTLY these six sections, in order,
each starting with its heading on its own line:

## endpoints — EVERY endpoint from the API inventory, one line each, terse format:
   METHOD /path (role, object-id param if any). No prose.
## authz — horizontal / vertical / context candidates (one line each: endpoint,
   missing or weak control, data sensitivity)
## injection — command / SQL / LFI·RFI / path-traversal / deserialization source leads
## xss — template-rendering / reflection / storage sink leads
## ssrf — outbound-request leads (including endpoints whose description mentions
   fetching user-controlled URLs; include network flows if present)
## auth — authentication-flow and role-architecture highlights

HARD CONSTRAINT: Only reorganize leads that are PRESENT in the input text. Do NOT
infer, speculate, or add any lead not explicitly written there. If a section has no
leads, output exactly "(none found)" under that heading. Never omit a heading.

Recon sections:
---
{recon_md}
---

Respond with the six sections ONLY (no preamble, no JSON)."""


def _extract_block(lines: list[str], start_re: re.Pattern, *, sub: bool) -> str:
    """抽取 start_re 命中的 markdown 节：到下一个二级标题（子节则含三级）止。

    Heading 由渲染器产出（``## {n}. {title}`` / ``### {num} {title}``），
    结构稳定（spec 2026-09-01 §3 锚点）。未命中返回空串（静默跳过）。
    """
    out: list[str] = []
    capturing = False
    for line in lines:
        stripped = line.strip()
        if not capturing:
            if start_re.match(stripped):
                capturing = True
                out.append(line)
            continue
        if stripped.startswith("## "):
            break
        if sub and stripped.startswith("### "):
            break
        out.append(line)
    return "\n".join(out).strip()


# 六节抽取集合（spec 2026-09-01 §4.1）：§3 认证流 / §4 端点清单 / §6.3 网络流 /
# §7 角色架构 / §8 authz 候选 / §9 注入源。任一节缺失静默跳过。
_INPUT_BLOCKS: tuple[tuple[re.Pattern, bool], ...] = (
    (re.compile(r"^## 3\.(?:\s|$)"), False),
    (re.compile(r"^## 4\.(?:\s|$)"), False),
    (re.compile(r"^### 6\.3(?:\s|$)"), True),
    (re.compile(r"^## 7\.(?:\s|$)"), False),
    (re.compile(r"^## 8\.(?:\s|$)"), False),
    (re.compile(r"^## 9\.(?:\s|$)"), False),
)


def build_summarizer_input(recon_md: str) -> tuple[str, dict]:
    """构造摘要输入：六节确定性抽取拼接 + 对账元数据。

    替代旧 ``recon_md[:8000]`` 截断（spec 2026-09-01 §1.1 P0）。返回
    ``(拼接文本, {"source_endpoint_rows", "input_chars", "input_truncated"})``；
    超过 ``_INPUT_CHAR_LIMIT`` 按原序截尾（纯防御，可观测）。
    """
    lines = recon_md.splitlines()
    blocks = [_extract_block(lines, pattern, sub=sub) for pattern, sub in _INPUT_BLOCKS]
    text = "\n\n".join(block for block in blocks if block)

    pipe_rows = sum(
        1 for ln in blocks[1].splitlines() if ln.strip().startswith("|"))
    truncated = len(text) > _INPUT_CHAR_LIMIT
    if truncated:
        text = text[:_INPUT_CHAR_LIMIT]
        logger.warning(
            "recon_context summarizer input exceeds %d chars, tail truncated",
            _INPUT_CHAR_LIMIT)
    return text, {
        "source_endpoint_rows": max(pipe_rows - 2, 0),
        "input_chars": len(text),
        "input_truncated": truncated,
    }


# 六节固定序（spec 2026-09-01 §4.3）：missing_sections 观测 + 注入侧重组节序共用。
DIGEST_SECTION_ORDER = ("endpoints", "authz", "injection", "xss", "ssrf", "auth")

# 六节别名表（spec 2026-09-01 §4.3）：规范化 heading 全等查表，六节各一组少量别名。
_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "endpoints": ("endpoints", "endpoint", "api endpoints", "api", "端点", "接口清单"),
    "authz": ("authz", "authorization", "authorization candidates", "授权候选", "越权"),
    "injection": ("injection", "injection sources", "注入", "注入源"),
    "xss": ("xss", "cross-site scripting", "cross site scripting", "跨站脚本"),
    "ssrf": ("ssrf", "server-side request forgery", "出站请求"),
    "auth": ("auth", "authentication", "authentication flow", "认证", "认证流"),
}
_ALIAS_LOOKUP = {
    alias: name for name, aliases in _SECTION_ALIASES.items() for alias in aliases
}
UNPARSED_SECTION = "_unparsed"

_DIGEST_HEADING_RE = re.compile(r"^#{2,3}\s+(.+?)\s*$")


def _canonical_section(title: str) -> str | None:
    """Heading 文本 → 规范节名；小写、压缩空白、切掉破折号/括号说明尾巴。"""
    t = re.sub(r"\s+", " ", title.strip().lower())
    t = t.replace("—", " - ").replace("–", " - ")
    for sep in (" - ", "(", "（"):
        t = t.split(sep)[0].strip()
    return _ALIAS_LOOKUP.get(t)


def parse_sections(raw: str) -> dict[str, str]:
    """LLM 摘要输出 → 六节视图 dict（纯代码解析，永不 hard fail）。

    - 逐行匹配 ``## ``/``### `` heading，规范化后查别名表；
    - 识别不了的段落（含首个 heading 前导语）挂 ``_unparsed``——零信息丢失；
    - 漏节不补造（``missing_sections`` 由调用方计算）；
    - 一个命名节都识别不出 → 空 dict（调用方判 unsectioned、注入退 ``text``）。
    """
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        body = "\n".join(buf).strip()
        if current is not None and body:
            sections[current] = (
                sections[current] + "\n" + body if current in sections else body
            )
        buf.clear()

    for line in raw.splitlines():
        stripped = line.strip()
        m = _DIGEST_HEADING_RE.match(stripped)
        if m:
            _flush()
            current = _canonical_section(m.group(1)) or UNPARSED_SECTION
        elif stripped:
            if current is None:
                current = UNPARSED_SECTION
            buf.append(line)
    _flush()

    if not any(name != UNPARSED_SECTION for name in sections):
        return {}
    return sections


def _extract_sections(recon_md: str) -> str:
    """Degradation fallback: six-section extract（与 LLM 摘要输入同构）."""
    return extract_recon_context_sections(recon_md)


# 抽取块 → 六节视图名映射（spec 2026-09-01 §4.4）：§3+§7→auth、§4→endpoints、
# §6.3→ssrf、§8→authz、§9→injection；xss 无源节（SSTI 桶在 §9 下），缺席即可。
_BLOCK_SECTION_NAMES = ("auth", "endpoints", "ssrf", "auth", "authz", "injection")


def build_deterministic_sections(recon_md: str) -> dict[str, str]:
    """确定性六节抽取 → 与 LLM 摘要同构的 sections 视图（deterministic-extract 用）。

    值为带原 heading 的抽取块文本（§3/§7 同映射 auth 时拼接）；源节缺失即缺席，
    不补造——``missing_sections`` 由调用方观测。
    """
    lines = recon_md.splitlines()
    sections: dict[str, str] = {}
    for (pattern, sub), name in zip(_INPUT_BLOCKS, _BLOCK_SECTION_NAMES):
        block = _extract_block(lines, pattern, sub=sub)
        if not block:
            continue
        sections[name] = (
            sections[name] + "\n\n" + block if name in sections else block
        )
    return sections


def extract_recon_context_sections(recon_md: str) -> str:
    """Public deterministic fallback for the shared recon-context digest.

    六节抽取，与 LLM 摘要输入（``build_summarizer_input``）同集合——降级档
    与 llm-summary 档信息面一致（spec 2026-09-01 §4.1，修质量倒挂）。
    """
    text, _meta = build_summarizer_input(recon_md)
    return text or recon_md[:2000]


async def summarize_recon_context(
    recon_md: str,
    llm_client: Callable[[str], Awaitable[str]],
    *,
    fallback_on_error: bool = True,
) -> str:
    """Summarize recon md into a structured context string for {{RECON_CONTEXT}}.

    Input is the six-section deterministic extract (``build_summarizer_input``)，
    NOT a truncated prefix (spec 2026-09-01 §1.1 P0 fix). By default falls back
    to raw §4/§8 extraction if the LLM call fails (legacy per-agent behavior).
    Shared-digest generation passes ``fallback_on_error=False`` so it can mark
    the artifact as degraded and retry the LLM upgrade on resume.
    """
    if not recon_md or not recon_md.strip():
        return "(no recon deliverable available)"
    input_text, _meta = build_summarizer_input(recon_md)
    if not input_text:
        # 六节全缺（格式大变）的防御：退全文，让 LLM 自己找线索。
        input_text = recon_md
    try:
        return await llm_client(_SUMMARY_PROMPT.format(recon_md=input_text))
    except Exception as e:  # noqa: BLE001 — graceful degradation
        if not fallback_on_error:
            raise
        logger.warning("recon_context summarizer LLM failed, falling back to raw extract: %s", e)
        return _extract_sections(recon_md)
