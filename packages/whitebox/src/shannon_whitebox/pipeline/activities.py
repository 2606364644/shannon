import json
import time
from datetime import timedelta
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError as ApplicationFailure

from shannon_core.models.agents import AgentName, AGENTS, ALL_VULN_CLASSES, VulnType
from shannon_core.models.errors import ErrorCode, PentestError, classify_error_for_temporal
from shannon_core.models.metrics import AgentMetrics
from shannon_core.utils.atomic_write import atomic_write_json
from shannon_core.utils.paths import resolve_deliverables_path
from shannon_core.utils.credential_validator import validate_credentials
from shannon_core.logging import create_activity_logger
from shannon_core.agents.executor import AgentExecutor
from shannon_core.agents.runner import run_claude_prompt
from shannon_core.prompts.manager import PromptManager
from shannon_core.session import SessionManager
from shannon_whitebox.audit.session import AuditSession

from .shared import ActivityInput
from .step_intents import intent_for

def _get_paths(input: ActivityInput) -> tuple[Path, Path, Path]:
    deliverables = resolve_deliverables_path(
        repo_path=input.repo_path,
        deliverables_subdir=input.deliverables_subdir,
        workspace_name=input.workspace_name,
    )
    repo = Path(input.repo_path)
    workspaces = repo.parent / "workspaces"
    return repo, deliverables, workspaces


def _to_endpoint(d: dict):
    """Reconstruct framework analyzer endpoint dataclasses from JSON."""
    from shannon_core.services.framework_analyzer import InferredEndpoint

    return InferredEndpoint(
        method=d["method"],
        path=d["path"],
        source=d["source"],
        model=d.get("model"),
        middleware=tuple(d.get("middleware", [])),
        vulnerability_indicators=tuple(d.get("vulnerability_indicators", [])),
    )


@activity.defn
async def run_preflight(input: ActivityInput) -> None:
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        async with get_audit_session().track_step("setup", "preflight", intent=intent_for("preflight")):
            # Config parsing validation
            if input.config_path:
                from shannon_core.config.parser import parse_config
                try:
                    parse_config(input.config_path)
                except PentestError:
                    raise
                except Exception as exc:
                    raise PentestError(
                        f"Config parsing failed: {exc}",
                        category="config",
                        error_code=ErrorCode.CONFIG_PARSE_ERROR,
                    ) from exc

            repo, _, _ = _get_paths(input)
            if not repo.exists():
                raise PentestError(
                    f"Repository not found: {input.repo_path}",
                    "config",
                    error_code=ErrorCode.REPO_NOT_FOUND,
                )
            if not (repo / ".git").exists():
                raise PentestError(
                    f"Not a git repository: {input.repo_path}",
                    "config",
                    error_code=ErrorCode.REPO_NOT_FOUND,
                )
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e

@activity.defn
async def run_agent(input: ActivityInput) -> dict:
    from shannon_whitebox.audit.session_registry import get_audit_session
    from shannon_whitebox.audit.session_tool_audit_logger import SessionToolAuditLogger
    from shannon_core.models.audit import AgentEndResult

    agent_name = AgentName(input.agent_name or input.workspace_name)
    attempt = activity.info().attempt
    session = get_audit_session()
    tool_audit_logger = SessionToolAuditLogger(session, agent_name.value, attempt)
    agent_start = time.monotonic()
    try:
        repo, deliverables, _ = _get_paths(input)
        prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
        prompt_manager = PromptManager(prompts_dir)
        executor = AgentExecutor(prompt_manager)

        prompt_variables = None
        if agent_name == AgentName.RECON:
            prompt_variables = {}
            framework_analysis_path = deliverables / "framework_analysis.json"
            if framework_analysis_path.exists():
                from shannon_core.services.framework_endpoint_renderer import render_framework_endpoints

                data = json.loads(framework_analysis_path.read_text())
                endpoints = [
                    _to_endpoint(endpoint)
                    for endpoint in data.get("inferred_endpoints", [])
                    if isinstance(endpoint, dict)
                ]
                prompt_variables["framework_endpoints_summary"] = render_framework_endpoints(endpoints)

            from shannon_core.code_index.recon_gitnexus_track import build_recon_gitnexus_track

            prompt_variables["recon_gitnexus_track"] = build_recon_gitnexus_track(str(deliverables))

        if agent_name == AgentName.PRE_RECON:
            from shannon_core.code_index.pre_recon_gitnexus_track import build_pre_recon_gitnexus_track

            prompt_variables = prompt_variables or {}
            prompt_variables["pre_recon_gitnexus_track"] = build_pre_recon_gitnexus_track(
                repo,
                deliverables,
            )

        await session.start_agent(agent_name.value, f"agent={agent_name.value}", attempt=attempt)
        await tool_audit_logger.initialize()
        metrics = await executor.execute(
            agent_name=agent_name,
            repo_path=str(repo),
            web_url=input.web_url,
            deliverables_path=str(deliverables),
            config_path=input.config_path,
            api_key=input.api_key,
            pipeline_testing=input.pipeline_testing_mode,
            prompt_override=input.prompt_override,
            prompt_variables=prompt_variables,
            tool_audit_logger=tool_audit_logger,
        )
        await tool_audit_logger.close(success=True, duration_ms=metrics.duration_ms)
        await session.end_agent(agent_name.value, AgentEndResult(
            success=True,
            duration_ms=metrics.duration_ms,
            cost_usd=metrics.cost_usd or 0.0,
            attempt_number=attempt,
            model=metrics.model,
        ))
        return metrics.model_dump()
    except PentestError as e:
        await tool_audit_logger.close(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000))
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value)
        error_type, retryable = classify_error_for_temporal(e)
        # log_error surfaces to the live display; ApplicationFailure surfaces to
        # Temporal for retry decisions — both are intended, not double-logging.
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        await tool_audit_logger.close(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000))
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(e, context=agent_name.value)
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e

