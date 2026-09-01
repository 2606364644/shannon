"""Asynchronous, workspace-isolated cross-repo topology analysis manager."""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from supernova_core.agents.runner import ClaudeRunResult, UsageSink, run_claude_prompt
from supernova_core.agents.tool_audit_logger import ToolAuditLogger
from supernova_core.prompts.manager import PromptManager
from supernova_core.topology.discovery import (
    build_topology_fingerprint,
    collect_navigation_manifest,
    normalize_topology_result,
)
from supernova_core.topology.schema import TOPOLOGY_DISCOVERY_SCHEMA

from .topology_analysis_store import TopologyAnalysisStore


class TopologyValidationError(ValueError):
    def __init__(self, message: str, *, repos: list[str] | None = None):
        self.repos = repos or []
        super().__init__(message)


class TooManyTopologyAnalyses(RuntimeError):
    pass


class AnalysisNotFound(KeyError):
    pass


class TopologyProviderConfigError(ValueError):
    pass
    pass


class _NdjsonToolAuditLogger(ToolAuditLogger):
    def __init__(self, path: Path):
        self._path = path
        self._lock = asyncio.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch(exist_ok=True)

    async def log_tool_start(self, tool_name: str, parameters: Any) -> None:
        await self._write({"type": "tool_start", "tool": tool_name,
                           "parameters": str(parameters)[:2000]})

    async def log_tool_end(self, result: Any) -> None:
        await self._write({"type": "tool_end", "result": str(result)[:2000]})

    async def log_error(self, error: str, *, turn_count: int = 0, duration_ms: int = 0) -> None:
        await self._write({"type": "error", "error": error, "turn_count": turn_count,
                           "duration_ms": duration_ms})

    async def log_assistant_turn(self, turn: int, content: str) -> None:
        await self._write({"type": "assistant_turn", "turn": turn, "content": content[:2000]})

    async def _write(self, event: dict[str, Any]) -> None:
        payload = json_line(event)
        async with self._lock:
            await asyncio.to_thread(self._append_sync, payload)

    def _append_sync(self, payload: str) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()


