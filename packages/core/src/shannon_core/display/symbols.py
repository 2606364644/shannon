"""Display status symbols — single source of truth for all renderers.

STEP / AGENT / summary 的状态符号集中在此，避免字面量散落在
rich_renderer.py / file_renderer.py 多处导致不一致。
"""
from __future__ import annotations

STEP_PENDING = "○"
STEP_DONE = "✓"
STEP_FAIL = "✗"

AGENT_START = "▶"
AGENT_DONE = "✓"
AGENT_FAIL = "✗"

SUMMARY_OK = "✓"
SUMMARY_FAIL = "✗"
