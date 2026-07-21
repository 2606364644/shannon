"""Activity 注册完整性护栏：断言 worker 注册集合 == @activity.defn 定义集合。

用 AST 解析源码（不依赖运行期 import、不连 temporal），既抓「漏注册」(missing)
也抓「幽灵注册」(extra)。供 blackbox/whitebox test_worker 复用，防 temporalio
activity 漏注册导致 workflow 卡死（见
docs/superpowers/specs/2026-06-29-blackbox-finalize-report-worker-registration-design.md）。
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType
from typing import Sequence


def _activity_def_names(source: str) -> set[str]:
    """从源码提取所有 @activity.defn / @activity.defn(...) 装饰的函数名。"""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and target.attr == "defn":
                names.add(node.name)
    return names


def _registered_activity_names(source: str) -> set[str]:
    """从 worker 源码提取所有 Worker(..., activities=[...]) 里注册的 activity 名。

    合并所有 Worker(...) 调用的 activities= 关键字（防多实例化），仅取 Name 节点。
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        callee = (
            func.id if isinstance(func, ast.Name)
            else (func.attr if isinstance(func, ast.Attribute) else None)
        )
        if callee != "Worker":
            continue
        for kw in node.keywords:
            if kw.arg != "activities" or not isinstance(kw.value, ast.List):
                continue
            for el in kw.value.elts:
                if isinstance(el, ast.Name):
                    names.add(el.id)
    return names


def assert_all_activities_registered(
    worker_module: ModuleType,
    activities_modules: Sequence[ModuleType],
) -> None:
    """断言 worker 注册的 activity 集合 == activities 模块定义的 @activity.defn 集合。

    worker_module: 含 Worker(..., activities=[...]) 调用的 worker 模块。
    activities_modules: 定义 @activity.defn 的模块列表（支持多模块）。
    不等则 AssertionError 报 missing / extra diff（pytest 友好）。
    """
    expected: set[str] = set()
    for mod in activities_modules:
        expected |= _activity_def_names(Path(mod.__file__).read_text(encoding="utf-8"))
    registered = _registered_activity_names(
        Path(worker_module.__file__).read_text(encoding="utf-8")
    )
    missing = expected - registered
    extra = registered - expected
    assert not missing and not extra, (
        f"activity registration mismatch in {worker_module.__name__}: "
        f"missing (defined but not registered)={sorted(missing)}, "
        f"extra (registered but not defined)={sorted(extra)}"
    )
