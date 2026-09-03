"""架构不变量守护（spec 2026-09-03-topology-preanalysis-worker-migration §4.4）：
web 进程不执行任何 agent / 不加载任何 prompt。

2026-09-03 拓扑预分析迁出后 web 零 agent 执行点。此前 web 进程内执行 agent 已两次
咬人（prompts 漏拷致预分析必失败；web 最终镜像不带 node/claude，claude-agent-sdk
引擎即失败）——worker 容器天然资源齐，web 侧 LLM 能力一律经 temporal 提交 worker
（AuthValidationWorkflow / TopologyAnalysisWorkflow 模式）。本测试锁定该不变量，
防「顺手在 web 再起一个 agent 执行点」的静默回归。模式抄 core 的
test_static_dataflow_hints_decoupling.py（不变量入测试）。

黑名单（AST 级 import 检查，注释/字符串不误报）：
- supernova_core.prompts*          —— PromptManager（prompt 加载）
- supernova_core.agents.runner     —— run_claude_prompt / UsageSink（agent 执行）
agents.providers（config 构造）/ agents.pricing（价目表）不属执行面，合法。
"""
import ast
from pathlib import Path

WEB_SRC = Path(__file__).resolve().parents[1] / "src" / "supernova_web"

FORBIDDEN_MODULES = (
    "supernova_core.prompts",
    "supernova_core.agents.runner",
)


def test_web_package_never_imports_agent_execution():
    offenders: list[str] = []
    for path in sorted(WEB_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.append(node.module)
            elif isinstance(node, ast.Import):
                mods.extend(alias.name for alias in node.names)
            for mod in mods:
                if mod == FORBIDDEN_MODULES or mod.startswith(
                        tuple(f"{m}." for m in FORBIDDEN_MODULES)):
                    offenders.append(
                        f"{path.relative_to(WEB_SRC)}:{node.lineno} imports {mod}")
    assert not offenders, (
        "web 包出现 agent 执行 import（PromptManager / agents.runner）——web 进程"
        "不得执行 agent（资源清单分叉已两次咬人：prompts 漏拷、node/claude 缺失）。"
        "web 侧 LLM 能力经 temporal 提交 worker（见 spec 2026-09-03-"
        "topology-preanalysis-worker-migration §4.4）: " + "; ".join(offenders)
    )
