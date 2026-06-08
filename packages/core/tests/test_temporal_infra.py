import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.services.temporal_infra import (
    ensure_infra,
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


class TestEnsureInfra:
    @pytest.mark.asyncio
    async def test_returns_immediately_if_already_ready(self):
        with patch("shannon_core.services.temporal_infra.is_temporal_ready", new_callable=AsyncMock) as mock_ready:
            mock_ready.return_value = True
            with patch("shannon_core.services.temporal_infra.start_temporal") as mock_start:
                await ensure_infra()
        mock_start.assert_not_called()

    @pytest.mark.asyncio
    async def test_starts_shannon_container_when_exists(self):
        """When shannon-temporal container exists but stopped, start it instead of docker-compose."""
        ready_count = 0

        async def fake_ready(address="localhost:7233"):
            nonlocal ready_count
            ready_count += 1
            return ready_count > 1

        with (
            patch("shannon_core.services.temporal_infra.is_temporal_ready", side_effect=fake_ready),
            patch("shannon_core.services.temporal_infra._shannon_container_exists", return_value=True),
            patch("shannon_core.services.temporal_infra.subprocess") as mock_sp,
            patch("shannon_core.services.temporal_infra.start_temporal") as mock_start,
        ):
            await ensure_infra()

        # Should have called docker start, NOT start_temporal (docker compose)
        mock_start.assert_not_called()
        mock_sp.run.assert_called_once_with(
            ["docker", "start", "shannon-temporal"],
            check=True, capture_output=True, text=True,
        )

    @pytest.mark.asyncio
    async def test_starts_own_container_as_fallback(self):
        """When no shannon container exists, fall back to own docker-compose."""
        ready_count = 0

        async def fake_ready(address="localhost:7233"):
            nonlocal ready_count
            ready_count += 1
            return ready_count > 1

        with (
            patch("shannon_core.services.temporal_infra.is_temporal_ready", side_effect=fake_ready),
            patch("shannon_core.services.temporal_infra._shannon_container_exists", return_value=False),
            patch("shannon_core.services.temporal_infra.start_temporal") as mock_start,
        ):
            await ensure_infra()

        mock_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_on_timeout(self):
        async def never_ready(address="localhost:7233"):
            return False

        with (
            patch("shannon_core.services.temporal_infra.is_temporal_ready", side_effect=never_ready),
            patch("shannon_core.services.temporal_infra._shannon_container_exists", return_value=False),
            patch("shannon_core.services.temporal_infra.start_temporal"),
            patch("shannon_core.services.temporal_infra._READY_POLL_ATTEMPTS", 3),
            patch("shannon_core.services.temporal_infra._READY_POLL_INTERVAL", 0),
        ):
            with pytest.raises(RuntimeError, match="Timed out waiting for Temporal"):
                await ensure_infra()


class TestGenerateTaskQueue:
    def test_format_is_prefix_hex8(self):
        from shannon_core.services.temporal_infra import generate_task_queue
        result = generate_task_queue("shannon-py-wb")
        assert result.startswith("shannon-py-wb-")
        suffix = result.removeprefix("shannon-py-wb-")
        assert len(suffix) == 8
        int(suffix, 16)  # must be valid hex

    def test_generates_unique_names(self):
        from shannon_core.services.temporal_infra import generate_task_queue
        names = {generate_task_queue("shannon-py-wb") for _ in range(100)}
        assert len(names) == 100


class TestShannonContainerExists:
    def test_returns_true_when_container_found(self):
        from shannon_core.services.temporal_infra import _shannon_container_exists
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            mock_sp.run.return_value = MagicMock(stdout="shannon-temporal")
            assert _shannon_container_exists() is True
            args = mock_sp.run.call_args[0][0]
            assert "--filter" in args
            assert "name=shannon-temporal" in args

    def test_returns_false_when_container_not_found(self):
        from shannon_core.services.temporal_infra import _shannon_container_exists
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            mock_sp.run.return_value = MagicMock(stdout="")
            assert _shannon_container_exists() is False

    def test_returns_false_when_docker_not_installed(self):
        from shannon_core.services.temporal_infra import _shannon_container_exists
        with patch("shannon_core.services.temporal_infra.subprocess") as mock_sp:
            mock_sp.run.side_effect = FileNotFoundError("docker not found")
            assert _shannon_container_exists() is False
