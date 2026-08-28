"""Per-scan env 覆盖层：让进程级 ``SUPERNOVA_*`` 配置支持工作区级（per-workspace）覆盖。

worker 是独立长驻进程，多个工作区的扫描在其中并发跑；进程级开关直接读
``os.environ`` 会让并发扫描互相串台（这正是 ws 配置页把它们标为「仅全局生效」
的原因）。本模块建一个按 ``workflow_id`` 索引的覆盖层：扫描期读取点经
:func:`ws_getenv` 优先读当前扫描的覆盖值，回落 ``os.environ``。

范式照搬 :mod:`supernova_core.audit.session_registry`（``_SESSIONS`` dict +
``_resolve_wf_id``）。生命周期：worker ``setup_display`` activity 入口
:func:`set_scan_env`，finalize/cleanup 出口 :func:`clear_scan_env`。CLI 路径不注入
→ ``ws_getenv`` 透明回落 ``os.environ``（CLI 单扫描无并发问题）。

语义不变量：未注入覆盖层时 ``ws_getenv`` 与 ``os.environ.get`` 行为完全一致，
保证 CLI / 单测 / 未改造调用点零行为变化。
"""
from __future__ import annotations

import os

# 进程级 per-scan 覆盖层：workflow_id → {env_key: value}。
# None 键 = CLI / 无 activity 上下文（仅测试显式置入；CLI 路径不调 set_scan_env）。
_SCAN_ENV: dict[str | None, dict[str, str]] = {}


def _resolve_wf_id(explicit: str | None = None) -> str | None:
    """解析当前 workflow_id。

    优先级：显式参数 > ``activity.info().workflow_id``（worker activity 上下文）
    > ``None``（CLI / 无 activity 上下文 → ws_getenv 回落 os.environ）。

    对齐 ``session_registry._resolve_wf_id``，但 CLI 兜底返回 ``None`` 而非
    ``'_cli'``——scan_env 的 CLI 路径本就该回落 os.environ（``_SCAN_ENV.get(None)``
    通常为空）。惰性 import temporalio 避免硬依赖（CLI/单测可能未装）。
    """
    if explicit:
        return explicit
    try:
        from temporalio import activity

        wf_id = activity.info().workflow_id
        if wf_id:
            return wf_id
    except RuntimeError:
        # 不在 activity 上下文内（CLI / 普通 Python 调用）。
        pass
    except ImportError:
        # temporalio 未安装（部分单测环境）。
        pass
    return None


def set_scan_env(overrides: dict[str, str] | None, workflow_id: str | None = None) -> None:
    """worker activity 入口：把该扫描的工作区 env 覆盖值注册进覆盖层。"""
    _SCAN_ENV[_resolve_wf_id(workflow_id)] = dict(overrides or {})


def clear_scan_env(workflow_id: str | None = None) -> None:
    """worker activity 出口：清理该扫描的覆盖值（防进程长驻后覆盖层无限增长）。"""
    _SCAN_ENV.pop(_resolve_wf_id(workflow_id), None)


def get_scan_env() -> dict[str, str] | None:
    """读当前扫描的覆盖层（测试 + 内部诊断用）。"""
    return _SCAN_ENV.get(_resolve_wf_id())


def ws_getenv(key: str, default: str | None = None) -> str | None:
    """优先读当前扫描的工作区覆盖，回落 ``os.environ.get``。

    扫描期读取点的统一入口：把 ``os.environ.get("SUPERNOVA_*")`` 换成本函数即可
    让进程级配置支持 per-workspace 覆盖。未注入覆盖层时与 ``os.environ.get`` 等价。
    """
    overrides = _SCAN_ENV.get(_resolve_wf_id())
    if overrides and key in overrides:
        return overrides[key]
    return os.environ.get(key, default)


def ws_override_get(key: str) -> str | None:
    """仅读当前扫描的工作区覆盖值；无覆盖层 / 键不在层内 → ``None``（**不回落** ``os.environ``）。

    与 :func:`ws_getenv` 的区别：读取点要「分层合并」（低层基座 + ws 选择性压过）
    而非「整体替换」时用本函数——如 pricing 工作区层（spec 2026-08-28
    global-pricing-console §4.1）：ws 覆盖只压过 ws 定义的模型，其余模型继承
    process / 全局层。
    """
    overrides = _SCAN_ENV.get(_resolve_wf_id())
    if overrides and key in overrides:
        return overrides[key]
    return None


def ws_getenv_bool(key: str, default: bool) -> bool:
    """布尔版 ws_getenv：``'0'/'false'/'no'/'off'`` → False，其余非空 → True，未设 → default。

    语义对齐 ``concurrency._is_truthy_env``，供布尔开关读取点直接使用。
    """
    raw = ws_getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}
