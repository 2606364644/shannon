"""WorkflowLogger.initialize: event_file 参数优先, None 回落 env(CLI 零改动).

C1 Phase B Task 2: web 提交端是另一进程(worker 容器), env 到不了容器边界 →
必须从 PipelineInput.event_file 拿路径。CLI 仍用 env(event_file=None 走 env 兜底)。
"""
import pytest

from shannon_core.audit.workflow_logger import WorkflowLogger
from shannon_core.models.metrics import SessionMetadata


def _make_logger(tmp_path) -> WorkflowLogger:
    # 最小构造: 跳过 rich/console, 只走 FileLogRenderer 路径。
    # output_path 控制审计目录落点(避免污染真实 workspaces)。
    meta = SessionMetadata(id="wf-test", web_url=None, output_path=str(tmp_path))
    return WorkflowLogger(session_metadata=meta, use_rich=False, console=None, dashboard=None)


@pytest.mark.asyncio
async def test_initialize_uses_explicit_event_file_over_env(tmp_path, monkeypatch):
    """显式 event_file 参数挂 StructuredEventRenderer 到该路径, 不读 env。"""
    monkeypatch.setenv("SHANNON_WEB_EVENT_FILE", "/should/not/use")
    logger = _make_logger(tmp_path)
    ef = tmp_path / "events.ndjson"
    await logger.initialize(event_file=str(ef))
    # StructuredEventRenderer 挂上了, 且路径对(StructuredEventRenderer._path 存 str)
    paths = [getattr(r, "_path", None) for r in logger._dispatcher._renderers]
    assert str(ef) in paths, f"未挂到 {ef}, renderers paths={paths}"
    await logger.close()


@pytest.mark.asyncio
async def test_initialize_falls_back_to_env_when_event_file_none(tmp_path, monkeypatch):
    """event_file=None 时回落 env(CLI 路径不变)。"""
    ef = tmp_path / "from_env.ndjson"
    monkeypatch.setenv("SHANNON_WEB_EVENT_FILE", str(ef))
    logger = _make_logger(tmp_path)
    await logger.initialize(event_file=None)
    paths = [getattr(r, "_path", None) for r in logger._dispatcher._renderers]
    assert str(ef) in paths, f"未回落到 env {ef}, renderers paths={paths}"
    await logger.close()


@pytest.mark.asyncio
async def test_initialize_no_event_file_no_env_no_renderer(tmp_path, monkeypatch):
    """两边都无 → 不挂 StructuredEventRenderer(纯 CLI rich 路径)。"""
    monkeypatch.delenv("SHANNON_WEB_EVENT_FILE", raising=False)
    logger = _make_logger(tmp_path)
    await logger.initialize(event_file=None)
    types = [type(r).__name__ for r in logger._dispatcher._renderers]
    assert "StructuredEventRenderer" not in types
    await logger.close()
