# Workspace Provider Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Web workspace scans require a complete workspace-owned Provider configuration while initializing new workspaces with the requested OpenAI-compatible template and never falling back to global model values.

**Architecture:** Keep the existing `WsConfigStore` as the workspace configuration source of truth. Add a default-template constructor and strict resolution validation there; use the same resolver as both the HTTP preflight and `ScanManager`’s internal submission path. Preserve the old global environment behavior only when a `ScanManager` is constructed without a workspace config store (CLI/legacy compatibility).

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, dataclasses, YAML; existing React/TypeScript workspace settings placeholder.

---

## Files and responsibilities

- Modify `packages/web/src/supernova_web/components/ws_config_store.py` — own the default workspace template, required-field validation, and workspace-only ProviderConfig dict resolution.
- Modify `packages/web/src/supernova_web/api/workspaces.py` — persist the default Provider template when an admin creates a workspace.
- Modify `packages/web/src/supernova_web/api/scan.py` — reject incomplete workspace Provider configuration before calling `scan_manager.start()`.
- Modify `packages/web/frontend/src/routes/WorkspaceDetail/WsSettingsTab.tsx` — show the same concrete defaults and tell the user to fill the API key.
- Modify `packages/web/tests/test_ws_config_store.py` — unit-test defaults, strict validation, and removal of global fallback.
- Modify `packages/web/tests/test_workspace_lifecycle.py` — verify workspace creation writes the template.
- Modify `packages/web/tests/test_api_ws_config.py` — update the empty-workspace GET expectation to the default template.
- Modify `packages/web/tests/test_api_scan.py` — cover HTTP fail-fast and make existing fake-manager success tests use a complete workspace config.
- Modify `packages/web/tests/test_scan_manager_ws_config.py` — make the successful path complete and add a missing-key submission regression test.
- Modify `packages/web/tests/test_ws_config_e2e.py` — replace the old global-fallback assertion with a strict failure assertion and keep a complete end-to-end success case.

## Task 1: Add workspace defaults and strict Provider resolution

**Files:**
- Modify: `packages/web/src/supernova_web/components/ws_config_store.py`
- Test: `packages/web/tests/test_ws_config_store.py`

- [ ] **Step 1: Write failing tests for the required behavior.**

Add tests with the existing `store` fixture:

```python
def test_read_missing_ws_returns_default_provider_template(store, tmp_path):
    (tmp_path / "ws-a").mkdir()
    cfg = store.read("ws-a")
    assert cfg.provider.ai_provider == "openai_compatible"
    assert cfg.provider.base_url == "https://llm-proxy.futuoa.com/v1"
    assert cfg.provider.large_model == "glm-5.2-coder"
    assert cfg.provider.medium_model == "glm-5.2-coder"
    assert cfg.provider.small_model == "glm-5.2-coder"
    assert cfg.provider.api_key is None


def test_resolve_provider_config_does_not_use_global_fallback(store, tmp_path, monkeypatch):
    (tmp_path / "ws-a").mkdir()
    monkeypatch.setenv("SUPERNOVA_OPENAI_API_KEY", "global-key")
    with pytest.raises(ValueError, match="SUPERNOVA_OPENAI_API_KEY"):
        store.resolve_provider_config("ws-a")


def test_resolve_provider_config_requires_all_openai_fields(store, tmp_path):
    (tmp_path / "ws-a").mkdir()
    store.write("ws-a", WsConfig(provider=WsProviderFields(
        ai_provider="openai_compatible",
        base_url="https://llm-proxy.futuoa.com/v1",
        api_key="sk-ws",
        medium_model="glm-5.2-coder",
    )))
    with pytest.raises(ValueError, match="SUPERNOVA_OPENAI_SMALL_MODEL"):
        store.resolve_provider_config("ws-a")


def test_resolve_provider_config_returns_only_workspace_values(store, tmp_path, monkeypatch):
    (tmp_path / "ws-a").mkdir()
    monkeypatch.setenv("SUPERNOVA_MODEL", "global-model")
    store.write("ws-a", WsConfig(provider=WsProviderFields(
        ai_provider="openai_compatible",
        base_url="https://llm-proxy.futuoa.com/v1",
        api_key="sk-ws",
        small_model="glm-5.2-coder",
        medium_model="glm-5.2-coder",
        large_model="glm-5.2-coder",
    )))
    resolved = store.resolve_provider_config("ws-a")
    assert resolved["type"] == "openai_compatible"
    assert resolved["api_key"] == "sk-ws"
    assert resolved["base_url"] == "https://llm-proxy.futuoa.com/v1"
    assert resolved["small_model"] == "glm-5.2-coder"
    assert resolved["medium_model"] == "glm-5.2-coder"
    assert resolved["large_model"] == "glm-5.2-coder"
    assert resolved["model"] is None
    assert "SUPERNOVA_MODEL" not in resolved.values()
```

