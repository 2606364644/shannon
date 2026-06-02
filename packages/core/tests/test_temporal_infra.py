from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.services.temporal_infra import (
    get_compose_file,
    is_temporal_ready,
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
