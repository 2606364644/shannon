#!/usr/bin/env python3
"""P6 硬验收：claude 引擎下 GLM 产出含 dataflow_steps 的 submit_finding。

与 scripts/validate_glm_task_probe.py 互补：glm probe 验证 GLM 经
claude-agent-sdk（glm-anthropic，profile anthropic_api + Claude Code CLI）
能驱动 Task 子代理工具（2/2 PASS）。本探针验证同一引擎下，GLM 能按
vuln-injection.txt 的 <finding_submission> 段产出含 `dataflow_steps` 字段
的 `submit_finding` 工具调用——spec 2026-08-20 dataflow-view §4 P6 硬验收项。

问题：glm-anthropic 下，GLM 能否按最小 vuln prompt 的 finding_submission 段
      + dataflow_steps 指引，正确发起 `submit_finding` 工具调用并填充
      非空 `dataflow_steps` list？
PASS：collector `submitted_findings` 有 ≥1 条目，且首条目 `dataflow_steps`
      存在且是非空 list（或至少 list 类型，宽容）。
FAIL：GLM 从不发 submit_finding / 字段缺失 / 字段非 list → 回查 Task 3/4
      schema 与 prompt 指引是否被 GLM 正确解析。

复现：
    cd /root/shannon-py
    SUPERNOVA_AI_PROVIDER=glm-anthropic python scripts/validate_claude_dataflow_probe.py
    # --dry-run: 校验 prompt 装载 + schema 一致 + collector 工具定义含
    #            dataflow_steps，不调真 LLM，须 exit 0。

exit 0 = 通过；非 0 = 失败。
"""
import argparse
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

# 工作目录可能是 /root/supernova（CI/Linux）或本仓库根（macOS）。两种都支持。
_PROFILE_CANDIDATES = [
    Path("/root/supernova/.env.profiles/glm-anthropic.env"),
    Path(__file__).resolve().parent.parent / ".env.profiles" / "glm-anthropic.env",
]
PROFILE = next((p for p in _PROFILE_CANDIDATES if p.exists()), _PROFILE_CANDIDATES[-1])


def load_profile() -> None:
    for line in PROFILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
    # 强制 anthropic 引擎（CLI 原生有 Task），覆盖任何默认。
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


# ── 最小 vuln prompt 片段：finding_submission 段（提取自 prompts/vuln-injection.txt
#    §<finding_submission>…</dataflow_steps_guidance>）+ 一个最小 source→sink 场景。
#    不 @include 完整 vuln prompt（依赖 recon deliverable 等 render-time 模板变量），
#    只内联触发 submit_finding + dataflow_steps 填充所需的最小指引。
FINDING_SUBMISSION_SNIPPET = """\
<finding_submission>
**SUBMIT IMMEDIATELY, ONE PER CALL:** As soon as you conclude an endpoint/component \
is **vulnerable**, call the `submit_finding` tool with that single finding — one \
finding per call. Do NOT batch multiple findings into one call; do NOT hold findings \
until the end of the session.

**Structure:** Each `submit_finding` argument MUST follow this exact format:
        {
            "ID": "unique ID for each vulnerability (e.g. INJ-VULN-XX)",
            "title": "一句话描述性标题，编码漏洞类别 + 位置，用简体中文撰写",
            "vulnerability_type": "SQLi | CommandInjection | LFI | RFI | SSTI | PathTraversal | InsecureDeserialization",
            "externally_exploitable": true | false,
            "source": "param name & file:line. MUST be a SINGLE source.",
            "path": "MUST start with 'METHOD /route' of an HTTP entry when HTTP-reachable, then the brief hop list.",
            "sink_call": "file:line and function/method.",
            "slot_type": "SQL-val | SQL-like | SQL-num | CMD-argument | ...",
            "sanitization_observed": "name & file:line (all of them, in order).",
            "concat_occurrences": "each concat/format/join with file:line.",
            "verdict": "safe | vulnerable.",
            "mismatch_reason": "if vulnerable, 1–2 lines in plain language.",
            "witness_payload": "the payload VALUE itself — no param-name prefix.",
            "confidence": "high | med | low.",
            "notes": "assumptions, untraversed branches, anything unusual.",
            "dataflow_steps": [
                {"label": "UserController.list", "file": "app/controllers/userController.js", "line": 25, "protection": null},
                {"label": "orm.escape", "file": "app/helpers/query.js", "line": 30, "protection": "orm.escape"}
            ]
        }

<dataflow_steps_guidance>
`dataflow_steps`: 按污点传播顺序，列出从 source 到 sink 经过的每个节点。
- 每个节点给 `label`（函数名或调用点）、`file`、`line`（未知填 null）。
- 若该节点有防护（sanitizer / 编码 / 校验），在 `protection` 填防护名；无防护填 null。
- 防护有效则该节点之后不应再出现未防护的传播——防护被绕过则继续列后续节点。
- 只列你实际追踪到的节点；没有完整路径时留空数组或省略。
</dataflow_steps_guidance>
</finding_submission>
"""


