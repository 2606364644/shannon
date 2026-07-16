#!/usr/bin/env python3
"""Minimal GLM-MCP-tool validation — host 渲染架构的决定性 checkpoint。

问题：glm-anthropic（GLM 经 BigModel anthropic 端点 + Claude Code CLI）下，GLM 能否
      驱动 in-process SDK MCP 工具（set_*），传符合 schema 的结构化参数，多次调用（write-once）？

PASS：GLM 发起 ≥1 次 set_* MCP 工具调用，collector.get_all() 非空。
FAIL：GLM 从不调 set_*（无视工具 / 卡住）→ host 渲染架构在 claude 轨受阻，需讨论。

对标 validate_glm_task_probe.py。scripts 级真机验证（非 pytest）。
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

_PROFILE_CANDIDATES = [
    Path("/root/shannon-py/.env.profiles/glm-anthropic.env"),
    Path(__file__).resolve().parent.parent / ".env.profiles" / "glm-anthropic.env",
]
PROFILE = next((p for p in _PROFILE_CANDIDATES if p.exists()), _PROFILE_CANDIDATES[-1])


def load_profile() -> None:
    if PROFILE.exists():
        for line in PROFILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    os.environ["SHANNON_AI_PROVIDER"] = "anthropic_api"   # claude 轨才走 SDK MCP
    os.environ["CLAUDE_MAX_TURNS"] = "20"


from shannon_core.agents.tool_audit_logger import NullToolAuditLogger


class RecordingLogger(NullToolAuditLogger):
    def __init__(self) -> None:
        self.tools: list[str] = []

    async def log_tool_start(self, tool_name: str, parameters) -> None:
        self.tools.append(tool_name)


async def main() -> None:
    load_profile()
    from shannon_core.collectors.pre_recon import PreReconCollector
    from shannon_core.agents.runner import run_claude_prompt
    from shannon_core.renderers.pre_recon import render_pre_recon

    target = Path(tempfile.mkdtemp(prefix="glm_mcp_probe_"))
    (target / "app.py").write_text(
        "from flask import Flask, request\n"
        "app = Flask(__name__)\n"
        "@app.route('/search')\n"
        "def search():\n"
        "    q = request.args.get('q', '')\n"
        "    import sqlite3\n"
        "    cur = sqlite3.connect('db').cursor()\n"
        "    cur.execute(\"SELECT * FROM items WHERE name='%s'\" % q)\n"
        "    return str(cur.fetchall())\n"
    )

    collector = PreReconCollector()
    prompt = (
        "You are a pre-recon agent. Analyze the Flask app in cwd for security posture.\n\n"
        "<deliverable_tools>\n"
        "Emit your findings exclusively via the deliverable tools. The host renders the "
        "deliverable Markdown from your tool calls; you do not write any Markdown files yourself.\n"
        "You must call all seven of the following tools exactly once before terminating:\n"
        "- set_executive_summary\n- set_application_intelligence\n- set_auth_deep_dive\n"
        "- set_codebase_indexing\n- set_critical_file_paths\n- set_xss_sinks\n- set_ssrf_sinks\n"
        "Each tool's full schema is in your tool catalog — read it there.\n"
        "</deliverable_tools>\n\n"
        "Task: call set_executive_summary with a 2-3 paragraph overview, then proceed through the "
        "remaining tools. The SQL injection in /search is relevant to your attack surface analysis."
    )

    logger = RecordingLogger()
    t0 = time.time()
    print(f"[probe] target={target}  provider=anthropic_api (glm-anthropic)")
    try:
        result = await asyncio.wait_for(
            run_claude_prompt(
                prompt=prompt,
                repo_path=str(target),
                model_tier="large",
                tool_audit_logger=logger,
                collector=collector,
            ),
            timeout=360,
        )
    except asyncio.TimeoutError:
        print("RESULT: TIMEOUT (>360s)")
        return

    dt = time.time() - t0
    set_calls = [t for t in logger.tools if "set_" in t]
    print("=" * 64)
    print(f"duration={dt:.1f}s  turns={getattr(result, 'turns', None)}  success={result.success}")
    if result.error:
        print(f"ERROR: {result.error}")
    print(f"TOOLS CALLED ({len(logger.tools)}): {logger.tools}")
    print(f"COLLECTED SECTIONS: {list(collector.get_all().keys())}")
    print(f"CALL STATUS: {collector.get_call_status()}")
    rendered = render_pre_recon(collector.get_all())
    print("\n--- RENDERED MD (first 800 chars) ---")
    print(rendered[:800])
    passed = len(set_calls) > 0 and len(collector.get_all()) > 0
    print(f"\n>>> MCP set_* TOOLS USED: {'YES ✅' if set_calls else 'NO ❌'}")
    print(f">>> RESULT: {'PASS ✅' if passed else 'FAIL ❌'}")


if __name__ == "__main__":
    asyncio.run(main())
