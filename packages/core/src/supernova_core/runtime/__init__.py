"""Runtime prerequisite detection, installation, and shared scan runner.

Re-export 改 PEP 562 lazy（2026-08-28 temporal-native-cancel）：workflow 沙箱
import 本包任何子模块（如 temporal_heartbeat——WhiteboxScanWorkflow/
BlackboxScanWorkflow 的取消放行 helper）时会先执行本 ``__init__``；旧版急切
``from .scan_runner import ...`` 连带执行 scan_runner → prerequisites 顶层
``Path(__file__).resolve()`` → 沙箱 RestrictedWorkflowAccessError。lazy 后
``__init__`` 无副作用，``from supernova_core.runtime import ScanCancelled`` 等
既有用法不变。
"""

_LAZY_EXPORTS = {
    "ScanCancelled",
    "ShutdownController",
    "await_workflow_with_shutdown",
    "poll_progress",
    "run_scan_graceful",
}


def __getattr__(name: str):  # noqa: ANN202 - PEP 562
    if name in _LAZY_EXPORTS:
        from . import scan_runner
        return getattr(scan_runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals()) + _LAZY_EXPORTS)