@activity.defn
async def run_vuln_agent(input: ActivityInput) -> dict:
    return await run_agent(input)


@activity.defn
async def log_phase_start_activity(input: ActivityInput, steps: list[str] | None = None,
                                   intents: list[str] | None = None) -> None:
    from shannon_whitebox.audit.session_registry import get_audit_session
    phase = input.phase or input.workspace_name or "unknown"
    await get_audit_session().log_phase_start(
        phase, steps=tuple(steps or ()), step_intents=tuple(intents or ()))


@activity.defn
async def log_phase_complete_activity(input: ActivityInput) -> None:
    """Emit a phase-complete event.

    Scheduled by ``WhiteboxScanWorkflow.run()`` at the end of each phase
    (setup / pre-recon / recon / risk-scoring / vulnerability-analysis /
    attack-chain / reporting) to surface phase completion, mirroring the
    phase-start event emitted by ``log_phase_start_activity``.
    """
    from shannon_whitebox.audit.session_registry import get_audit_session
    phase = input.phase or input.workspace_name or "unknown"
    await get_audit_session().log_phase_complete(phase)


@activity.defn
async def run_authz_gitnexus_judge(input: ActivityInput) -> dict:
    """GitNexus track LLM chain-judgement pass for authz (spec §5.7).

    1. Build IDOR candidates from code_index.json + framework_analysis.json
       (dominance heuristic + framework auto-generated).
    2. If candidates exist, render them into the authz_gitnexus_judge prompt
       and call run_claude_prompt (single call, structured JSON output).
    3. Parse the LLM verdicts leniently, tag each with source_track="gitnexus"
       + evidence_chain (the candidate path), write authz_gitnexus_queue.json.

    No candidates → write empty queue, skip the LLM call (save cost).
    Lenient on LLM output: invalid JSON → empty queue, no crash.

    This writes ONLY authz_gitnexus_queue.json (never authz_exploitation_queue.json
    — that's the LLM track's; Plan 3 merges them). It does NOT go through
    executor.execute (no git checkpoint, no validator, no auto queue write).
    """
    from shannon_whitebox.audit.session_registry import get_audit_session
    from shannon_core.code_index.authz_gitnexus_track import build_authz_gitnexus_track
    from shannon_core.models.queue_schemas import VulnerabilityQueue

    try:
        async with get_audit_session().track_step(
            "vulnerability-analysis", "authz-gitnexus-judge",
            intent=intent_for("authz-gitnexus-judge"),
        ):
            repo, deliverables, _ = _get_paths(input)
            md, dom_cands, fw_cands = build_authz_gitnexus_track(str(deliverables))
            candidate_count = len(dom_cands) + len(fw_cands)

            vulnerabilities: list[dict] = []
            if candidate_count > 0:
                prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
                prompt_manager = PromptManager(prompts_dir)
                prompt = prompt_manager.load_sync(
                    "authz_gitnexus_judge",
                    variables={
                        "authz_gitnexus_candidates": md,
                    },
                )
                result = await run_claude_prompt(
                    prompt=prompt,
                    repo_path=str(repo),
                    model_tier="medium",
                    api_key=input.api_key,
                    structured_output_schema={
                        "type": "object",
                        "properties": {
                            "vulnerabilities": {"type": "array"},
                        },
                    },
                )
                raw = result.structured_output
                if raw is None and result.text:
                    raw = result.text  # fallback to text; parse_lenient handles
                parsed = VulnerabilityQueue.parse_lenient(
                    raw if isinstance(raw, str) else json.dumps(raw) if raw is not None else "{}"
                )
                for v in parsed.queue.vulnerabilities:
                    data = v.model_dump()
                    data["source_track"] = "gitnexus"
                    if not data.get("evidence_chain"):
                        data["evidence_chain"] = "gitnexus track candidate (dominance/framework)"
                    vulnerabilities.append(data)

            atomic_write_json(
                deliverables / "authz_gitnexus_queue.json",
                {"vulnerabilities": vulnerabilities},
            )
            return {
                "candidate_count": candidate_count,
                "verdict_count": len(vulnerabilities),
                "dominance_candidates": len(dom_cands),
                "framework_candidates": len(fw_cands),
            }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_credential_check(input: ActivityInput) -> None:

    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        async with get_audit_session().track_step("setup", "credential-check", intent=intent_for("credential-check")):
            from shannon_core.agents.providers import build_provider_config

            config = build_provider_config(api_key=input.api_key or None)
            if config.api_key or config.type != "anthropic_api":
                await validate_credentials(
                    config.type,
                    api_key=config.api_key,
                    base_url=config.base_url,
                    auth_token=config.auth_token,
                    model=config.large_model or config.medium_model or config.model,
                )
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_auth_validation(input: ActivityInput) -> None:
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        async with get_audit_session().track_step("setup", "auth-validation", intent=intent_for("auth-validation")):
            from shannon_core.services.validate_authentication import validate_authentication
            from shannon_core.prompts.manager import PromptManager
            from shannon_core.agents.executor import AgentExecutor

            prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
            prompt_manager = PromptManager(prompts_dir)
            executor = AgentExecutor(prompt_manager)

            repo, deliverables, _ = _get_paths(input)
            result = await validate_authentication(
                web_url=input.web_url,
                config_path=input.config_path,
                prompt_manager=prompt_manager,
                executor=executor,
                repo_path=input.repo_path,
                deliverables_path=str(deliverables),
                api_key=input.api_key,
                workspace_path=input.workspace_path or "",
                audit_logger=create_activity_logger(),
            )
            if not result.success:
                raise PentestError(
                    f"Authentication validation failed: {result.failure_detail or 'unknown'}",
                    category="preflight",
                    retryable=False,
                    error_code=ErrorCode.AUTH_LOGIN_FAILED,
                )
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e

