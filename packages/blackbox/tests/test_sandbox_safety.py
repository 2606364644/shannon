"""Sandbox 确定性守卫：BlackboxScanWorkflow.run 体内禁止调用 env/cwd/文件 I/O API。

Temporal workflow sandbox 要求 run() 确定性——禁止 os.getenv / Path.cwd / 文件 I/O 等
非确定性操作，否则抛 RestrictedWorkflowAccessError。覆盖 resolve_workspaces_dir/
find_project_root/get_default_deliverables_subdir（env/cwd 解析）与 _load_correlation_context
（文件 I/O，已挪进 load_correlation_context activity，仅 --correlated-workspace 触发）。
任何需要在 run() 内拿路径/env/文件的操作，都必须经 input 字段或 activity 完成。
"""
import ast
from pathlib import Path

import pytest

WORKFLOW_FILE = (
    Path(__file__).resolve().parents[1]
    / "src" / "supernova_blackbox" / "pipeline" / "workflows.py"
)
WORKER_FILE = (
    Path(__file__).resolve().parents[1]
    / "src" / "supernova_blackbox" / "worker.py"
)

# 读 env/cwd 的函数名直接调用（ast.Call + ast.Name）
FORBIDDEN_FUNCS = {
    "resolve_workspaces_dir",
    "find_project_root",
    "get_default_deliverables_subdir",
    "_load_correlation_context",  # 文件 I/O（exists/read_text），须挪进 activity
    "parse_config",               # 读 yaml 文件 I/O，须挪进 resolve_blackbox_engine activity
    "sync_code_path_deny_rules",  # 写 ~/.claude/settings.json，须挪进 activity
    "has_valid_whitebox_results", # 读 queue 文件，须挪进 detect_whitebox_results activity
    "has_correlation_results",    # 读 correlation queue，须挪进 detect_whitebox_results activity
}
# 属性形式的不安全 API：(模块名, 属性名)；同时覆盖 os.environ[...] 与 os.getenv(...)/Path.cwd()
FORBIDDEN_ATTRS = {("os", "getenv"), ("os", "environ"), ("Path", "cwd")}
# 类方法形式的间接文件 I/O：ExploitationChecker.validate_queue/should_exploit/check_coverage。
# 这些方法内部走 aiofiles（async_path_exists/async_read_file → run_in_executor），workflow sandbox
# 内直调会抛 NotImplementedError（同 _load_correlation_context 的违规模式）。文件 I/O 须经
# validate_exploitation_queue activity 完成。
FORBIDDEN_METHODS = {
    "validate_queue", "should_exploit", "check_coverage",
    "write_config",    # engine.write_config 写 stealth config 文件，须挪进 activity
    "check_available", # engine.check_available 走 shutil.which(env/PATH)，须挪进 activity
}


def _run_body_nodes(tree: ast.AST) -> list[ast.AST]:
    """返回 BlackboxScanWorkflow.run 方法体的所有 AST 节点（含嵌套）。"""
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and cls.name == "BlackboxScanWorkflow":
            for item in cls.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "run":
                    return list(ast.walk(item))
    return []


def _forbidden_hits(nodes: list[ast.AST]) -> list[str]:
    hits = []
    for node in nodes:
        # 直接函数名调用：resolve_workspaces_dir(...) / find_project_root(...) 等
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_FUNCS:
                hits.append(node.func.id)
        # 属性访问/调用：os.getenv(...) / os.environ / Path.cwd(...)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if (node.value.id, node.attr) in FORBIDDEN_ATTRS:
                hits.append(f"{node.value.id}.{node.attr}")
        # 类方法间接文件 I/O：ExploitationChecker.validate_queue(...) / .should_exploit(...) / .check_coverage(...)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in FORBIDDEN_METHODS:
                hits.append(node.func.attr)
    return hits


def test_run_body_has_no_forbidden_sandbox_calls():
    tree = ast.parse(WORKFLOW_FILE.read_text())
    nodes = _run_body_nodes(tree)
    assert nodes, "未找到 BlackboxScanWorkflow.run —— 守卫测试接线坏了"

    hits = _forbidden_hits(nodes)
    assert not hits, (
        f"BlackboxScanWorkflow.run 体内发现 {len(hits)} 处 sandbox 不安全引用:\n  "
        + "\n  ".join(sorted(set(hits)))
        + "\nworkspaces 根应在 sandbox 外（CLI/worker）解析后通过 input.workspaces_root 传入；"
        "文件 I/O 应挪进 activity。"
    )


def test_run_body_execute_activity_has_at_most_two_positional_args():
    """防回归：workflow body 内 workflow.execute_activity(...) 位置参数 ≤ 2。

    temporalio 的 execute_activity(activity, args=None, *, ...) 最多 2 个位置参数
    （activity + args）。给 activity 传多个参数必须包成 args=[...]——直接展开成位置参数
    会抛 TypeError: takes from 1 to 2 positional arguments（sandbox-violation-fix 真机冒烟
    暴露：detect_whitebox_results / write_engine_config_for_session 误传 4 位置参数，workflow
    一跑到就崩）。本守卫填补原 AST 守卫只查 forbidden API、不查调用签名的盲区。
    """
    tree = ast.parse(WORKFLOW_FILE.read_text())
    nodes = _run_body_nodes(tree)
    assert nodes, "未找到 BlackboxScanWorkflow.run —— 守卫测试接线坏了"

    offenders = []
    for node in nodes:
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute_activity"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "workflow"
                and len(node.args) > 2):
            offenders.append(len(node.args))
    assert not offenders, (
        f"workflow.execute_activity 发现 {len(offenders)} 处位置参数 > 2（{offenders}）。"
        "temporalio execute_activity 最多 2 位置参数（activity + args），多参数须用 args=[...]"
        "关键字传入。"
    )


