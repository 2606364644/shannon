"""回归锚点:每个 workflow.execute_activity 必须声明 retry_policy。

防止"裸奔→Temporal 默认≈无限重试"回归。详见
docs/superpowers/specs/2026-06-22-retry-policy-alignment-design.md。
"""
import ast
from pathlib import Path

WORKFLOW_FILE = (
    Path(__file__).resolve().parents[1]
    / "src" / "supernova_whitebox" / "pipeline" / "workflows.py"
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


def _retry_for_category(call) -> str | None:
    """从 retry_policy=retry_for("xxx") 提取 category 字符串;非该形式返回 None。"""
    retry_kws = [kw for kw in call.keywords if kw.arg == "retry_policy"]
    if not retry_kws:
        return None
    rpc = retry_kws[0].value
    if (
        isinstance(rpc, ast.Call)
        and isinstance(rpc.func, ast.Name)
        and rpc.func.id == "retry_for"
        and rpc.args
        and isinstance(rpc.args[0], ast.Constant)
    ):
        return rpc.args[0].value
    return None


def test_run_code_index_uses_bounded_code_index_retry():
    """run_code_index 必须 retry_for('code-index'),不能用 'standard'。

    'standard' = PRODUCTION_RETRY(maximum_attempts=8) 会把大仓 LLM sink
    discovery 的 10 分钟超时(幂等)放大成 8x ≈ 数小时的"卡死"(2026-06-30
    juice-shop 实测:attempt 1/2/3 各 10m10s 超时,PRE_RECON 早已完成但 gather
    等代码索引重试耗尽)。AST 锚点防止有人把调用点改回 'standard'。
    """
    source = WORKFLOW_FILE.read_text()
    calls = _execute_activity_calls(source)
    code_index_calls = [
        c for c in calls
        if c.args
        and isinstance(c.args[0], ast.Attribute)
        and c.args[0].attr == "run_code_index"
    ]
    assert code_index_calls, "run_code_index 的 execute_activity 未找到 — 锚点接线坏了"
    for call in code_index_calls:
        category = _retry_for_category(call)
        assert category == "code-index", (
            f"run_code_index 必须 retry_for('code-index'),当前是 {category!r}"
            f"(PRODUCTION_RETRY max 8 会放大超时)"
        )


def test_generate_poc_report_uses_bounded_poc_retry():
    """generate_poc_report 必须 retry_for('poc'),不能用 'standard'。

    'standard' = PRODUCTION_RETRY(max 8) 会把 PoC 串行 LLM 调用的
    start_to_close_timeout(幂等)放大成数小时卡死(2026-07-10 NodeGoat 实测:
    5 个 externally_exploitable 串行 llm_fill_gap 各 max_turns=50,5min timeout
    反复重入"白盒 PoC: 5 个" 1h43m+)。PoC 是报告增强、非关键路径(activity 内
    吞异常),用短重试。AST 锚点防止改回 'standard'。
    """
    source = WORKFLOW_FILE.read_text()
    calls = _execute_activity_calls(source)
    poc_calls = [
        c for c in calls
        if c.args
        and isinstance(c.args[0], ast.Attribute)
        and c.args[0].attr == "generate_poc_report"
    ]
    assert poc_calls, "generate_poc_report 的 execute_activity 未找到 — 锚点接线坏了"
    for call in poc_calls:
        category = _retry_for_category(call)
        assert category == "poc", (
            f"generate_poc_report 必须 retry_for('poc'),当前是 {category!r}"
            f"(PRODUCTION_RETRY max 8 会放大超时)"
        )
