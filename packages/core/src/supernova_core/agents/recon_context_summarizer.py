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
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

_SUMMARY_PROMPT = """You are a compact summarizer for a static-recon deliverable.
Given the recon markdown, extract ONLY:
1. Every endpoint from Section 4 (API Endpoint Inventory): METHOD /path (required role,
   object-id parameter if any).
2. Every Section 8 authorization candidate (horizontal/vertical/context): the endpoint,
   the missing or weak control, and the data sensitivity.
Be terse — one line per item. Omit prose. If a section is absent, skip it silently.

Recon markdown:
---
{recon_md}
---

Respond with the extracted lines ONLY (no preamble, no JSON)."""


def _extract_sections(recon_md: str) -> str:
    """Degradation fallback: extract §4 + §8 raw text from markdown."""
    lines = recon_md.splitlines()
    out, capturing = [], False
    for line in lines:
        if line.strip().startswith("## 4.") or line.strip().startswith("## 8."):
            capturing = True
            out.append(line)
        elif line.strip().startswith("## ") and capturing:
            capturing = False
        elif capturing:
            out.append(line)
    return "\n".join(out).strip() or recon_md[:2000]


async def summarize_recon_context(
    recon_md: str,
    llm_client: Callable[[str], Awaitable[str]],
) -> str:
    """Summarize recon md into a structured context string for {{RECON_CONTEXT}}.

    Falls back to raw §4/§8 extraction if the LLM call fails (non-fatal).
    """
    if not recon_md or not recon_md.strip():
        return "(no recon deliverable available)"
    try:
        return await llm_client(_SUMMARY_PROMPT.format(recon_md=recon_md[:8000]))
    except Exception as e:  # noqa: BLE001 — graceful degradation
        logger.warning("recon_context summarizer LLM failed, falling back to raw extract: %s", e)
        return _extract_sections(recon_md)
