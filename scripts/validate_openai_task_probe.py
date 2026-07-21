#!/usr/bin/env python3
"""Minimal openai-engine GLM-Task validation — spec 改动 4a 的双引擎对齐 checkpoint。

与 scripts/validate_glm_task_probe.py 互补：该脚本已验证 GLM 经 claude-agent-sdk
（glm-anthropic，profile anthropic_api + Claude Code CLI）能正确驱动 Agent 子代理工具
（2/2 PASS）。本脚本验证同一 Task-delegation prompt 经 **openai-agents 引擎**
（glm-openai，profile openai_compatible）能否驱动 Task 5 新增的 `task` function_tool
（tools_openai/build_tools() 暴露，providers_openai._make_subagent_runner 注入 runner）。

问题：glm-openai 下，GLM 能否按 vuln prompt 的 "delegate to Task Agent, MANDATORY"
      正确发起 `task` tool call？

PASS：GLM 发起 ≥1 次 task tool call（子代理读码），audit 录到 toolName=task，并产出 SQLi 判定。
FAIL：GLM 从不发 task（自己用 Read / 卡住）→ 见 task-9-brief Step 3 失败处置（检查
      build_tools() 含 task / _make_subagent_runner 注入 / GLM openai 端点 tool-use 行为差异）。

注：openai 引擎不走 CLI，无 IS_SANDBOX 依赖（glm probe 亦未设置该变量）。
"""
import asyncio
import os
import tempfile
import time
from pathlib import Path

# 工作目录可能是 /root/supernova（CI/Linux）或本仓库根（macOS）。两种都支持。
_PROFILE_CANDIDATES = [
    Path("/root/supernova/.env.profiles/glm-openai.env"),
    Path(__file__).resolve().parent.parent / ".env.profiles" / "glm-openai.env",
]
PROFILE = next((p for p in _PROFILE_CANDIDATES if p.exists()), _PROFILE_CANDIDATES[-1])


def load_profile() -> None:
    for line in PROFILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
    # 强制 openai 引擎（Task 5 新增 task function_tool 的引擎），覆盖任何默认。
    # glm-openai.env 本身已设 openai_compatible，此处显式 setenv 做防御性锁定。
    os.environ["SUPERNOVA_AI_PROVIDER"] = "openai_compatible"
    os.environ["CLAUDE_MAX_TURNS"] = "15"  # 最小测试，有界（与 glm probe 对齐）


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
    target = Path(tempfile.mkdtemp(prefix="openai_task_probe_"))
    (target / "app.py").write_text(
        "import sqlite3\n"
        "def get_user(name):\n"
        "    conn = sqlite3.connect('db')\n"
        "    cur = conn.cursor()\n"
        "    cur.execute(\"SELECT * FROM users WHERE name='\" + name + \"'\")\n"
        "    return cur.fetchone()\n"
    )
    # 与 glm probe 完全一致的 prompt（唯一变量是引擎）
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
    print(f"[probe] target={target}  provider=openai_compatible (glm-openai)  "
          f"profile={PROFILE}  model_tier=medium")
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
    # openai 引擎 Task 5 暴露的 function_tool name 为 "task"（task.py name_override="task"）；
    # 同时保留对 "agent" 的识别以兼容 claude-agent-sdk 引擎产物（glm probe 走该名）。
    task_used = any(t.lower() in ("task", "agent") for t in logger.tools)
    read_used = any(t.lower() == "read" for t in logger.tools)
    print(f"\n>>> SUBAGENT TOOL USED (task/agent): "
          f"{'YES ✅ (openai 引擎 task tool 被 GLM 驱动)' if task_used else 'NO ❌'}")
    print(f">>> READ USED (直接读): {read_used}")
    for name, params in logger.tool_params:
        if name.lower() in ("task", "agent"):
            print(f"    {name} call params: {params}")
    print("\n--- FINAL ANSWER ---")
    print((result.text or "(empty)")[:1500])


if __name__ == "__main__":
    asyncio.run(main())
