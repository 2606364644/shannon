"""run_code_index activity: GitNexus 不可用必须硬失败(不降级 minimal)。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from temporalio.exceptions import ApplicationError as ApplicationFailure

from shannon_whitebox.pipeline.activities import run_code_index
from shannon_whitebox.pipeline.shared import ActivityInput


@pytest.mark.asyncio
async def test_run_code_index_raises_when_gitnexus_unavailable(tmp_path):
    """GitNexus CLI 不可用 → run_code_index 抛 ApplicationFailure,不再降级。"""
    input = ActivityInput(repo_path=str(tmp_path), workspace_name="test")

    with patch("shannon_whitebox.audit.session_registry.get_audit_session") as mock_sess, \
         patch("shannon_core.code_index.gitnexus_engine.GitNexusEngine") as mock_engine_cls, \
         patch("shannon_whitebox.pipeline.activities._get_paths") as mock_paths:
        # track_step 是 async context manager。
        # 关键:__aexit__ 必须返回 falsy,否则会吞掉块内抛出的异常
        # (真实 track_step 在 __aexit__ 里 re-raise,见 shannon_core.audit.session)。
        cm = mock_sess.return_value.track_step.return_value
        cm.__aenter__ = AsyncMock(return_value=None)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_engine = MagicMock()
        mock_engine.is_available.return_value = False
        mock_engine_cls.return_value = mock_engine
        mock_paths.return_value = (tmp_path, tmp_path / "deliverables", tmp_path)

        with pytest.raises(ApplicationFailure, match="GitNexus"):
            await run_code_index(input)
