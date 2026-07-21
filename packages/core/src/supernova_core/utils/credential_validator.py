"""Validate AI provider credentials before starting an expensive scan."""

from __future__ import annotations

import httpx

from supernova_core.models.errors import ErrorCode, PentestError

_DEFAULT_TIMEOUT = 15

# Minimal Anthropic messages request to test API key validity
_ANTHROPIC_TEST_BODY = {
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 1,
    "messages": [{"role": "user", "content": "hi"}],
}


async def _validate_anthropic(api_key: str) -> None:
    """POST a minimal request to the Anthropic messages API."""
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=_ANTHROPIC_TEST_BODY,
            )
        if resp.status_code in (401, 403):
            raise PentestError(
                f"Anthropic API key rejected (HTTP {resp.status_code})",
                category="preflight",
                retryable=False,
                error_code=ErrorCode.AUTH_FAILED,
            )
    except httpx.ConnectError as exc:
        raise PentestError(
            f"Cannot reach Anthropic API: {exc}",
            category="preflight",
            retryable=True,
            error_code=ErrorCode.AUTH_FAILED,
        ) from exc


async def _validate_bedrock() -> None:
    """Call STS GetCallerIdentity to verify AWS credentials."""
    try:
        import boto3

        client = boto3.client("sts")
        client.get_caller_identity()
    except Exception as exc:
        raise PentestError(
            f"AWS/Bedrock credential validation failed: {exc}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.AUTH_FAILED,
        ) from exc


async def _validate_vertex() -> None:
    """Verify Google Cloud project access for Vertex AI."""
    try:
        from google.cloud import aiplatform

        aiplatform.init()
    except Exception as exc:
        raise PentestError(
            f"Vertex AI credential validation failed: {exc}",
            category="preflight",
            retryable=False,
            error_code=ErrorCode.AUTH_FAILED,
        ) from exc


async def _validate_openai_compatible(
    api_key: str, base_url: str, model: str | None = None
) -> None:
    """POST a minimal chat request to verify OpenAI-compatible API credentials.

    Uses ``{base_url}/chat/completions`` — supported by every OpenAI-compatible
    service — rather than ``{base_url}/v1/models``. Many dedicated channels
    (e.g. Zhipu's ``coding/paas/v4``) do not expose a models-list endpoint at
    all, and ``/v1/models`` also collides with base_urls that already include a
    version path: it resolves to ``…/paas/v4/v1/models``, which the gateway
    rejects as HTTP 404 once an otherwise-valid key passes its auth check.
    """
    base_url = base_url.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model or "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
            )
        if resp.status_code in (401, 403):
            raise PentestError(
                f"OpenAI-compatible API key rejected (HTTP {resp.status_code})",
                category="preflight",
                retryable=False,
                error_code=ErrorCode.AUTH_FAILED,
            )
        # Any other status (200 success, 400 unknown model, …) means the request
        # reached the endpoint and cleared authentication — credentials are valid.
    except httpx.ConnectError as exc:
        raise PentestError(
            f"Cannot reach OpenAI-compatible API at {base_url}: {exc}",
            category="preflight",
            retryable=True,
            error_code=ErrorCode.AUTH_FAILED,
        ) from exc


async def validate_credentials(
    provider: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    auth_token: str | None = None,
    model: str | None = None,
) -> None:
    """Dispatch credential validation to the appropriate provider.

    Unknown providers are silently skipped (graceful degradation).
    """
    if provider == "anthropic_api":
        if not api_key:
            raise PentestError(
                "Anthropic API key is required but not provided",
                category="preflight",
                retryable=False,
                error_code=ErrorCode.AUTH_FAILED,
            )
        await _validate_anthropic(api_key)
    elif provider == "bedrock":
        await _validate_bedrock()
    elif provider == "vertex":
        await _validate_vertex()
    elif provider == "openai_compatible":
        if not api_key:
            raise PentestError(
                "OpenAI-compatible provider requires api_key (SUPERNOVA_API_KEY or ANTHROPIC_API_KEY)",
                category="preflight",
                retryable=False,
                error_code=ErrorCode.AUTH_FAILED,
            )
        if not base_url:
            raise PentestError(
                "OpenAI-compatible provider requires base_url (SUPERNOVA_BASE_URL)",
                category="preflight",
                retryable=False,
                error_code=ErrorCode.AUTH_FAILED,
            )
        await _validate_openai_compatible(api_key, base_url, model)
    elif provider == "litellm_router":
        if not base_url or not auth_token:
            raise PentestError(
                "LiteLLM router requires both base_url and auth_token",
                category="preflight",
                retryable=False,
                error_code=ErrorCode.AUTH_FAILED,
            )
        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
                resp = await client.get(
                    f"{base_url}/health",
                    headers={"Authorization": f"Bearer {auth_token}"},
                )
                if resp.status_code in (401, 403):
                    raise PentestError(
                        f"LiteLLM router auth failed (HTTP {resp.status_code})",
                        category="preflight",
                        retryable=False,
                        error_code=ErrorCode.AUTH_FAILED,
                    )
        except httpx.ConnectError as exc:
            raise PentestError(
                f"Cannot reach LiteLLM router at {base_url}: {exc}",
                category="preflight",
                retryable=True,
                error_code=ErrorCode.AUTH_FAILED,
            ) from exc
    # Unknown providers: skip silently