@activity.defn
async def run_code_index(input: ActivityInput) -> dict:
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        import logging
        from shannon_core.code_index import build_code_index_with_gitnexus, write_index_files
        from shannon_core.code_index.gitnexus_mcp import GitNexusMCPClient

        logger = logging.getLogger(__name__)

        repo, deliverables, _ = _get_paths(input)

        async with get_audit_session().track_step("pre-recon", "code-index", intent=intent_for("code-index")):
            # Create LLM client for taint analysis
            async def _llm_taint_client(prompt: str, **kwargs) -> str:
                # P1: real per-function LLM taint is not wired yet (cost).
                # Raising (not returning "{}") routes analyze_taint_llm to its
                # conservative fallback so the taint channel is non-empty
                # instead of silently dead.
                raise RuntimeError(
                    "LLM taint client not wired in production; "
                    "analyze_taint_llm will use conservative fallback"
                )

            # --- GitNexus integration ---
            # GitNexus MCP serves ALL indexed repos from its global registry
            # (~/.gitnexus/registry.json).  The correct order is:
            #   1. `gitnexus analyze <repo>`  — index & register the repo
            #   2. `gitnexus mcp`             — start MCP server (no --repo flag)
            # If GitNexus is unavailable, indexing fails, or MCP fails, we raise PentestError — no degradation.
            from shannon_core.code_index.gitnexus_engine import GitNexusEngine

            engine = GitNexusEngine(Path(repo))
            if not engine.is_available():
                raise PentestError(
                    "GitNexus CLI not available, cannot build code index. "
                    "Install with: npm install -g gitnexus",
                    category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
                )
            result = engine.ensure_indexed()
            if not result.success:
                raise PentestError(
                    f"GitNexus indexing failed: {result.error_message}. "
                    "Code index requires a working GitNexus index.",
                    category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
                )

            try:
                async with GitNexusMCPClient(Path(repo)) as mcp:
                    index, rule_gaps = await build_code_index_with_gitnexus(
                        str(repo),
                        mcp_client=mcp,
                        llm_client=_llm_taint_client,
                        auto_index=False,
                    )
            except PentestError:
                raise
            except Exception as exc:
                raise PentestError(
                    f"GitNexus MCP query failed: {exc}. "
                    "Code index requires a working GitNexus MCP connection.",
                    category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
                ) from exc

            json_path, summary_path = write_index_files(
                index, str(deliverables), rule_gaps=rule_gaps,
            )

        return {
            "total_blocks": index.total_blocks,
            "total_entry_points": index.total_entry_points,
            "total_chains": index.total_chains,
            "json_path": str(json_path),
            "summary_path": str(summary_path),
        }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_entry_point_fusion(input: ActivityInput) -> dict:
    """Merge deterministic entry points with LLM-discovered entry points."""
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.code_index import run_entry_point_fusion as _fusion

        repo, deliverables, _ = _get_paths(input)
        async with get_audit_session().track_step("pre-recon", "entry-point-fusion", intent=intent_for("entry-point-fusion")):
            index = _fusion(str(deliverables))

        return {
            "total_entry_points": index.total_entry_points,
            "status": "ok",
        }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_save_adjudication(input: ActivityInput) -> dict:
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.code_index import save_adjudication

        repo, deliverables, _ = _get_paths(input)
        async with get_audit_session().track_step("pre-recon", "adjudication", intent=intent_for("adjudication")):
            save_adjudication(str(deliverables))

        return {"status": "ok"}
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_merge_sink_reports(input: ActivityInput) -> dict:
    """Merge deterministic sinks with LLM-discovered sinks from pre-recon."""
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.code_index.sink_merger import merge_sink_reports
        from shannon_core.code_index.parameter_models import SinkCallSite
        from shannon_core.code_index.models import CodeIndex

        repo, deliverables, _ = _get_paths(input)

        async with get_audit_session().track_step("pre-recon", "merge-sinks", intent=intent_for("merge-sinks")):
            # Load deterministic sinks from code_index.json
            code_index_path = deliverables / "code_index.json"
            det_sinks: list[SinkCallSite] = []
            index = None
            if code_index_path.exists():
                index = CodeIndex.model_validate_json(code_index_path.read_text())
                det_sinks = index.sink_call_sites

            # Read LLM pre-recon deliverable
            llm_report = ""
            pre_recon_path = deliverables / "pre_recon_deliverable.md"
            if pre_recon_path.exists():
                llm_report = pre_recon_path.read_text()

            # Merge
            merged = merge_sink_reports(det_sinks, llm_report)

            # Write merged sinks back to code_index.json (reuse existing index)
            if index is not None:
                index.sink_call_sites = merged
                atomic_write_json(code_index_path, json.loads(index.model_dump_json()))

        return {
            "deterministic_count": len(det_sinks),
            "llm_only_count": len(merged) - len(det_sinks),
            "total_count": len(merged),
        }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_merge_dual_track_queues(input: ActivityInput) -> dict:
    """Merge LLM-track and GitNexus-track vulnerability queues."""
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.code_index.dual_track_merger import merge_dual_track_queues
        from shannon_core.models.queue_schemas import VulnerabilityQueue

        _, deliverables, _ = _get_paths(input)

        merged_classes: list[str] = []
        per_class_counts: dict[str, dict] = {}

        async with get_audit_session().track_step(
            "vulnerability-analysis",
            "merge-dual-track",
            intent=intent_for("merge-dual-track"),
        ):
            for vuln_class in ("injection", "xss", "ssrf", "authz", "auth"):
                exploitation_path = deliverables / f"{vuln_class}_exploitation_queue.json"
                if not exploitation_path.exists():
                    continue

                llm_path = deliverables / f"{vuln_class}_llm_queue.json"
                llm_path.write_text(exploitation_path.read_text(encoding="utf-8"), encoding="utf-8")
                llm_parsed = VulnerabilityQueue.parse_lenient(llm_path.read_text(encoding="utf-8"))
                llm_findings = llm_parsed.queue.vulnerabilities

                gitnexus_findings = []
                gitnexus_path = deliverables / f"{vuln_class}_gitnexus_queue.json"
                if gitnexus_path.exists():
                    gitnexus_parsed = VulnerabilityQueue.parse_lenient(
                        gitnexus_path.read_text(encoding="utf-8")
                    )
                    gitnexus_findings = gitnexus_parsed.queue.vulnerabilities

                merged = merge_dual_track_queues(
                    llm_findings,
                    gitnexus_findings,
                    mode="verdict",
                )
                atomic_write_json(
                    exploitation_path,
                    {"vulnerabilities": [finding.model_dump() for finding in merged]},
                )

                merged_classes.append(vuln_class)
                per_class_counts[vuln_class] = {
                    "llm": len(llm_findings),
                    "gitnexus": len(gitnexus_findings),
                    "merged": len(merged),
                    "both": sum(1 for finding in merged if finding.merge_source == "both"),
                    "llm_only": sum(1 for finding in merged if finding.merge_source == "llm-only"),
                    "gitnexus_only": sum(
                        1 for finding in merged if finding.merge_source == "gitnexus-only"
                    ),
                    "warnings": llm_parsed.warnings,
                }

        return {"merged_classes": merged_classes, "per_class_counts": per_class_counts}
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_risk_scoring(input: ActivityInput) -> dict:
    """Score call chains and produce tiered audit plan."""
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.code_index.models import CodeIndex
        from shannon_core.code_index.parameter_models import ParameterPropagationGraph
        from shannon_core.code_index.risk_scorer import AuditBudget
        from shannon_core.code_index.tiered_audit import TieredAuditPlanner

        repo, deliverables, _ = _get_paths(input)

        async with get_audit_session().track_step("risk-scoring", "risk-scoring", intent=intent_for("risk-scoring")):
            # Load code index
            code_index_path = deliverables / "code_index.json"
            if not code_index_path.exists():
                return {"total_chains": 0, "tier3_count": 0, "tier2_count": 0, "tier1_count": 0}

            index = CodeIndex.model_validate_json(code_index_path.read_text())

            # Load parameter graph
            param_graph_path = deliverables / "parameter_graph.json"
            taint_flows_by_chain: dict[str, list] = {}
            if param_graph_path.exists():
                pgraph = ParameterPropagationGraph.model_validate_json(
                    param_graph_path.read_text()
                )
                for flow in pgraph.taint_flows:
                    taint_flows_by_chain.setdefault(flow.entry_point_id, []).append(flow)

            # Build block lookup
            blocks_by_id = {b.id: b for b in index.blocks}

            # Auth middleware detection: simple heuristic — functions with
            # auth/login/token/verify in name or decorators
            auth_ids: set[str] = set()
            for block in index.blocks:
                combined = f"{block.function_name} {' '.join(block.decorators)}".lower()
                if any(kw in combined for kw in ("auth", "login", "token", "verify", "session")):
                    auth_ids.add(block.id)

            # Plan
            planner = TieredAuditPlanner(
                chains=index.chains,
                blocks_by_id=blocks_by_id,
                taint_flows_by_chain=taint_flows_by_chain,
                auth_middleware_ids=auth_ids,
                budget=AuditBudget(),
                sink_call_sites=index.sink_call_sites,
            )
            plan = planner.plan()

            # Write audit plan
            plan_path = deliverables / "audit_plan.json"
            plan_data = json.loads(plan.to_json())
            atomic_write_json(plan_path, plan_data)

        return {
            "total_chains": plan.total_chains,
            "tier3_count": plan.tier3_count,
            "tier2_count": plan.tier2_count,
            "tier1_count": plan.tier1_count,
            "estimated_llm_calls": plan.estimated_llm_calls,
        }
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def render_findings(input: ActivityInput) -> None:
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.services.findings_renderer import FindingsRenderer
        from shannon_core.config.parser import parse_config

        _, deliverables, _ = _get_paths(input)
        async with get_audit_session().track_step("reporting", "render-findings", intent=intent_for("render-findings")):
            report_config = None
            if input.config_path:
                cfg = parse_config(input.config_path)
                report_config = cfg.report
            await FindingsRenderer.render_findings_from_queues(deliverables, report_config)
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def assemble_report(input: ActivityInput) -> None:
    """轴1:把各 *_analysis_deliverable.md 拼接成 comprehensive report。

    ReportAssembler 已实现 evidence → findings → analysis_deliverable 三级回退,
    天然支持 white-box 产物。拼接产物随后由 REPORT agent(report-executive)
    加执行摘要并清理。
    """
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.services.report_assembler import ReportAssembler

        _, deliverables, _ = _get_paths(input)
        report_path = deliverables / "comprehensive_security_assessment_report.md"
        vuln_classes = list(ALL_VULN_CLASSES)
        async with get_audit_session().track_step(
            "reporting", "assemble-report", intent=intent_for("assemble-report")
        ):
            await ReportAssembler.assemble(deliverables, vuln_classes, report_path)
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