Update the old tests that expected a global fallback:

- `test_resolve_provider_config_global_default_when_unset` becomes the strict failure test above.
- `test_resolve_provider_config_ws_overrides` supplies all required OpenAI fields before resolving.
- `test_validate_ws_config_none_provider_ok` remains a write-time provider-name validation test; strict completeness belongs to resolution, not saving an editable incomplete form.

- [ ] **Step 2: Run the focused tests and verify they fail for the intended reason.**

Run:

```bash
pytest packages/web/tests/test_ws_config_store.py -q
```

Expected: the new default assertions fail because missing files currently return an empty `WsConfig`; the no-fallback test fails because resolution currently calls `build_provider_config()`; existing tests may also fail until their old fallback expectations are updated.

- [ ] **Step 3: Implement the minimal store behavior.**

In `ws_config_store.py`:

1. Add immutable constants for the requested defaults and a `default_ws_config()` function that returns a fresh `WsConfig`:

```python
DEFAULT_WS_PROVIDER = "openai_compatible"
DEFAULT_WS_BASE_URL = "https://llm-proxy.futuoa.com/v1"
DEFAULT_WS_MODEL = "glm-5.2-coder"


def default_ws_config() -> WsConfig:
    return WsConfig(provider=WsProviderFields(
        ai_provider=DEFAULT_WS_PROVIDER,
        base_url=DEFAULT_WS_BASE_URL,
        small_model=DEFAULT_WS_MODEL,
        medium_model=DEFAULT_WS_MODEL,
        large_model=DEFAULT_WS_MODEL,
    ))
```

2. Make `read()` return `default_ws_config()` only when the workspace has no `config.yaml`; do not merge defaults into an existing partial file.

3. Add strict resolution validation driven by `PROVIDER_SETTINGS`:

```python
def _missing_provider_fields(provider: WsProviderFields) -> list[str]:
    provider_type = provider.ai_provider
    if not provider_type:
        return ["SUPERNOVA_AI_PROVIDER"]
    settings = PROVIDER_SETTINGS.get(provider_type)
    if settings is None:
        raise ValueError(f"unknown ai_provider: {provider_type}")
    missing: list[str] = []
    for required in settings.required:
        if required == "credential":
            if not provider.api_key:
                missing.append(settings.api_key or settings.auth_token or "API credential")
            continue
        value = getattr(provider, required, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            env_name = getattr(settings, required, None)
            if env_name:
                missing.append(env_name)
    return missing
```

Raise `ValueError("workspace provider config incomplete; missing: ...")` when the list is non-empty.

4. Replace `resolve_provider_config()`’s `asdict(build_provider_config())` base with a dict made only from `self.read(ws).provider`: map `ai_provider` to `type`, keep the workspace model/API/base URL/tuning values, and omit the `ai_provider` key. Do not import or call `build_provider_config()` in this workspace-specific method. Keep `validate_ws_config()` for provider-name validation on writes.

- [ ] **Step 4: Run the focused tests and verify they pass.**

Run:

```bash
pytest packages/web/tests/test_ws_config_store.py -q
```

Expected: all store tests pass, including the assertion that a global API key/model cannot satisfy an incomplete workspace.

## Task 2: Persist and display the default template

