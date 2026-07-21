#!/usr/bin/env python3
"""Minimal GLM-Task validation — spec 改动 4a 的决定性 checkpoint。

问题：glm-anthropic（GLM 经 BigModel anthropic 端点 + Claude Code CLI）下，
      GLM 能否按 vuln prompt 的 "delegate to Task Agent, MANDATORY" 正确发起 Task tool call？

PASS：GLM 发起 ≥1 次 Task tool call（子代理读码），audit 录到 toolName=Task。
FAIL：GLM 从不发 Task（自己用 Read / 卡住）→ approach ① 模型侧受阻（注：approach ② 已作废，需另议降级）。实测结果为 PASS（2/2），本 FAIL 分支未触发。
"""
import asyncio
import os
import tempfile
import time
from pathlib import Path

PROFILE = Path("/root/supernova/.env.profiles/glm-anthropic.env")


def load_profile() -> None:
    for line in PROFILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
    # 强制 anthropic 引擎（CLI 原生有 Task），覆盖当前 active 的 glm-openai
    os.environ["SUPERNOVA_AI_PROVIDER"] = "anthropic_api"
    os.environ["CLAUDE_MAX_TURNS"] = "15"  # 最小测试，有界


from supernova_core.agents.tool_audit_logger import NullToolAuditLogger


class RecordingLogger(NullToolAuditLogger):
    def __init__(self) -> None:
        self.tools: list[str] = []
        self.tool_params: list[tuple[str, str]] = []

    async def log_tool_start(self, tool_name: str, parameters) -> None:
        self.tools.append(tool_name)
        self.tool_params.append((tool_name, str(parameters)[:300]))


async def main() -> None:
    load_profile()
    target = Path(tempfile.mkdtemp(prefix="glm_task_probe_"))
    (target / "app.py").write_text(
        "import sqlite3\n"
        "def get_user(name):\n"
        "    conn = sqlite3.connect('db')\n"
        "    cur = conn.cursor()\n"
        "    cur.execute(\"SELECT * FROM users WHERE name='\" + name + \"'\")\n"
        "    return cur.fetchone()\n"
    )
    prompt = (
        "You are an injection analysis specialist. Analyze app.py (in cwd) for SQL injection.\n\n"
        "CRITICAL TOOL USAGE RESTRICTIONS:\n"
        "- NEVER use the Read tool for application source code analysis—delegate every code review to the Task Agent.\n"
        "- ALWAYS direct the Task Agent to trace tainted data flow and sink construction before you reach a verdict.\n"
        "- Task Agent (Code Analysis): Your primary tool. MANDATORY for all source code analysis.\n\n"
        "Task: Use the Task Agent to read app.py and determine whether get_user(name) has a SQL injection flaw. "
        "Report: verdict (vulnerable/safe), the sink line, and rationale."
    )
    from supernova_core.agents.runner import run_claude_prompt

    logger = RecordingLogger()
    t0 = time.time()
    print(f"[probe] target={target}  provider=anthropic_api (glm-anthropic)  model_tier=medium")
    try:
        result = await asyncio.wait_for(
            run_claude_prompt(
                prompt=prompt,
                repo_path=str(target),
                model_tier="medium",
                tool_audit_logger=logger,
            ),
            timeout=300,
        )
    except asyncio.TimeoutError:
        print("RESULT: TIMEOUT (>300s)")
        return

    dt = time.time() - t0
    print("=" * 64)
    print(f"duration={dt:.1f}s  turns={getattr(result, 'turns', None)}  "
          f"cost={getattr(result, 'cost', None)}  success={result.success}")
    if result.error:
        print(f"ERROR: {result.error}")
    print(f"TOOLS CALLED ({len(logger.tools)}): {logger.tools}")
    task_used = any(t.lower() in ("task", "agent") for t in logger.tools)
    read_used = any(t.lower() == "read" for t in logger.tools)
    print(f"\n>>> SUBAGENT TOOL USED (Task/Agent): {'YES ✅ (approach ① 模型侧可行)' if task_used else 'NO ❌'}")
    print(f">>> READ USED (直接读): {read_used}")
    for name, params in logger.tool_params:
        if name.lower() in ("task", "agent"):
            print(f"    {name} call params: {params}")
    print("\n--- FINAL ANSWER ---")
    print((result.text or "(empty)")[:1500])


if __name__ == "__main__":
    asyncio.run(main())