async def _gitnexus_verdict_llm_client(prompt: str, **kwargs) -> str:
    """GitNexus-track chain-verdict LLM pass client.

    Production: reuses the same LLM client pool as run_agent. This stub is the
    injection point -- when not configured, callers must pass their own client
    (tests do). Default raises so judge_chain_verdict takes its conservative
    needs_review path (does NOT silently clear).
    """
    raise RuntimeError(
        "GitNexus-track chain-verdict LLM client not configured; "
        "judge_chain_verdict will mark candidates needs_review"
    )


@activity.defn
async def run_gitnexus_chain_verdict(input: ActivityInput) -> dict:
    """GitNexus-track chain verdict for injection/xss/ssrf (spec §5.4-5.6).

    Reads parameter_graph.json (Plan 1) + code_index.json (sink_call_sites for
    XSS routing), runs the light chain-verdict pass for the three trace-class
    vuln types, and writes <vuln>_gitnexus_queue.json for each. Plan 3's
    run_merge_dual_track_queues then does verdict OR against the LLM track.

    Graceful degradation: no parameter_graph.json (Plan 1 not landed) ->
    per_class empty, no gitnexus queues written, merger falls back to llm-only.
    """
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.code_index.models import CodeIndex
        from shannon_core.code_index.parameter_models import (
            ParameterPropagationGraph,
            SinkCallSite,
        )
        from shannon_core.code_index.vuln_chain_builders.injection_builder import (
            build_injection_findings,
        )
        from shannon_core.code_index.vuln_chain_builders.xss_builder import (
            build_xss_findings,
        )
        from shannon_core.code_index.vuln_chain_builders.ssrf_builder import (
            build_ssrf_findings,
        )
        from shannon_core.utils.atomic_write import atomic_write_json

        repo, deliverables, _ = _get_paths(input)
        per_class: dict[str, int] = {}

        pgraph_path = deliverables / "parameter_graph.json"
        if not pgraph_path.exists():
            return {"per_class": {}, "skipped": "no parameter_graph.json"}
        try:
            pgraph = ParameterPropagationGraph.model_validate_json(pgraph_path.read_text())
        except Exception:
            return {"per_class": {}, "skipped": "invalid parameter_graph.json"}

        # XSS routes by SinkCallSite.category == XSS (SlotContext has no render
        # context), so read code_index.json for the sink call sites.
        sink_call_sites: dict[str, SinkCallSite] = {}
        code_index_path = deliverables / "code_index.json"
        if code_index_path.exists():
            try:
                index = CodeIndex.model_validate_json(code_index_path.read_text())
                sink_call_sites = {s.id: s for s in index.sink_call_sites}
            except Exception as exc:
                logger.warning("gitnexus chain-verdict: code_index.json parse failed (%s)", exc)

        async with get_audit_session().track_step(
            "vulnerability-analysis", "gitnexus-chain-verdict",
            intent=None,
        ):
            llm = _gitnexus_verdict_llm_client

            for vc, builder in (
                ("injection", build_injection_findings),
                ("xss", build_xss_findings),
                ("ssrf", build_ssrf_findings),
            ):
                try:
                    if vc == "xss":
                        findings = await builder(pgraph, llm_client=llm,
                                                 sink_call_sites=sink_call_sites)
                    else:
                        findings = await builder(pgraph, llm_client=llm)
                except Exception as exc:
                    # one vuln class failing must not block the others
                    logger.warning("gitnexus chain-verdict %s failed: %s", vc, exc)
                    continue
                if findings:
                    atomic_write_json(
                        deliverables / f"{vc}_gitnexus_queue.json",
                        {"vulnerabilities": [f.model_dump() for f in findings]},
                    )
                    per_class[vc] = len(findings)

        return {"per_class": per_class}
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_framework_analysis(input: ActivityInput) -> dict:
    """Detect auto-REST frameworks, infer endpoints, write deliverable."""
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.services.framework_analyzer import analyze_frameworks

        repo, deliverables, _ = _get_paths(input)
        async with get_audit_session().track_step("pre-recon", "framework-analysis", intent=intent_for("framework-analysis")):
            result = await analyze_frameworks(str(repo))

            # Write result as JSON deliverable
            import dataclasses
            result_data = dataclasses.asdict(result)
            result_path = deliverables / "framework_analysis.json"
            atomic_write_json(result_path, result_data)

        return {
            "detected_framework": result.detected_framework.name if result.detected_framework else None,
            "inferred_endpoint_count": len(result.inferred_endpoints),
            "recommendation_count": len(result.recommendations),
        }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_frontend_mapping(input: ActivityInput) -> dict:
    """Map frontend routes to API calls, identify XSS chains, write deliverable."""
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.services.frontend_mapper import map_frontend_routes

        repo, deliverables, _ = _get_paths(input)
        async with get_audit_session().track_step("pre-recon", "frontend-mapping", intent=intent_for("frontend-mapping")):
            result = await map_frontend_routes(str(repo))

            # Write result as JSON deliverable
            import dataclasses
            result_data = dataclasses.asdict(result)
            result_path = deliverables / "frontend_mapping.json"
            atomic_write_json(result_path, result_data)

        return {
            "route_count": len(result.routes),
            "xss_chain_count": len(result.xss_chains),
        }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_auth_config_scan(input: ActivityInput) -> dict:
    """Deterministic auth-config scan (spec §5.8 vuln-auth GitNexus track).

    Scans the repo for auth/session config issues (cookie flags, HSTS, CORS,
    JWT nOAuth claims, rate-limit middleware) and writes two deliverables:
      1. auth_config_scan.json — structured scan result (LLM reads this as a
         starting point, like vuln-authz reads Endpoint Security Context).
      2. auth_gitnexus_queue.json — each suspicious config item as an
         AuthVulnerability (source_track='gitnexus'), consumed by Plan 3's
         run_merge_dual_track_queues for verdict OR with the LLM track.

    Pure additive: zero findings still writes both files (empty), so the
    merger degrades cleanly to llm-only. Scan failures never abort the vuln
    phase (logged, empty result).
    """
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        import dataclasses
        from shannon_core.services.auth_config_scanner import scan_auth_config
        from shannon_core.models.queue_schemas import AuthVulnerability

        repo, deliverables, _ = _get_paths(input)
        async with get_audit_session().track_step(
            "vulnerability-analysis", "auth-config-scan",
            intent=intent_for("auth-config-scan"),
        ):
            result = await scan_auth_config(str(repo))

            # 1. Structured scan result (LLM reads this)
            scan_data = dataclasses.asdict(result)
            atomic_write_json(deliverables / "auth_config_scan.json", scan_data)

            # 2. GitNexus-track queue (Plan 3 merger consumes this)
            vulns = [_finding_to_auth_vulnerability(f) for f in result.all_findings()]
            atomic_write_json(
                deliverables / "auth_gitnexus_queue.json",
                {"vulnerabilities": [v.model_dump() for v in vulns]},
            )

            total = len(result.all_findings())
        return {
            "total_findings": total,
            "cookie": len(result.cookie_findings),
            "hsts": len(result.hsts_findings),
            "cors": len(result.cors_findings),
            "jwt_claim": len(result.jwt_claim_findings),
            "rate_limit": len(result.rate_limit_findings),
        }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


