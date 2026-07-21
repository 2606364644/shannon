import pytest

from supernova_core.audit.workflow_logger import WorkflowLogger
from supernova_core.models.metrics import SessionMetadata


def _make_logger(tmp_path) -> WorkflowLogger:
    # 最小构造：跳过 rich/console，只走 FileLogRenderer 路径。
    # 真实构造签名是 (session_metadata, use_rich=False, console=None, dashboard=None)；
    # output_path 控制审计目录落点（避免污染真实 workspaces）。
    meta = SessionMetadata(id="wf-test", web_url=None, output_path=str(tmp_path))
    return WorkflowLogger(session_metadata=meta, use_rich=False, console=None, dashboard=None)


@pytest.mark.asyncio
async def test_env_unset_does_not_mount_web_renderer(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERNOVA_WEB_EVENT_FILE", raising=False)
    logger = _make_logger(tmp_path)
    await logger.initialize(workflow_id="wf-test")
    types = [type(r).__name__ for r in logger._dispatcher._renderers]
    assert "StructuredEventRenderer" not in types
    await logger.close()


@pytest.mark.asyncio
async def test_env_set_mounts_web_renderer(tmp_path, monkeypatch):
    target = tmp_path / "events.ndjson"
    monkeypatch.setenv("SUPERNOVA_WEB_EVENT_FILE", str(target))
    logger = _make_logger(tmp_path)
    await logger.initialize(workflow_id="wf-test")
    types = [type(r).__name__ for r in logger._dispatcher._renderers]
    assert "StructuredEventRenderer" in types
    await logger.close()


@pytest.mark.asyncio
async def test_close_calls_renderer_close(tmp_path, monkeypatch):
    target = tmp_path / "events.ndjson"
    monkeypatch.setenv("SUPERNOVA_WEB_EVENT_FILE", str(target))
    logger = _make_logger(tmp_path)
    await logger.initialize(workflow_id="wf-test")
    web_r = next(r for r in logger._dispatcher._renderers
                 if type(r).__name__ == "StructuredEventRenderer")
    # 强制触发一次 render，使 lazy-open 真正创建文件句柄（否则 _fh 本就是
    # None，close 遍历与否都无法被这个断言观测到）。
    from supernova_core.display.events import InfoEvent
    from supernova_core.display.formatters import format_log_time
    await web_r.render(InfoEvent(
        timestamp=format_log_time(), category="INFO", message="ping", level="info"))
    assert web_r._fh is not None  # render 后句柄已开
    await logger.close()
    assert web_r._fh is None  # close 遍历 renderers 后句柄已关
