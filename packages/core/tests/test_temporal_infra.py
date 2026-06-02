import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.services.temporal_infra import (
    get_compose_file,
    get_temporal_status,
    is_temporal_ready,
    start_temporal,
    stop_temporal,
)


class TestGetComposeFile:
    def test_returns_docker_compose_yml_in_project_root(self):
        result = get_compose_file()
        assert result.name == "docker-compose.yml"
        assert result.exists()

    def test_custom_path_overrides_default(self, tmp_path):
        custom = tmp_path / "custom-compose.yml"
        custom.write_text("services: {}")
        result = get_compose_file(custom)
        assert result == custom


class TestIsTemporalReady:
    @pytest.mark.asyncio
    async def test_returns_true_when_connect_succeeds(self):
        with patch("shannon_core.services.temporal_infra.Client") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.connect = AsyncMock(return_value=mock_client)
            mock_client.connection = MagicMock()
            result = await is_temporal_ready("localhost:7233")
        assert result is True
        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_connect_fails(self):
        with patch("shannon_core.services.temporal_infra.Client") as mock_client_cls:
            mock_client_cls.connect = AsyncMock(side_effect=Exception("connection refused"))
            result = await is_temporal_ready("localhost:7233")
        assert result is False


class TestStartTemporal:
    def test_calls_docker_compose_up(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}")
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            start_temporal(compose)
        mock_sp.run.assert_called_once()
        args = mock_sp.run.call_args[0][0]
        assert "docker" in args
        assert "compose" in args
        assert "up" in args
        assert "-d" in args

    def test_raises_on_failure(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}")
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            mock_sp.run.side_effect = subprocess.CalledProcessError(1, "docker")
            with pytest.raises(RuntimeError, match="Failed to start Temporal"):
                start_temporal(compose)


class TestStopTemporal:
    def test_calls_docker_compose_down(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}")
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            stop_temporal(compose)
        mock_sp.run.assert_called_once()
        args = mock_sp.run.call_args[0][0]
        assert "docker" in args
        assert "compose" in args
        assert "down" in args

    def test_raises_on_failure(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services: {}")
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            mock_sp.run.side_effect = subprocess.CalledProcessError(1, "docker")
            with pytest.raises(RuntimeError, match="Failed to stop Temporal"):
                stop_temporal(compose)


class TestGetTemporalStatus:
    @pytest.mark.asyncio
    async def test_returns_running_and_healthy(self):
        with (
            patch("shannon_core.services.temporal_infra.subprocess") as mock_sp,
            patch("shannon_core.services.temporal_infra.is_temporal_ready", new_callable=AsyncMock) as mock_ready,
        ):
            mock_sp.run.return_value = MagicMock(stdout="Up 5 minutes")
            mock_ready.return_value = True
            result = await get_temporal_status()
        assert result["container"] == "running"
        assert result["healthy"] is True

    @pytest.mark.asyncio
    async def test_returns_stopped_when_container_not_found(self):
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            mock_sp.run.side_effect = FileNotFoundError("docker not found")
            result = await get_temporal_status()
        assert result["container"] == "not found"
        assert result["healthy"] is False