# Category -> AUTH-VULN vulnerability_type mapping.
# externally_exploitable=True for all (conservative over-report; merger ORs).
_CATEGORY_TO_VULN_TYPE = {
    "cookie": "Session_Management_Flaw",
    "hsts": "Transport_Exposure",
    "cors": "Transport_Exposure",
    "jwt_claim": "Login_Flow_Logic",      # nOAuth is a login-flow identity flaw
    "rate_limit": "Abuse_Defenses_Missing",
}
_CATEGORY_TO_SUGGESTED_TECHNIQUE = {
    "cookie": "session_hijacking",
    "hsts": "credential_theft_via_mitm",
    "cors": "credential_theft_via_cors",
    "jwt_claim": "noauth_attribute_hijack",
    "rate_limit": "brute_force_login",
}


def _finding_to_auth_vulnerability(f) -> "AuthVulnerability":
    """Convert a deterministic ConfigFinding into an AuthVulnerability for the
    GitNexus-track queue (source_track='gitnexus')."""
    from shannon_core.models.queue_schemas import AuthVulnerability
    vuln_type = _CATEGORY_TO_VULN_TYPE.get(f.category, "Session_Management_Flaw")
    technique = _CATEGORY_TO_SUGGESTED_TECHNIQUE.get(f.category, "session_hijacking")
    location = f"{f.file_path}:{f.line}"
    return AuthVulnerability(
        ID=f"AUTH-GN-{f.category.upper()}-{abs(hash((f.file_path, f.line, f.category))) % 100000:05d}",
        vulnerability_type=vuln_type,
        externally_exploitable=True,   # conservative: scanner hit -> exploitable candidate
        confidence="medium",           # deterministic signal, LLM confirms/denies
        source_track="gitnexus",
        evidence_chain=f"[deterministic scan] {f.category}@{location}: {f.detail}",
        source_endpoint=None,          # config-level, not endpoint-scoped
        vulnerable_code_location=location,
        missing_defense=f.detail,
        exploitation_hypothesis=(
            f"Attacker can exploit the missing/weak auth configuration: {f.detail}"
        ),
        suggested_exploit_technique=technique,
        notes=f"GitNexus-track candidate (awaiting LLM confirmation). Evidence: {f.evidence}",
    )