def build_prompt() -> str:
    """最小 vuln prompt：role + finding_submission 段 + 一个最小 source→sink 场景，
    驱动 GLM 发 submit_finding 并填 dataflow_steps。不依赖 recon deliverable。"""
    return (
        "You are an Injection Analysis Specialist (SQLi / Command Injection).\n"
        "You have ONE task: analyze the file `app.py` in your cwd and, if vulnerable, "
        "call the `submit_finding` tool with a single finding.\n\n"
        "TOOL USAGE: Use the `submit_finding` tool to submit your finding. "
        "Do NOT write prose only — you MUST call submit_finding.\n\n"
        f"{FINDING_SUBMISSION_SNIPPET}\n\n"
        "SCENARIO: app.py defines `get_user(name)` which reads `name` from a URL "
        "parameter and concatenates it directly into a SQL query string passed to "
        "cursor.execute() — no sanitization, no parameterization. This is a textbook "
        "SQL injection (source = request param `name`, sink = cursor.execute).\n\n"
        "TASK: Confirm get_user(name) is SQL-injectable and call submit_finding with "
        "verdict=\"vulnerable\", a witness_payload, and a non-empty dataflow_steps list "
        "tracing name → cursor.execute (at least 2 nodes)."
    )


def _make_target_repo() -> Path:
    """最小 target：app.py 含裸字符串拼接 SQL（经典 SQLi）。"""
    target = Path(tempfile.mkdtemp(prefix="claude_dataflow_probe_"))
    (target / "app.py").write_text(
        "import sqlite3\n"
        "def get_user(name):\n"
        "    conn = sqlite3.connect('db')\n"
        "    cur = conn.cursor()\n"
        "    cur.execute(\"SELECT * FROM users WHERE name='\" + name + \"'\")\n"
        "    return cur.fetchone()\n"
    )
    return target


def _dry_run() -> int:
    """Dry-run：不调真 LLM，校验 prompt 装载 + collector schema 含 dataflow_steps +
    bridge 双引擎工具定义含 dataflow_steps（单点定义不变量）。须 exit 0。"""
    prompt = build_prompt()
    assert "dataflow_steps" in prompt, "prompt 装载失败：缺 dataflow_steps 指引"
    assert "submit_finding" in prompt, "prompt 装载失败：缺 submit_finding 指引"
    assert "finding_submission" in prompt, "prompt 装载失败：缺 finding_submission 段"
    print(f"[dry-run] prompt 装载 OK（{len(prompt)} chars），含 finding_submission + "
          f"dataflow_steps 指引")

    from supernova_core.collectors.bridge import build_claude_mcp_server, build_openai_tools
    from supernova_core.collectors.vuln import make_vuln_collector

    collector = make_vuln_collector("injection")
    sections = collector.section_schemas
    submit_section = next(s for s in sections if s.tool_name == "submit_finding")
    assert submit_section.mode == "append", "submit_finding 应为 append 模式"
    schema_props = submit_section.json_schema["properties"]
    assert "dataflow_steps" in schema_props, "collector submit_finding schema 缺 dataflow_steps"
    dfs = schema_props["dataflow_steps"]
    assert dfs["type"] == "array", f"dataflow_steps 非 array: {dfs.get('type')}"
    assert "items" in dfs and dfs["items"]["type"] == "object", "dataflow_steps items 非 object"
    print(f"[dry-run] collector submit_finding schema 含 dataflow_steps（type=array, "
          f"items=object, {list(dfs['items']['properties'].keys())}）")

    # 双引擎工具定义均含 dataflow_steps（单点定义不变量，对齐 Task 3 测试）
    oai_tools = build_openai_tools(collector)
    oai_tool = next(t for t in oai_tools if t.name == "submit_finding")
    oai_props = oai_tool.params_json_schema["properties"]
    assert "dataflow_steps" in oai_props, "openai submit_finding 工具定义缺 dataflow_steps"

    # claude 侧（本探针引擎）：经 bridge → SDK MCP server list_tools → Tool.inputSchema
    import asyncio as contextlib_asyncio  # noqa: F401
    from mcp.types import ListToolsRequest
    server = build_claude_mcp_server(collector)["instance"]
    handler = server.request_handlers[ListToolsRequest]
    result = asyncio.run(handler(ListToolsRequest(method="tools/list")))
    claude_tool = next(t for t in result.root.tools if t.name == "submit_finding")
    claude_props = claude_tool.inputSchema["properties"]
    assert "dataflow_steps" in claude_props, "claude submit_finding 工具定义缺 dataflow_steps"
    assert oai_props["dataflow_steps"] == claude_props["dataflow_steps"], \
        "双引擎 dataflow_steps schema 不一致（违反单点定义不变量）"
    print(f"[dry-run] 双引擎 submit_finding 工具定义均含 dataflow_steps，schema 一致")
    print("[dry-run] PASS — prompt + schema + bridge 双引擎一致性校验通过（未调真 LLM）")
    return 0


