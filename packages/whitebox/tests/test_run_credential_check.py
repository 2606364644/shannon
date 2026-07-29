"""run_credential_check 预检门：anthropic_api 走 auth_token（glm-anthropic）也须预检，
不再因无 api_key 静默跳过（2026-07-30 web 引擎错配根因之一）。

回归锚点：web 不 load_env → scan_manager 回落 anthropic_api + 凭据空 → 经 worker 跑
CLI 引擎 → "Not logged in"。预检门本应 fail-fast，却因 `config.api_key or
config.type != "anthropic_api"` 条件在「anthropic_api + 无 api_key」时整段跳过。
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from supernova_whitebox.audit.session_registry import (
    clear_audit_session,
    set_audit_session,
)
from supernova_whitebox.pipeline import activities


class _StubSession:
    @asynccontextmanager
    async def track_step(self, phase, name, intent=None):
        yield


def _input(provider_config):
    return SimpleNamespace(api_key=None, provider_config=provider_config)


@pytest.mark.asyncio
async def test_anthropic_api_with_auth_token_triggers_preflight():
    """glm-anthropic（auth_token、无 api_key）应触发预检，而非被跳过。"""
    set_audit_session(_StubSession())
    try:
        with patch.object(activities, "validate_credentials", new=AsyncMock()) as mock_vc:
            await activities.run_credential_check(
                _input({
                    "type": "anthropic_api",
                    "api_key": None,
                    "auth_token": "glm-bearer",
                    "base_url": "https://open.bigmodel.cn/api/anthropic",
                })
            )
        mock_vc.assert_awaited_once()
        assert mock_vc.call_args.kwargs.get("auth_token") == "glm-bearer"
    finally:
        clear_audit_session()


@pytest.mark.asyncio
async def test_anthropic_api_no_credential_still_preflights_not_skip():
    """anthropic_api 凭据全空（web 错配）不应静默跳过预检。

    改前：`config.api_key or config.type != "anthropic_api"` → False → 跳过 → 放行后满屏
    "Not logged in"。改后：交给 validate_credentials fail-fast。
    """
    set_audit_session(_StubSession())
    try:
        with patch.object(activities, "validate_credentials", new=AsyncMock()) as mock_vc:
            await activities.run_credential_check(
                _input({"type": "anthropic_api", "api_key": None, "auth_token": None})
            )
        mock_vc.assert_awaited_once()  # 不再跳过，交由 validate_credentials 决定
    finally:
        clear_audit_session()
