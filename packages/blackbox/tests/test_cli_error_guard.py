"""防回归：blackbox start 内的 run_scan(...) 必须位于 try 块内。

CLI 顶层 try/except 是 workflow 失败友好展示的不变量（见
docs/superpowers/specs/2026-06-28-cli-workflow-failure-friendly-display-design.md §8）。
有人若误删 try/except 会让裸 traceback 回归，本锚点守住。
"""
import ast
from pathlib import Path

CLI_FILE = (
    Path(__file__).resolve().parents[1]
    / "src" / "shannon_blackbox" / "cli" / "main.py"
)


def _start_func(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "start":
            return node
    return None


def test_run_scan_call_is_inside_try():
    tree = ast.parse(CLI_FILE.read_text(encoding="utf-8"))
    start = _start_func(tree)
    assert start is not None, "未找到 start 命令"

    try_nodes = [n for n in ast.walk(start) if isinstance(n, ast.Try)]
    run_scan_calls = [
        n for n in ast.walk(start)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "run_scan"
    ]
    assert run_scan_calls, "start 内未找到 run_scan(...) 调用"

    for call in run_scan_calls:
        inside = any(call in ast.walk(t) for t in try_nodes)
        assert inside, (
            "run_scan(...) 必须位于 try 块内（CLI 顶层友好错误展示不变量）。"
        )