**Files:**
- Modify: `packages/web/src/supernova_web/api/workspaces.py`
- Modify: `packages/web/tests/test_workspace_lifecycle.py`
- Modify: `packages/web/tests/test_api_ws_config.py`
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/WsSettingsTab.tsx`

- [ ] **Step 1: Add failing lifecycle and API expectations.**

After the existing successful workspace creation assertions, add:

```python
cfg = _app.state.ws_config_store.read("ws1").provider
assert cfg.ai_provider == "openai_compatible"
assert cfg.base_url == "https://llm-proxy.futuoa.com/v1"
assert cfg.small_model == "glm-5.2-coder"
assert cfg.medium_model == "glm-5.2-coder"
assert cfg.large_model == "glm-5.2-coder"
assert cfg.api_key is None
```

Change `test_get_config_empty_ws` to assert the default env text contains the provider, proxy URL, and all three `SUPERNOVA_OPENAI_*_MODEL` lines, and does not contain an API key value.

- [ ] **Step 2: Run the focused tests and verify the new expectations fail.**

Run:

```bash
pytest packages/web/tests/test_workspace_lifecycle.py::test_admin_creates_workspace packages/web/tests/test_api_ws_config.py::test_get_config_empty_ws -q
```

Expected: the workspace creation test fails because `config.yaml` is not written; the config GET test fails because the current empty store renders an empty string.

- [ ] **Step 3: Implement default persistence and UI guidance.**

In `create_workspace()`, after creating `ws_dir` and before returning, call:

```python
request.app.state.ws_config_store.write(ws, default_ws_config())
```

Import `default_ws_config` from `ws_config_store`. The existing `read()` default makes the GET endpoint work for legacy workspaces without a config file and does not overwrite partial user configurations.

Update `PLACEHOLDER` in `WsSettingsTab.tsx` to:

```ts
const PLACEHOLDER = [
  "SUPERNOVA_AI_PROVIDER=openai_compatible",
  "SUPERNOVA_OPENAI_API_KEY=填入你的 API key",
  "SUPERNOVA_OPENAI_BASE_URL=https://llm-proxy.futuoa.com/v1",
  "SUPERNOVA_OPENAI_LARGE_MODEL=glm-5.2-coder",
  "SUPERNOVA_OPENAI_MEDIUM_MODEL=glm-5.2-coder",
  "SUPERNOVA_OPENAI_SMALL_MODEL=glm-5.2-coder",
].join("\\n");
```

The placeholder is guidance only; the API key must not be persisted as a fake default.

- [ ] **Step 4: Run the focused tests and verify they pass.**

Run:

```bash
pytest packages/web/tests/test_workspace_lifecycle.py::test_admin_creates_workspace packages/web/tests/test_api_ws_config.py::test_get_config_empty_ws -q
```

Expected: both tests pass and the workspace contains the template with a `None` API key.

## Task 3: Fail fast before scan submission

**Files:**
- Modify: `packages/web/src/supernova_web/api/scan.py`
- Modify: `packages/web/tests/test_api_scan.py`
- Modify: `packages/web/src/supernova_web/components/scan_manager.py` only if the existing internal resolver test exposes a missing validation path
- Modify: `packages/web/tests/test_scan_manager_ws_config.py`

- [ ] **Step 1: Write failing HTTP and internal-path tests.**

Add an API test using a fake scan manager whose `start()` records calls. Create an existing workspace without a config/key, post the normal scan body, and assert:

```python
assert response.status_code == 422
assert "SUPERNOVA_OPENAI_API_KEY" in response.text
assert fake.started == []
```

Update the success fixture/workspace in existing `test_api_scan.py` tests to write a complete template plus `api_key="test-key"`, because API preflight must now pass before the fake manager is called.

Add/adjust the manager test so `_submit_whitebox()` with the default template but no API key raises `ValueError` and the fake Temporal client has no captured input.

- [ ] **Step 2: Run the focused tests and verify the new tests fail.**

Run:

```bash
pytest packages/web/tests/test_api_scan.py packages/web/tests/test_scan_manager_ws_config.py -q
```

Expected: the HTTP request currently calls the fake manager despite missing workspace credentials, and the internal path may only fail later or use global values.

- [ ] **Step 3: Implement the HTTP preflight and preserve internal defense.**

At the beginning of `create_scan()`, after workspace existence and membership checks and before `sm.start(req)`, call:

```python
request.app.state.ws_config_store.resolve_provider_config(ws)
```

Let the existing `except ValueError` convert the incomplete configuration into HTTP 422. This runs only for the Web app, where `ws_config_store` is wired. Keep `ScanManager._resolve_provider_config()` delegating to the strict store resolver whenever `self._ws_config_store is not None`; its `None` branch continues to call global `build_provider_config()` for CLI/legacy compatibility.

Ensure the strict resolver runs before `_submit_whitebox()` or `_submit_blackbox()` can connect to Temporal, so a missing API key/model never creates a submitted workflow.

- [ ] **Step 4: Run the focused tests and verify they pass.**

Run:

```bash
pytest packages/web/tests/test_api_scan.py packages/web/tests/test_scan_manager_ws_config.py -q
```

Expected: incomplete workspaces return 422 without invoking the manager/Temporal path; complete workspaces still submit with workspace-owned values.

## Task 4: Update end-to-end and regression coverage

**Files:**
- Modify: `packages/web/tests/test_ws_config_e2e.py`
- Modify: `packages/web/tests/test_scan_manager_ws_config.py`
- Modify: `packages/web/tests/test_ws_config_store.py` if any exact error text or field mapping needs assertion updates

- [ ] **Step 1: Replace fallback assertions with strict behavior.**

Change `test_unconfigured_ws_falls_back_to_global` into a test that creates `ws-b` with no API key, sets a complete global environment configuration, and asserts `_submit_whitebox()` raises `ValueError` mentioning `SUPERNOVA_OPENAI_API_KEY`; assert the fake client captured no input.

Update successful e2e/store/manager fixtures to include:

```python
WsProviderFields(
    ai_provider="openai_compatible",
    api_key="sk-e2e",
    base_url="https://llm-proxy.futuoa.com/v1",
    small_model="glm-5.2-coder",
    medium_model="glm-5.2-coder",
    large_model="glm-5.2-coder",
)
```

Assert the resulting `PipelineInput.provider_config` has these exact values and does not contain a global `model` value.

- [ ] **Step 2: Run all affected Python tests.**

Run:

```bash
pytest packages/web/tests/test_ws_config_store.py \
       packages/web/tests/test_api_ws_config.py \
       packages/web/tests/test_workspace_lifecycle.py \
       packages/web/tests/test_api_scan.py \
       packages/web/tests/test_scan_manager_ws_config.py \
       packages/web/tests/test_ws_config_e2e.py -q
