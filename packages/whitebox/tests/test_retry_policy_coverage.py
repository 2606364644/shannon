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


def _poc_call_nearest_try_swallows(source: str) -> tuple[bool, bool]:
    """(found, swallows):found=generate_poc_report execute_activity 调用存在;
    swallows=该调用的*最近*包裹 Try 的 except 处理不 re-raise(吞掉异常)。

    必须查“最近”而非“任一”祖先 Try:两轨 workflow 整体包在一个全局 try/except 里
    (其 except 设 status=failed 并 raise/return failed),PoC 调用处在其中会让
    “任一 Try 祖先”的粗检 spuriously pass。真正契约:PoC 调用须被自己的本地
    try/except(pass)包住,异常不冒泡到全局 FAILED 处理器。
    """
    tree = ast.parse(source)
    parents: dict = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    target = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute_activity"
                and node.args and isinstance(node.args[0], ast.Attribute)
                and node.args[0].attr == "generate_poc_report"):
            target = node
            break
    if target is None:
        return (False, False)

    cur = parents.get(target)
    nearest_try = None
    while cur is not None:
        if isinstance(cur, ast.Try):
            nearest_try = cur
            break
        cur = parents.get(cur)
    if nearest_try is None or not nearest_try.handlers:
        return (True, False)

    def _harmful(node) -> bool:
        """处理体是否“传播失败”:re-raise / return / 给 self._state.status 赋值都算。
        全局 workflow try 的 except 一定会落入其中之一(set status=failed 并 raise/return),
        而本地 PoC 兜底 try 的 `except Exception: pass` 三者皆无 → swallows=True。
        """
        for n in ast.walk(node):
            if isinstance(n, (ast.Raise, ast.Return)):
                return True
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if (isinstance(t, ast.Attribute) and t.attr == "status"
                            and isinstance(t.value, ast.Attribute)
                            and isinstance(t.value.value, ast.Name)
                            and t.value.value.id == "self"):
                        return True
        return False

    swallows = not any(_harmful(h) for h in nearest_try.handlers)
    return (True, swallows)


def test_poc_activity_call_is_wrapped_in_try():
    """Fix A(§8 契约硬化):generate_poc_report 的 execute_activity 最近的包裹 try 必须
    吞掉异常(不 re-raise/return/set status=failed),否则 Temporal start_to_close_timeout
    抛 ActivityError 会冒泡到 workflow 全局 except(设 status=failed 并 raise/return)→
    workflow FAILED。sentinel_dashboard 2026-07-22 实测回归。

    注:本轨 workflow 整体在一个全局 try/except 里(whitebox: set failed+raise;
    blackbox: set failed+return),故“任一 Try 祖先”粗检会假绿;此处查最近 Try 且
    要求其处理体不传播失败。
    """
    source = WORKFLOW_FILE.read_text()
    found, swallows = _poc_call_nearest_try_swallows(source)
    assert found, "找不到 generate_poc_report execute_activity 调用 — 锚点接线坏了"
    assert swallows, (
        "generate_poc_report execute_activity 最近包裹 try 未吞掉异常(处理 re-raise 或无本地 try) — "
        "PoC timeout 会冒泡到全局 FAILED 处理器,击穿 §8 非阻塞契约"
    )
