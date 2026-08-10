"""Sandbox 确定性守卫：WhiteboxScanWorkflow.run 体内禁止调用文件 I/O API。

Temporal workflow sandbox 要求 run() 确定性——禁止 glob/文件 I/O 等非确定性操作，否则抛
RestrictedWorkflowAccessError → WorkflowTask 反复 TimedOut → 整个 scan failed。本守卫覆盖
cleanup_auth_state_sync（glob auth-state*.json，须挪进 cleanup_auth_state_activity）。对齐
blackbox test_sandbox_safety.py。本次只锁 cleanup_auth_state_sync（真机 NodeGoat 扫描直接死因），
其余 FORBIDDEN 后续按需补。
"""
import ast
from pathlib import Path

WORKFLOW_FILE = (
    Path(__file__).resolve().parents[1]
    / "src" / "supernova_whitebox" / "pipeline" / "workflows.py"
)
WORKER_FILE = (
    Path(__file__).resolve().parents[1]
    / "src" / "supernova_whitebox" / "worker.py"
)

# workflow run() 体内禁止直接调用的文件 I/O 函数（须挪进 activity）。
FORBIDDEN_FUNCS = {
    "cleanup_auth_state_sync",  # glob auth-state*.json，须挪进 cleanup_auth_state_activity
}


def _run_body_nodes(tree: ast.AST) -> list[ast.AST]:
    """返回 WhiteboxScanWorkflow.run 方法体的所有 AST 节点（含嵌套）。"""
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and cls.name == "WhiteboxScanWorkflow":
            for item in cls.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "run":
                    return list(ast.walk(item))
    return []


def test_run_body_has_no_forbidden_sandbox_calls():
    """WhiteboxScanWorkflow.run 体内不得直接调 cleanup_auth_state_sync（glob 文件 I/O）。

    真机根因：带 auth config 的白盒扫描登录后生成 auth-state.json，run() finally 裸调
    cleanup_auth_state_sync（glob）→ 抛 RestrictedWorkflowAccessError → WorkflowTask 反复
    TimedOut → scan failed。须挪进 cleanup_auth_state_activity（worker 进程不受 sandbox 限）。
    """
    tree = ast.parse(WORKFLOW_FILE.read_text())
    nodes = _run_body_nodes(tree)
    assert nodes, "未找到 WhiteboxScanWorkflow.run —— 守卫测试接线坏了"

    hits = [
        node.func.id for node in nodes
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in FORBIDDEN_FUNCS
    ]
    assert not hits, (
        f"WhiteboxScanWorkflow.run 体内发现 {len(hits)} 处 sandbox 不安全引用:\n  "
        + "\n  ".join(sorted(set(hits)))
        + "\n文件 I/O 应挪进 activity（cleanup_auth_state_activity）。"
    )


def test_worker_registers_cleanup_auth_state_activity():
    """防回归：cleanup_auth_state activity 必须在 worker.py 注册（import + activities 列表）。

    cleanup_auth_state_sync 用 glob.glob，workflow sandbox 内直调抛 RestrictedWorkflowAccessError
    → WorkflowTask 反复 TimedOut → scan failed（与 blackbox 同根因）。挪进 activity 后须在 worker
    注册，否则 Temporal 找不到 activity 实现而崩溃。见 temporalio-activity-worker-registration
    教训（新 activity 三处同步：定义/调用/worker 注册，注册易漏）。
    """
    worker_src = WORKER_FILE.read_text()
    count = worker_src.count("cleanup_auth_state")
    assert count >= 2, (
        f"cleanup_auth_state 在 worker.py 仅出现 {count} 次，预期 >= 2"
        "（import 一处 + activities 列表一处）。"
    )