```

Expected: all affected tests pass with no fallback assertion remaining.

- [ ] **Step 3: Run the frontend settings tests/typecheck.**

From `packages/web/frontend` run the repository’s existing test command and TypeScript check (inspect `package.json` scripts first):

```bash
npm test -- --run src/routes/WorkspaceDetail/WsSettingsTab.test.tsx
npm run typecheck
```

Expected: the settings component still renders, saves the env text unchanged, and typechecks after the placeholder update.

## Task 5: Final verification

- [ ] **Step 1: Inspect the final diff and ensure unrelated user changes are untouched.**

Run:

```bash
git status --short
git diff -- packages/web/src/supernova_web/components/ws_config_store.py \
  packages/web/src/supernova_web/api/workspaces.py \
  packages/web/src/supernova_web/api/scan.py \
  packages/web/frontend/src/routes/WorkspaceDetail/WsSettingsTab.tsx \
  packages/web/tests
```

Confirm only the requested files and the already committed design document are part of this task.

- [ ] **Step 2: Run the complete Web test suite.**

Run:

```bash
pytest packages/web/tests -q
```

Expected: exit code 0 with zero failures. If a test assumes a global workspace config, update that test to explicitly provide a complete workspace configuration rather than weakening the strict resolver.

- [ ] **Step 3: Run final diff checks.**

Run:

```bash
git diff --check
```

Expected: no whitespace errors. Only after this verification report the exact tests and exit codes.