@activity.defn
async def run_route_chain_building(input: ActivityInput) -> dict:
    """Build route chain map from framework + frontend analysis results."""
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.services.framework_analyzer import FrameworkAnalysisResult
        from shannon_core.services.frontend_mapper import FrontendAnalysisResult, XssAttackChain, FrontendRoute
        from shannon_core.services.route_chain_builder import build_attack_chains_from_analysis
        import dataclasses
        import logging

        repo, deliverables, _ = _get_paths(input)
        log = logging.getLogger(__name__)

        async with get_audit_session().track_step("pre-recon", "route-chain-building", intent=intent_for("route-chain-building")):
            # Load framework analysis result
            framework_result = FrameworkAnalysisResult()
            framework_path = deliverables / "framework_analysis.json"
            if framework_path.exists():
                data = json.loads(framework_path.read_text())
                endpoints = [_to_endpoint(ep) for ep in data.get("inferred_endpoints", []) if isinstance(ep, dict)]
                framework_result = FrameworkAnalysisResult(
                    inferred_endpoints=endpoints,
                    recommendations=data.get("recommendations", []),
                )

            # Load frontend mapping result
            frontend_result = FrontendAnalysisResult()
            frontend_path = deliverables / "frontend_mapping.json"
            if frontend_path.exists():
                data = json.loads(frontend_path.read_text())
                def _to_route(d: dict) -> FrontendRoute:
                    return FrontendRoute(
                        path=d["path"], component=d["component"], authenticated=d["authenticated"],
                    )
                def _to_xss(d: dict) -> XssAttackChain:
                    return XssAttackChain(
                        entry_point=d["entry_point"], storage_endpoint=d["storage_endpoint"],
                        render_endpoint=d["render_endpoint"], sink=d["sink"], confidence=d["confidence"],
                    )
                routes = [_to_route(r) for r in data.get("routes", []) if isinstance(r, dict)]
                xss_chains = [_to_xss(c) for c in data.get("xss_chains", []) if isinstance(c, dict)]
                frontend_result = FrontendAnalysisResult(routes=routes, xss_chains=xss_chains)

            chains = build_attack_chains_from_analysis(
                framework_result.inferred_endpoints, frontend_result.routes, frontend_result.xss_chains, log,
            )

            # Write chains
            chains_data = [dataclasses.asdict(c) for c in chains]
            chains_path = deliverables / "route_chains.json"
            atomic_write_json(chains_path, chains_data)

        return {"chain_count": len(chains)}
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_attack_chain_assembly(input: ActivityInput) -> dict:
    """Assemble multi-step attack chains from all analysis results."""
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.services.framework_analyzer import FrameworkAnalysisResult, InferredEndpoint
        from shannon_core.services.frontend_mapper import FrontendAnalysisResult, XssAttackChain, FrontendRoute
        from shannon_core.services.attack_chain_builder import build_attack_chains
        import dataclasses
        import logging

        repo, deliverables, _ = _get_paths(input)
        log = logging.getLogger(__name__)

        async with get_audit_session().track_step("attack-chain", "attack-chain-assembly", intent=intent_for("attack-chain-assembly")):
            # Load results (JSON stores tuples as lists, convert back)
            def _to_endpoint(d: dict) -> InferredEndpoint:
                return InferredEndpoint(
                    method=d["method"], path=d["path"], source=d["source"],
                    model=d.get("model"), middleware=tuple(d.get("middleware", [])),
                    vulnerability_indicators=tuple(d.get("vulnerability_indicators", [])),
                )

            def _to_route(d: dict) -> FrontendRoute:
                return FrontendRoute(
                    path=d["path"], component=d["component"], authenticated=d["authenticated"],
                )

            def _to_xss(d: dict) -> XssAttackChain:
                return XssAttackChain(
                    entry_point=d["entry_point"], storage_endpoint=d["storage_endpoint"],
                    render_endpoint=d["render_endpoint"], sink=d["sink"], confidence=d["confidence"],
                )

            framework_result = FrameworkAnalysisResult()
            framework_path = deliverables / "framework_analysis.json"
            if framework_path.exists():
                data = json.loads(framework_path.read_text())
                endpoints = [_to_endpoint(ep) for ep in data.get("inferred_endpoints", []) if isinstance(ep, dict)]
                framework_result = FrameworkAnalysisResult(
                    inferred_endpoints=endpoints,
                    recommendations=data.get("recommendations", []),
                )

            frontend_result = FrontendAnalysisResult()
            frontend_path = deliverables / "frontend_mapping.json"
            if frontend_path.exists():
                data = json.loads(frontend_path.read_text())
                routes = [_to_route(r) for r in data.get("routes", []) if isinstance(r, dict)]
                xss_chains = [_to_xss(c) for c in data.get("xss_chains", []) if isinstance(c, dict)]
                frontend_result = FrontendAnalysisResult(routes=routes, xss_chains=xss_chains)

            chains = await build_attack_chains(framework_result, frontend_result, log)

            # Write assembled chains
            chains_data = [dataclasses.asdict(c) for c in chains]
            chains_path = deliverables / "attack_chains.json"
            atomic_write_json(chains_path, chains_data)

        return {"chain_count": len(chains)}
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