async def _real_run(timeout: float) -> int:
    """真机跑：调 run_claude_prompt，断言 collector submitted_findings 含 dataflow_steps。"""
    from supernova_core.agents.runner import run_claude_prompt
    from supernova_core.collectors.vuln import make_vuln_collector

    target = _make_target_repo()
    prompt = build_prompt()
    collector = make_vuln_collector("injection")
    logger = RecordingLogger()
    t0 = time.time()
    print(f"[probe] target={target}  provider=anthropic_api (glm-anthropic)  "
          f"profile={PROFILE}  model_tier=medium")
    try:
        result = await asyncio.wait_for(
            run_claude_prompt(
                prompt=prompt,
                repo_path=str(target),
                model_tier="medium",
                tool_audit_logger=logger,
                collector=collector,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        print(f"RESULT: TIMEOUT (>{timeout}s)")
        return 2

    dt = time.time() - t0
    print("=" * 64)
    print(f"duration={dt:.1f}s  turns={getattr(result, 'turns', None)}  "
          f"cost={getattr(result, 'cost', None)}  success={result.success}")
    if result.error:
        print(f"ERROR: {result.error}")
    print(f"TOOLS CALLED ({len(logger.tools)}): {logger.tools}")
    # claude 引擎经 MCP 暴露 collector，工具名带命名空间前缀
    # （如 mcp__shannon-collector__submit_finding）；按后缀匹配。
    submit_used = any(t.lower().endswith("submit_finding") for t in logger.tools)
    print(f">>> submit_finding TOOL USED: {'YES ✅' if submit_used else 'NO ❌'}")
    for name, params in logger.tool_params:
        if name.lower().endswith("submit_finding"):
            print(f"    {name} call params: {params}")

    # 断言点：collector submitted_findings 非空 + dataflow_steps 非空 list
    bag = collector.get_all()
    submitted = bag.get("submitted_findings", [])
    print(f"\n>>> submitted_findings count: {len(submitted)}")
    if not submitted:
        print("RESULT: FAIL — collector submitted_findings 为空（GLM 未发 submit_finding）")
        print("\n--- FINAL ANSWER ---")
        print((result.text or "(empty)")[:1500])
        return 1

    first = submitted[0]
    dfs = first.get("dataflow_steps")
    print(f">>> first finding dataflow_steps: {dfs!r}")
    if not isinstance(dfs, list):
        print(f"RESULT: FAIL — dataflow_steps 非 list（{type(dfs).__name__}）")
        print("\n--- FIRST FINDING ---")
        import json
        print(json.dumps(first, ensure_ascii=False, indent=2)[:1500])
        return 1
    if not dfs:
        print("RESULT: FAIL — dataflow_steps 是空 list（至少要求 list 类型，但硬验收期"
              "望非空）")
        print("\n--- FIRST FINDING ---")
        import json
        print(json.dumps(first, ensure_ascii=False, indent=2)[:1500])
        return 1

    print(f"RESULT: PASS ✅ — submitted_findings[0] dataflow_steps 含 {len(dfs)} 个节点")
    print("\n--- FIRST FINDING (dataflow_steps) ---")
    for i, step in enumerate(dfs):
        print(f"  [{i}] {step}")
    print("\n--- FINAL ANSWER ---")
    print((result.text or "(empty)")[:800])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="不调真 LLM，只校验 prompt 装载 + schema 一致 + "
                             "collector 工具定义含 dataflow_steps，须 exit 0")
    parser.add_argument("--timeout", type=float, default=90.0,
                        help="真机跑单次 timeout（秒，默认 90）；超时即报告，不重试")
    args = parser.parse_args()

    if args.dry_run:
        # dry-run 不需 profile（不调真 LLM），但仍 init 以复用导入路径
        try:
            load_profile()
        except FileNotFoundError:
            print(f"[dry-run] profile {PROFILE} 不存在，dry-run 不依赖 profile，继续")
        return _dry_run()

    load_profile()
    return asyncio.run(_real_run(args.timeout))


if __name__ == "__main__":
    sys.exit(main())