def json_line(event: dict[str, Any]) -> str:
    import json
    return json.dumps({"ts": _now_iso(), **event}, ensure_ascii=False, separators=(",", ":")) + "\n"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class TopologyAnalysisManager:
    def __init__(
        self,
        workspaces_dir: Path,
        *,
        repo_manager: Any | None,
        ws_config_store: Any | None = None,
        runner: Callable[..., Awaitable[ClaudeRunResult]] | None = None,
    ) -> None:
        self._root = Path(workspaces_dir).resolve()
        self.store = TopologyAnalysisStore(self._root)
        self._repo_manager = repo_manager
        self._ws_config_store = ws_config_store
        self._runner = runner or run_claude_prompt
        self._tasks: dict[str, asyncio.Task] = {}
        self._task_ws: dict[str, str] = {}
        self._usage_sinks: dict[str, UsageSink] = {}
        self._provider_configs: dict[str, dict] = {}
        self._active_count = 0
        self._active_ids: set[str] = set()
        self._start_lock = asyncio.Lock()
        self.store.recover_interrupted()
        self.store.cleanup(max_records=self.max_stored_analyses)

    @property
    def max_repos(self) -> int:
        return max(2, int(os.getenv("SUPERNOVA_TOPOLOGY_MAX_REPOS", "8")))

    @property
    def max_concurrent(self) -> int:
        return max(1, int(os.getenv("SUPERNOVA_TOPOLOGY_MAX_CONCURRENT", "1")))

    @property
    def timeout_seconds(self) -> float:
        return max(0.01, float(os.getenv("SUPERNOVA_TOPOLOGY_TIMEOUT_SECONDS", "900")))

    @property
    def max_turns(self) -> int:
        return max(1, int(os.getenv("SUPERNOVA_TOPOLOGY_MAX_TURNS", "30")))

    @property
    def cache_ttl_seconds(self) -> int:
        return max(0, int(os.getenv("SUPERNOVA_TOPOLOGY_CACHE_TTL_SECONDS", "86400")))

    @property
    def max_stored_analyses(self) -> int:
        return max(1, int(os.getenv("SUPERNOVA_TOPOLOGY_MAX_STORED_ANALYSES", "100")))

    async def start(self, ws: str, repos: list[str], *, refresh: bool = False) -> str:
        # Serialize validation/cache/task creation to prevent duplicate cache misses.
        async with self._start_lock:
            return await self._start(ws, repos, refresh=refresh)

    async def _start(self, ws: str, repos: list[str], *, refresh: bool = False) -> str:
        names = sorted(set(repos))
        if len(names) < 2:
            raise TopologyValidationError("at least two repositories are required", repos=names)
        if len(names) > self.max_repos:
            raise TopologyValidationError(
                f"at most {self.max_repos} repositories are allowed", repos=names)
        if self._active_count >= self.max_concurrent:
            raise TooManyTopologyAnalyses(self.max_concurrent)

        paths = {name: await self._resolve_repo_path(ws, name) for name in names}
        provider_config = self._provider_config(ws)
        manifest = await asyncio.to_thread(collect_navigation_manifest, paths)
        fingerprint = await asyncio.to_thread(build_topology_fingerprint, paths)
        if not refresh:
            cached = self.store.find_cached(ws, fingerprint.value, ttl_seconds=self.cache_ttl_seconds)
            if cached is not None:
                cached["cache_hit"] = True
                cached["updated_at"] = _now_iso()
                self.store.write(cached)
                return cached["analysis_id"]

        analysis_id = f"topology-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        state = {
            "analysis_id": analysis_id,
            "workspace": ws,
            "status": "queued",
            "repos": names,
            "fingerprint": fingerprint.value,
            "fingerprint_detail": fingerprint.model_dump(),
            "manifest": manifest.model_dump(),
            "repo_paths": {name: str(path) for name, path in paths.items()},
            "created_at": now,
            "updated_at": now,
            "progress": 5,
            "cache_hit": False,
            "result": None,
            "raw_output": None,
            "usage": None,
            "error": None,
        }
        self.store.create(ws, state)
        self._provider_configs[analysis_id] = provider_config
        self._task_ws[analysis_id] = ws
        self._usage_sinks[analysis_id] = UsageSink()
        self._active_count += 1
        self._active_ids.add(analysis_id)
        task = asyncio.create_task(self._run(analysis_id), name=analysis_id)
        self._tasks[analysis_id] = task
        # A task cancelled before its coroutine starts never reaches _run's finally.
        task.add_done_callback(lambda _task, analysis_id=analysis_id: self._cleanup_analysis(analysis_id, _task))
        return analysis_id

    async def wait(self, analysis_id: str) -> None:
        task = self._tasks.get(analysis_id)
        if task is not None:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # A cancelled topology task is a terminal state, not a caller failure.
                if task is not asyncio.current_task() and task.cancelled():
                    return
                raise

    def get(self, ws: str, analysis_id: str) -> dict[str, Any]:
        state = self.store.get(ws, analysis_id)
        if state is None:
            raise AnalysisNotFound(analysis_id)
        return state

    async def cancel(self, ws: str, analysis_id: str) -> dict[str, Any]:
        state = self.get(ws, analysis_id)
        if state.get("status") in {"queued", "running"}:
            task = self._tasks.get(analysis_id)
            if task is not None and not task.done():
                task.cancel()
            state.update({
                "status": "cancelled", "updated_at": _now_iso(), "progress": 100,
                "error": {"code": "cancelled", "message": "analysis cancelled by user", "retryable": True},
                "usage": _usage_from_sink(self._usage_sinks.get(analysis_id)) or state.get("usage"),
            })
            self.store.write(state)
        return self.get(ws, analysis_id)

    async def _resolve_repo_path(self, ws: str, name: str) -> Path:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", name or ""):
            raise TopologyValidationError(f"invalid repository name: {name!r}", repos=[name])
        if self._repo_manager is None:
            candidate = (self._root / ws / "repos" / name).resolve()
            if not candidate.is_relative_to(self._root.resolve()) or not candidate.is_dir():
                raise TopologyValidationError(f"repository not found: {name}", repos=[name])
            return candidate
        view = self._repo_manager.get_repo(ws, name)
        if view is None:
            raise TopologyValidationError(f"repository not found: {name}", repos=[name])
        if view.get("state") not in (None, "ready"):
            raise TopologyValidationError(f"repository is not ready: {name}", repos=[name])
        if view.get("linked"):
            from .repo_manager import resolve_linked_repo_path
            linked = resolve_linked_repo_path(self._root, ws, name)
            if not linked:
                raise TopologyValidationError(f"linked repository path missing: {name}", repos=[name])
            return Path(linked).resolve()
        try:
            candidate = self._repo_manager._repo_dir(ws, name)
        except ValueError as exc:
            raise TopologyValidationError(str(exc), repos=[name]) from exc
        if not candidate.is_relative_to(self._root.resolve()) or not candidate.is_dir():
            raise TopologyValidationError(f"repository path is not available: {name}", repos=[name])
        return candidate

    async def _run(self, analysis_id: str) -> None:
        ws = self._task_ws[analysis_id]
        try:
            state = self.get(ws, analysis_id)
            state.update({"status": "running", "progress": 20, "updated_at": _now_iso()})
            self.store.write(state)
            try:
                result = await asyncio.wait_for(self._run_agent(analysis_id), timeout=self.timeout_seconds)
            except asyncio.TimeoutError:
                await self._fail(ws, analysis_id, {
                    "code": "timeout", "message": f"analysis exceeded {self.timeout_seconds:g}s",
                    "retryable": True,
                })
                return
            except asyncio.CancelledError:
                # cancel() normally writes terminal state before task cancellation. The provider
                # may still flush UsageSink after that write, so preserve late partial accounting.
                state = self.get(ws, analysis_id)
                sink_usage = _usage_from_sink(self._usage_sinks.get(analysis_id))
                if state.get("status") in {"queued", "running"}:
                    await self._fail(ws, analysis_id, {
                        "code": "cancelled", "message": "analysis cancelled", "retryable": True,
                    })
                elif sink_usage is not None:
                    state["usage"] = sink_usage
                    self.store.write(state)
                raise
            except Exception as exc:  # provider infrastructure failures must not auto-retry
                await self._fail(ws, analysis_id, {
                    "code": "provider_failed", "message": str(exc), "retryable": True,
                }, result=None)
                return

            usage = _usage(result)
            if not result.success:
                await self._fail(ws, analysis_id, {
                    "code": "provider_failed", "message": result.error or "provider returned failure",
                    "retryable": bool(result.retryable),
                }, result=result)
                return
            payload = result.structured_output
            if not isinstance(payload, dict):
                await self._fail(ws, analysis_id, {
                    "code": "malformed_output", "message": "agent did not return a JSON object",
                    "retryable": True,
                }, result=result)
                return
            normalized = normalize_topology_result(payload, self._repo_paths_from_state(state))
            if any(item.reason == "malformed_output" for item in normalized.invalid):
                await self._fail(ws, analysis_id, {
                    "code": "malformed_output", "message": "agent output failed schema validation",
                    "retryable": True,
                }, result=result)
                return
            state = self.get(ws, analysis_id)
            state.update({
                "status": "completed", "progress": 100,
                "completed_at": _now_iso(), "updated_at": _now_iso(),
                "result": normalized.model_dump(by_alias=True, exclude_none=True),
                "raw_output": payload, "usage": usage, "error": None,
            })
            self.store.write(state)
            self.store.cleanup(max_records=self.max_stored_analyses)
        finally:
            self._cleanup_analysis(analysis_id)

    def _cleanup_analysis(self, analysis_id: str, task: asyncio.Task | None = None) -> None:
        """Idempotently release registry/concurrency state for every terminal path."""
        current_task = self._tasks.get(analysis_id)
        if task is not None and current_task is not None and current_task is not task:
            return
        if analysis_id in self._active_ids:
            self._active_ids.discard(analysis_id)
            self._active_count = max(0, self._active_count - 1)
        self._tasks.pop(analysis_id, None)
        self._task_ws.pop(analysis_id, None)
        self._usage_sinks.pop(analysis_id, None)
        self._provider_configs.pop(analysis_id, None)

    async def _run_agent(self, analysis_id: str) -> ClaudeRunResult:
        ws = self._task_ws[analysis_id]
        state = self.get(ws, analysis_id)
        manifest = state["manifest"]
        names = state["repos"]
        paths = self._repo_paths_from_state(state)
        repositories = {name: str(path) for name, path in paths.items()}
        prompt = PromptManager(Path(__file__).resolve().parents[5] / "prompts").load_sync(
            "cross-repo-topology-discovery",
            {
                "repositories_json": _json(repositories),
                "navigation_manifest_json": _json(manifest),
            },
        )
        return await self._runner(
            prompt=prompt,
            model_tier="large",
            structured_output_schema=TOPOLOGY_DISCOVERY_SCHEMA,
            provider_config=self._provider_configs.get(analysis_id) or self._provider_config(ws),
            repo_path=str(self.store.path(ws, analysis_id)),
            tool_audit_logger=_NdjsonToolAuditLogger(
                self.store.path(ws, analysis_id) / "tool-audit.ndjson"),
            max_turns=self.max_turns,
            usage_sink=self._usage_sinks.get(analysis_id),
            allowed_roots=[str(path) for path in paths.values()],
            tool_policy="readonly-code",
        )

    def _repo_paths_from_state(self, state: dict[str, Any]) -> dict[str, Path]:
        paths = state.get("repo_paths") or {}
        return {name: Path(paths.get(name) or self._root / state["workspace"] / "repos" / name).resolve()
                for name in state["repos"]}

    def api_view(self, ws: str, analysis_id: str) -> dict[str, Any]:
        """Minimal frontend response; persisted paths/manifests/raw output stay server-side."""
        state = self.get(ws, analysis_id)
        if state is None:
            raise AnalysisNotFound(analysis_id)
        result = state.get("result")
        if isinstance(result, dict):
            result = {**result, "raw": None}
            result["invalid"] = [
                {**item, "raw": {}} if isinstance(item, dict) else item
                for item in result.get("invalid", [])
            ]
        return {
            "analysis_id": state.get("analysis_id"), "workspace": state.get("workspace"),
            "status": state.get("status"), "repos": state.get("repos"),
            "progress": state.get("progress"), "cache_hit": state.get("cache_hit"),
            "fingerprint": state.get("fingerprint"), "result": result,
            "usage": state.get("usage"), "error": state.get("error"),
            "created_at": state.get("created_at"), "updated_at": state.get("updated_at"),
            "completed_at": state.get("completed_at"),
        }

    def _provider_config(self, ws: str) -> dict:
        if self._ws_config_store is not None:
            from .ws_config_store import ProviderConfigIncomplete, validate_ws_config
            try:
                validate_ws_config(self._ws_config_store.read(ws))
            except ProviderConfigIncomplete:
                raise
            except ValueError as exc:
                raise TopologyProviderConfigError(str(exc)) from exc
            provider_config = self._ws_config_store.resolve_provider_config(ws)
            pricing_override = self._root / ws / "pricing.override.json"
            if pricing_override.is_file():
                provider_config = {**provider_config, "pricing_override": str(pricing_override)}
            return provider_config
        from supernova_core.agents.providers import build_provider_config
        return asdict(build_provider_config())

    async def _fail(
        self, ws: str, analysis_id: str, error: dict[str, Any],
        *, result: ClaudeRunResult | None = None,
    ) -> None:
        state = self.get(ws, analysis_id)
        state.update({
            "status": "failed", "progress": 100, "updated_at": _now_iso(), "error": error,
            "usage": (
                _usage(result) if result is not None
                else _usage_from_sink(self._usage_sinks.get(analysis_id)) or state.get("usage")
            ),
            "raw_output": getattr(result, "text", None) if result is not None else state.get("raw_output"),
        })
        self.store.write(state)


