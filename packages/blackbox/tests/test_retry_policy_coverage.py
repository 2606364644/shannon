"""回归锚点:每个 workflow.execute_activity 必须声明 retry_policy。

防止"裸奔→Temporal 默认≈无限重试"回归。详见
docs/superpowers/specs/2026-06-22-retry-policy-alignment-design.md。

注:generate_poc_report 相关锚点(retry_for('poc') + try 吞异常)已于阶段 2 删除——
黑盒不再调度 generate_poc_report(PoC=evidence,由 exploit agent 产,assemble 拼进综合报告)。
"""
import ast
from pathlib import Path

WORKFLOW_FILE = (
    Path(__file__).resolve().parents[1]
    / "src" / "supernova_blackbox" / "pipeline" / "workflows.py"
)


def _execute_activity_calls(source: str) -> list[ast.Call]:
    tree = ast.parse(source)
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute_activity"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "workflow"
        ):
            calls.append(node)
    return calls


def test_every_execute_activity_has_retry_policy():
    source = WORKFLOW_FILE.read_text()
    calls = _execute_activity_calls(source)
    assert calls, "no execute_activity calls found — 锚点测试接线坏了"
    missing = []
    for call in calls:
        if "retry_policy" not in {kw.arg for kw in call.keywords}:
            missing.append(ast.get_source_segment(source, call))
    assert not missing, (
        f"{len(missing)} 个 execute_activity 缺 retry_policy="
        f"(会落 Temporal 默认≈无限重试):\n"
        + "\n---\n".join(str(m) for m in missing)
    )