def test_worker_registers_load_correlation_context():
    """防回归：load_correlation_context activity 必须在 worker.py 注册（import + activities 列表）。

    新 activity 三处同步（定义/调用/worker 注册），第 3 处 worker 注册易漏（见
    temporalio-activity-worker-registration 教训：assemble_report/authz judge 都因漏注册崩过）。
    """
    worker_src = WORKER_FILE.read_text()
    count = worker_src.count("load_correlation_context")
    assert count >= 2, (
        f"load_correlation_context 在 worker.py 仅出现 {count} 次，预期 >= 2"
        "（import 一处 + activities 列表一处）。新 activity 必须在 worker 注册，否则运行时 "
        "Temporal 找不到 activity 实现而崩溃。"
    )


def test_worker_registers_validate_exploitation_queue():
    """防回归：validate_exploitation_queue activity 必须在 worker.py 注册。

    ExploitationChecker.validate_queue 含文件 I/O（aiofiles → run_in_executor），workflow sandbox
    内直调会抛 NotImplementedError。该调用经 activity 包装后，必须在 worker 注册，否则运行时
    Temporal 找不到 activity 实现而崩溃。见 temporalio-activity-worker-registration 教训。
    """
    worker_src = WORKER_FILE.read_text()
    count = worker_src.count("validate_exploitation_queue")
    assert count >= 2, (
        f"validate_exploitation_queue 在 worker.py 仅出现 {count} 次，预期 >= 2"
        "（import 一处 + activities 列表一处）。"
    )


def test_worker_registers_log_info_activity():
    """防回归：log_info_activity 必须在 worker.py 注册（import + activities 列表）。

    见 temporalio-activity-worker-registration 教训：新 activity 三处同步，第 3 处 worker
    注册易漏。提示经 activity 走显示通道，未注册则 workflow 调用时 Temporal 找不到 activity 崩。
    """
    worker_src = WORKER_FILE.read_text()
    count = worker_src.count("log_info_activity")
    assert count >= 2, (
        f"log_info_activity 在 worker.py 仅出现 {count} 次，预期 >= 2"
        "（import 一处 + activities 列表一处）。"
    )


def test_completed_branch_uses_exploit_result_to_outcome():
    """防回归：exploit completed 分支必须用 exploit_result_to_outcome 构造 AgentOutcome。

    run_exploit_agent 返回 AgentMetrics.model_dump() dict；completed 分支曾用
    getattr(result, "duration_s"|"cost_usd"|"turns") 取属性——getattr(dict) 永返 default
    + 字段名错位，导致 AgentOutcome 指标恒 0、format_exploit_summary 全 0（spec §2/§5.2，
    commit f16982b）。该锚点锁定 completed 分支走纯函数映射，不回退到 getattr(dict)。
    """
    src = WORKFLOW_FILE.read_text()
    assert "exploit_result_to_outcome(" in src, (
        "workflows.py 未调用 exploit_result_to_outcome —— exploit completed 分支必须用它"
        "构造 AgentOutcome（dict 字段映射），否则回到 getattr(dict) 指标归零 bug"
    )
    for forbidden in [
        'getattr(result, "duration_s"',
        'getattr(result, "cost_usd"',
        'getattr(result, "turns"',
    ]:
        assert forbidden not in src, (
            f"workflows.py 仍含 `{forbidden}` —— getattr(dict) 永返 default，"
            "exploit 指标归零（应改用 exploit_result_to_outcome）"
        )


@pytest.mark.parametrize("activity_name", [
    "resolve_blackbox_engine",
    "detect_whitebox_results",
    "write_engine_config_for_session",
    "cleanup_engine_configs",
])
def test_worker_registers_blackbox_config_activity(activity_name):
    """防回归：3 个 config/engine activity 必须在 worker.py 注册（import + activities 列表）。

    这 3 个 activity 承接原 workflow run() 体内的文件 I/O（parse_config / check_available /
    sync_code_path_deny_rules / write_config / has_valid_whitebox_results），把 sandbox 违规
    挪进 activity。见 temporalio-activity-worker-registration 教训：新 activity 三处同步
    （定义/调用/worker 注册），第 3 处 worker 注册易漏——漏注册则 Temporal 找不到 activity 崩溃。
    """
    worker_src = WORKER_FILE.read_text()
    count = worker_src.count(activity_name)
    assert count >= 2, (
        f"{activity_name} 在 worker.py 仅出现 {count} 次，预期 >= 2"
        "（import 一处 + activities 列表一处）。"
    )