def _usage(result: ClaudeRunResult) -> dict[str, Any]:
    tokens = result.tokens
    return {
        "input_tokens": tokens.input_tokens,
        "output_tokens": tokens.output_tokens,
        "cache_read_tokens": tokens.cache_read_input_tokens,
        "cache_creation_tokens": tokens.cache_creation_input_tokens,
        "cost_usd": result.cost,
        "cost_currency": result.cost_currency,
        "model": result.model,
        "turns": result.turns,
    }


def _usage_from_sink(sink: UsageSink | None) -> dict[str, Any] | None:
    if sink is None:
        return None
    if not any((sink.input_tokens, sink.output_tokens, sink.cache_read_tokens,
                sink.cache_creation_tokens, sink.cost_usd)):
        return None
    return {
        "input_tokens": sink.input_tokens,
        "output_tokens": sink.output_tokens,
        "cache_read_tokens": sink.cache_read_tokens,
        "cache_creation_tokens": sink.cache_creation_tokens,
        "cost_usd": sink.cost_usd,
        "cost_currency": sink.cost_currency or "USD",
        "model": sink.model,
        "turns": 0,
    }


def _json(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


__all__ = [
    "AnalysisNotFound", "TopologyAnalysisManager", "TopologyAnalysisStore",
    "TooManyTopologyAnalyses", "TopologyValidationError",
]
