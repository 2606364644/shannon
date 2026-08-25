import asyncio
import json
import logging
import time
import os
from datetime import timedelta
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError as ApplicationFailure

from supernova_core.models.agents import AgentName, AGENTS, ALL_VULN_CLASSES, VulnType
from supernova_core.models.audit import WorkflowSummary
from supernova_core.models.errors import (
    ErrorCode,
    PentestError,
    classify_error_for_temporal,
    classify_for_temporal_with_retry_cap,
)
from supernova_core.models.metrics import AgentMetrics
from supernova_core.models.retry import agent_retry_category, retry_for
from supernova_core.runtime.heartbeat import stop_heartbeat
from supernova_core.utils.atomic_write import atomic_write_json
from supernova_core.utils.paths import intermediate_path, resolve_deliverables_path, resolve_intermediate
from supernova_core.utils.credential_validator import validate_credentials
from supernova_core.logging import create_activity_logger
from supernova_core.logging.log_bus import LogBus
from supernova_core.agents.executor import AgentExecutor
from supernova_core.agents.runner import run_claude_prompt
from supernova_core.agents.recon_context_summarizer import summarize_recon_context
from supernova_core.config.concurrency import is_gitnexus_llm_enabled
from supernova_core.prompts.manager import PromptManager
from supernova_core.session import SessionManager
from supernova_whitebox.audit.session import AuditSession
from supernova_core.audit.session_recovery import (
    build_headless_audit_session,
    ensure_audit_session,
)

from .shared import ActivityInput
from .step_intents import intent_for

logger = logging.getLogger(__name__)


def _get_paths(input: ActivityInput) -> tuple[Path, Path, Path]:
    from supernova_core.utils.paths import WHITEBOX_SUBDIR

    # workspace_path（web=event_file.parent=scan_dir；CLI=repo.parent/workspaces/<ws>）由
    # WhiteboxScanWorkflow 算好传入（workflows.py C1 Phase B）。优先用它作 deliverables 根，
    # 使产物落 scan_dir，与 web DeliverablesReader / get_workspace_vuln_counts 读取口径对齐——
    # 修 2026-07-30 分裂：_get_paths 旧用 resolve_deliverables_path(workspace_name=scan_id) 落
    # workspaces/<scan_id>/ 平铺目录，web 在 scan_dir/deliverables 读不到 → 前端 0 漏洞。
    # 无 workspace_path（activity 被直接调用、不经 workflow）回落 resolve_deliverables_path。
    if input.workspace_path:
        deliverables = Path(input.workspace_path) / input.deliverables_subdir
    else:
        deliverables = resolve_deliverables_path(
            repo_path=input.repo_path,
            deliverables_subdir=input.deliverables_subdir,
            workspace_name=input.workspace_name,
        )
    # 白盒产物隔离到 deliverables/whitebox/（与黑盒 blackbox/ 对称）。
    # 写侧永远落新结构；黑盒读白盒 queue 走 resolve_track_deliverable fallback。
    deliverables = deliverables / WHITEBOX_SUBDIR
    repo = Path(input.repo_path)
    workspaces = repo.parent / "workspaces"
    return repo, deliverables, workspaces


def _to_endpoint(d: dict):
    """Reconstruct framework analyzer endpoint dataclasses from JSON."""
    from supernova_core.services.framework_analyzer import InferredEndpoint

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
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    try:
        async with get_audit_session().track_step("setup", "preflight", intent=intent_for("preflight")):
            # Config parsing validation
            if input.config_path:
                from supernova_core.config.parser import parse_config
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


def _vuln_max_turns(agent_name: str) -> int | None:
    """vuln agent 用专用 max_turns(SUPERNOVA_VULN_MAX_TURNS,默认 500);其他返回 None。

    返回 None 时,executor → run_claude_prompt → provider 沿用各引擎全局 env 默认
    (CLAUDE_MAX_TURNS / SUPERNOVA_OPENAI_MAX_TURNS = 200),行为零变更。
    B2: 仅 vuln 单独配,不污染 pre-recon/recon/report。
    """
    if agent_retry_category(agent_name) == "vuln":
        return int(os.getenv("SUPERNOVA_VULN_MAX_TURNS", "500"))
    return None


def _vuln_output_schema(agent_name: AgentName) -> dict | None:
    """Phase 2 B 拓扑(spec 2026-08-19 §3.5):恒返 None,vuln agent 停传结构化输出。

    queue 数据通道已切换到 collector(submit_finding 单条上交 + finding_roster 对账,
    executor 写盘);末条大 JSON 通道停用——CLI --json-schema 与 collected_text 兜底
    对 vuln 不再激活,网关断流的原始故障形态(session 正常结束带半截 JSON)在 vuln
    通道不复存在。历史:本函数曾补「schema 未传 → queue 永不落盘」的断线(见原
    docstring,对齐原始 TS getOutputFormat 的 *-vuln 宽松基线 schema),Phase 2 起
    该断线由 collector 通道接管。

    恒返 None(原 *-vuln / *-exploit 之分随之失效;exploit 仍为 None)。
    """
    return None


@activity.defn
async def run_agent(input: ActivityInput) -> dict:
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    from supernova_whitebox.audit.session_tool_audit_logger import SessionToolAuditLogger
    from supernova_core.models.audit import AgentEndResult, end_result_from_pentest_error

    agent_name = AgentName(input.agent_name or input.workspace_name)
    attempt = activity.info().attempt
    # Resolve once; reused by both except branches so the display can render
    # 将重试 N/M (attempt/max) without recomputing.
    max_attempts = retry_for(
        agent_retry_category(agent_name.value)).maximum_attempts
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

        if agent_name == AgentName.PRE_RECON:
            prompt_variables = prompt_variables or {}

        if _is_vuln_agent(agent_name):
            prompt_variables = await _build_vuln_prompt_variables(
                input, prompt_variables or {}
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
            max_turns=_vuln_max_turns(agent_name.value),
            structured_output_schema=_vuln_output_schema(agent_name),
            provider_config=input.provider_config,   # P3c 阶段 1
        )
        await tool_audit_logger.close(success=True, duration_ms=metrics.duration_ms)
        await session.end_agent(agent_name.value, AgentEndResult(
            success=True,
            duration_ms=metrics.duration_ms,
            cost_usd=metrics.cost_usd or 0.0,
            cost_currency=metrics.cost_currency,
            attempt_number=attempt,
            model=metrics.model,
            num_turns=metrics.num_turns,
            input_tokens=metrics.input_tokens,
            output_tokens=metrics.output_tokens,
            cache_read_tokens=metrics.cache_read_tokens,
            cache_creation_tokens=metrics.cache_creation_tokens,
        ))
        return metrics.model_dump()
    except PentestError as e:
        dur_ms = int((time.monotonic() - agent_start) * 1000)
        await tool_audit_logger.close(success=False, duration_ms=dur_ms)
        # 失败 agent 也记 cost：从 PentestError.context 取 executor 携带的真实消耗
        # （修 error path cost 归 0），取不到回落 0（非 executor raise）。
        await session.end_agent(
            agent_name.value,
            end_result_from_pentest_error(e, duration_ms=dur_ms, attempt_number=attempt))
        await session.log_error(
            e, context=agent_name.value, attempt=attempt, max_attempts=max_attempts)
        # classify + OUTPUT_VALIDATION 独立 cap(对齐 TS MAX_OUTPUT_VALIDATION_RETRIES=3):
        # vuln agent 反复不吐 exploitation_queue 时,attempt >= cap 则停止重试(non_retryable),
        # 而非吃满通用 VULN_RETRY(8) 白烧 ~5×20min。
        error_type, retryable = classify_for_temporal_with_retry_cap(e, attempt)
        # log_error surfaces to the live display; ApplicationFailure surfaces to
        # Temporal for retry decisions — both are intended, not double-logging.
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        await tool_audit_logger.close(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000))
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(
            e, context=agent_name.value, attempt=attempt, max_attempts=max_attempts)
        error_type, retryable = classify_for_temporal_with_retry_cap(e, attempt)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e

@activity.defn
async def run_vuln_agent(input: ActivityInput) -> dict:
    return await run_agent(input)


# ── vuln prompt_variables 注入（Task 7 / SharedKnowledge 接通） ──────────
# 5 个 vuln agent（INJECTION_VULN/XSS_VULN/SSRF_VULN/AUTHZ_VULN/AUTH_VULN）专用：在 vuln prompt
# 渲染前注入 {{RECON_CONTEXT}}（LLM 摘要 recon_deliverable.md §4+§8）+
# {{FRAMEWORK_ANALYSIS}}（条件：framework_analysis.json inferred_endpoints 非空）。
# 守铁律（CLAUDE.md §1）：注入源仅限 LLM 轨产物（recon md + pre-recon 代码层推断），
# 绝不引 GitNexus 确定性层产物。
_VULN_AGENT_NAMES = frozenset({
    AgentName.INJECTION_VULN,
    AgentName.XSS_VULN,
    AgentName.SSRF_VULN,
    AgentName.AUTHZ_VULN,
    AgentName.AUTH_VULN,
})


def _is_vuln_agent(agent_name: AgentName) -> bool:
    return agent_name in _VULN_AGENT_NAMES


def _make_recon_summary_llm_client(repo_path: str, provider_config: dict | None = None):   # P3c 阶段 1：透传 run_claude_prompt
    """LLM client for summarize_recon_context.

    Always attempts an LLM call (not gated by GitNexus-LLM toggle, since the
    summarizer belongs to the LLM track, not GitNexus). When the LLM provider
    itself is unavailable, run_claude_prompt raises and the summarizer degrades
    gracefully to raw §4/§8 extraction (non-fatal).
    """
    async def _client(prompt: str, **kwargs) -> str:
        result = await run_claude_prompt(
            prompt=prompt, repo_path=repo_path, model_tier="medium",
            provider_config=provider_config,
        )
        return result.text
    return _client


async def _build_vuln_prompt_variables(
    input: ActivityInput, base: dict
) -> dict:
    """Inject structured recon prior knowledge into vuln prompt_variables.

    - RECON_CONTEXT: LLM-summarized §4/§8 of recon_deliverable.md (always injected;
      degrades to raw extract if LLM unavailable). Source = LLM-track recon output.
    - FRAMEWORK_ANALYSIS: from framework_analysis.json — injected ONLY when
      inferred_endpoints is non-empty (whitebox samples are often empty).
    Both sources are LLM-track / code-layer pre-recon output — NEVER GitNexus
    deterministic-layer (CLAUDE.md §1 ironclad rule).
    """
    repo, deliverables, _ = _get_paths(input)

    # RECON_CONTEXT: summarize recon_deliverable.md §4/§8
    recon_md_path = deliverables / "recon_deliverable.md"
    recon_md = recon_md_path.read_text("utf-8") if recon_md_path.exists() else ""
    llm_client = _make_recon_summary_llm_client(str(repo), provider_config=input.provider_config)   # P3c 阶段 1
    recon_context = await summarize_recon_context(recon_md, llm_client)
    base["RECON_CONTEXT"] = recon_context

    # FRAMEWORK_ANALYSIS: conditional — only when inferred_endpoints non-empty
    fw_path = resolve_intermediate(deliverables, "framework_analysis.json")  # tiering 双路径
    fw_lines: list[str] = []
    if fw_path is not None:
        try:
            fw = json.loads(fw_path.read_text("utf-8"))
            endpoints = fw.get("inferred_endpoints", []) or []
            if endpoints:
                fw_lines.append(
                    "Framework-inferred endpoints (auto-generated, verify each):"
                )
                for ep in endpoints:
                    fw_lines.append(
                        f"- {ep.get('method', '?')} {ep.get('path', '?')} "
                        f"[source={ep.get('source', '?')}, "
                        f"middleware={ep.get('middleware', [])}]"
                    )
        except (json.JSONDecodeError, OSError):
            pass  # non-fatal
    base["FRAMEWORK_ANALYSIS"] = "\n".join(fw_lines) if fw_lines else ""

    return base


@activity.defn
async def log_phase_start_activity(input: ActivityInput, steps: list[str] | None = None,
                                   intents: list[str] | None = None) -> None:
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
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
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    phase = input.phase or input.workspace_name or "unknown"
    await get_audit_session().log_phase_complete(phase)


def _entry_points_brief(http_route_count: int, entry_point_total: int) -> str:
    """spec-1a T4: 格式化 entry_points_summary 给 explore prompt。

    确定性层识别的入口点摘要（explore prompt 会提示此为「可能不全，自行 grep 补」）。
    """
    return (
        f"{http_route_count} http_route / {entry_point_total} total entry points"
        if (http_route_count or entry_point_total)
        else "0 http_route / 0 total (deterministic layer identified no entry points; grep routes yourself)"
    )


@activity.defn
async def log_info_activity(input: ActivityInput) -> None:
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    try:
        await get_audit_session().log_info(input.info_message, input.info_level)
    except Exception:
        pass  # best-effort: 显示侧通道失败绝不影响扫描（尤其 except 块里调，避免替换原异常）


@activity.defn
async def write_track_status_activity(input: ActivityInput) -> dict:
    """Task 4 fail-fast: 写 gitnexus_track_status.json(workflow->activity->helper).

    Task 1 helper 的薄 activity 包装: workflow 在两 GitNexus activity(authz_judge /
    chain_verdict)执行后, 汇总 per-class 状态(inj/xss/ssrf + authz), 经本 activity
    落盘 gitnexus_track_status.json. merger/report 读它做开轨标红; workflow 读
    返回值决定关轨终止(关轨 + DEGRADABLE fail).

    铁律 (CLAUDE.md §1): 状态产物只给 workflow/merger/report 编排用, 绝不喂 LLM 轨 prompt.
    activity 不直接写文件 -- 调 Task 1 的 write_track_status helper (守「不假估算」).
    """
    from supernova_core.code_index.gitnexus_track_status import write_track_status
    _, deliverables, _ = _get_paths(input)
    write_track_status(deliverables, getattr(input, "track_statuses", {}))
    return {"written": True}


def _parse_gitnexus_verdict_output(raw, id_prefix):
    """补缺 ID 的 GitNexus 轨 verdict/explore 输出 → parse_lenient → (vulns, warnings)。

    探索/判定 agent 产出的候选可能缺 ID(authz_gitnexus_explore prompt schema 无 ID
    字段),而 BaseVulnerability.ID 必填 → parse_lenient 校验丢弃 → 静默落地 0
    (回归 hr_20260713:agent 找到 4 个候选,authz_gitnexus_queue.json 落地 0)。
    这里在 parse 前给缺 ID 的条目补序列化 ID(如 AUTHZ-GN-EXPLORE-01),防静默丢数据。

    warnings 由 parse_lenient 产出(dropped 计数等);callers MUST 打日志
    (守 queue_schemas.parse_lenient docstring 的 "callers MUST surface, never silent")。
    """
    from supernova_core.models.queue_schemas import VulnerabilityQueue
    raw_str = raw if isinstance(raw, str) else (json.dumps(raw) if raw is not None else "{}")
    try:
        data = json.loads(raw_str)
        vulns = data.get("vulnerabilities") if isinstance(data, dict) else None
        if isinstance(vulns, list):
            for idx, v in enumerate(vulns):
                if isinstance(v, dict) and not v.get("ID"):
                    v["ID"] = f"{id_prefix}{idx + 1:02d}"
            raw_str = json.dumps(data)
    except (json.JSONDecodeError, ValueError, TypeError):
        pass  # raw 非 JSON → parse_lenient 以 invalid_json 形式兜底
    parsed = VulnerabilityQueue.parse_lenient(raw_str)
    return list(parsed.queue.vulnerabilities), parsed.warnings


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
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    from supernova_core.code_index.authz_gitnexus_track import build_authz_gitnexus_track
    from supernova_core.models.queue_schemas import VulnerabilityQueue

    try:
        failed = False
        fail_reason: str | None = None
        async with get_audit_session().track_step(
            "vulnerability-analysis", "authz-gitnexus-judge",
            intent=intent_for("authz-gitnexus-judge"),
        ):
            repo, deliverables, _ = _get_paths(input)
            try:
                md, dom_cands, fw_cands, http_route_count, entry_point_total = build_authz_gitnexus_track(str(deliverables))
            except Exception as exc:
                failed = True
                fail_reason = f"build_authz_gitnexus_track failed: {exc}"
                logger.warning("authz gitnexus build track failed: %s", exc)
                # tiering：*_gitnexus_queue.json 属中间产物 → 桶内 intermediate/
                atomic_write_json(
                    intermediate_path(deliverables, "authz_gitnexus_queue.json"),
                    {"vulnerabilities": []})
                return {"candidate_count": 0, "verdict_count": 0, "dominance_candidates": 0,
                        "framework_candidates": 0, "failed": True, "fail_reason": fail_reason}
            candidate_count = len(dom_cands) + len(fw_cands)

            # 可观测性（spec §3.2）：GitNexus 轨候选状态经 InfoEvent 通道，避免静默空转。
            # best-effort：显示通道失败绝不影响扫描（对齐 log_info_activity 防御）。
            try:
                _session = get_audit_session()
                if candidate_count == 0:
                    await _session.log_info(
                        f"authz GitNexus 轨：0 候选（dominance={len(dom_cands)}, "
                        f"framework={len(fw_cands)}；http_route 入口点="
                        f"{http_route_count}/{entry_point_total}）→ 进入自主探索分支"
                        f"（GitNexus 轨内部补召回）。",
                        "warning",
                    )
                else:
                    await _session.log_info(
                        f"authz GitNexus 轨：{candidate_count} 候选（dominance="
                        f"{len(dom_cands)}, framework={len(fw_cands)}）→ 调 LLM 判定。",
                        "info",
                    )
            except Exception:
                pass

            vulnerabilities: list[dict] = []
            # prompt_manager 两分支共用（T4：0 候选探索分支也要加载 explore prompt）。
            prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
            prompt_manager = PromptManager(prompts_dir)
            if candidate_count > 0:
                prompt = prompt_manager.load_sync(
                    "authz_gitnexus_judge",
                    variables={
                        "authz_gitnexus_candidates": md,
                    },
                )
                try:
                    result = await run_gitnexus_verdict_agent(
                        prompt=prompt,
                        repo_path=str(repo),
                        structured_output_schema={
                            "type": "object",
                            "properties": {
                                "vulnerabilities": {"type": "array"},
                            },
                        },
                        audit_session=get_audit_session(),
                        provider_config=input.provider_config,   # P3c 阶段 1
                    )
                except Exception as exc:
                    failed = True
                    fail_reason = f"verdict agent failed: {exc}"
                    logger.warning("authz gitnexus verdict agent failed: %s", exc)
                    vulnerabilities = []
                else:
                    raw = result.structured_output
                    if raw is None and result.text:
                        raw = result.text
                    gn_vulns, gn_warnings = _parse_gitnexus_verdict_output(raw, "AUTHZ-GN-")
                    for v in gn_vulns:
                        data = v.model_dump()
                        data["source_track"] = "gitnexus"
                        if not data.get("evidence_chain"):
                            data["evidence_chain"] = "gitnexus track candidate (dominance/framework)"
                        vulnerabilities.append(data)
                    if gn_warnings:
                        try:
                            await get_audit_session().log_info(
                                f"authz GitNexus 轨：parse warnings (candidate>0): {gn_warnings}",
                                "warning",
                            )
                        except Exception:
                            pass

                    try:
                        await get_audit_session().log_info(
                            f"authz GitNexus 轨：产出 {len(vulnerabilities)} 条 verdict。",
                            "info",
                        )
                    except Exception:
                        pass
            else:
                # spec-1a G2：0 候选不静默写空 queue——多轮 agent 自主探索仓库找 IDOR。
                # 确定性层常因入口点未识别（语言误判/调用图未就绪/纯静态页）漏召回，
                # agent 自主 grep route + read handler 补候选（软候选，needs_review=True）。
                explore_prompt = prompt_manager.load_sync(
                    "authz_gitnexus_explore",
                    variables={
                        "entry_points_summary": _entry_points_brief(
                            http_route_count, entry_point_total
                        ),
                    },
                )
                try:
                    result = await run_gitnexus_verdict_agent(
                        prompt=explore_prompt,
                        repo_path=str(repo),
                        structured_output_schema={
                            "type": "object",
                            "properties": {
                                "vulnerabilities": {"type": "array"},
                            },
                        },
                        audit_session=get_audit_session(),
                        provider_config=input.provider_config,   # P3c 阶段 1
                    )
                except Exception as exc:
                    failed = True
                    fail_reason = f"explore agent failed: {exc}"
                    logger.warning("authz gitnexus explore agent failed: %s", exc)
                    vulnerabilities = []
                else:
                    raw = result.structured_output
                    if raw is None and result.text:
                        raw = result.text
                    gn_vulns, gn_warnings = _parse_gitnexus_verdict_output(raw, "AUTHZ-GN-EXPLORE-")
                    for v in gn_vulns:
                        data = v.model_dump()
                        data["source_track"] = "gitnexus"
                        data["needs_review"] = True  # 探索发现，软候选（未经确定性 dominance 验证）
                        if not data.get("evidence_chain"):
                            data["evidence_chain"] = "gitnexus explore-discovered (0 deterministic candidates)"
                        vulnerabilities.append(data)
                    if gn_warnings:
                        try:
                            await get_audit_session().log_info(
                                f"authz GitNexus 轨（探索）：parse warnings: {gn_warnings}",
                                "warning",
                            )
                        except Exception:
                            pass

                    try:
                        await get_audit_session().log_info(
                            f"authz GitNexus 轨（探索）：0 确定性候选 → 自主探索产出 "
                            f"{len(vulnerabilities)} 条软候选（needs_review=True）。",
                            "info",
                        )
                    except Exception:
                        pass

            atomic_write_json(
                intermediate_path(deliverables, "authz_gitnexus_queue.json"),
                {"vulnerabilities": vulnerabilities},
            )
            return {
                "candidate_count": candidate_count,
                "verdict_count": len(vulnerabilities),
                "dominance_candidates": len(dom_cands),
                "framework_candidates": len(fw_cands),
                "failed": failed,
                "fail_reason": fail_reason,
            }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_credential_check(input: ActivityInput) -> None:

    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    try:
        async with get_audit_session().track_step("setup", "credential-check", intent=intent_for("credential-check")):
            # P3c 阶段 1：web 穿线优先（input.provider_config），CLI 兜底 build from env。
            from supernova_core.agents.providers import build_provider_config
            from supernova_core.agents.runner import ProviderConfig

            if input.provider_config:
                config = ProviderConfig(**input.provider_config)
            else:
                config = build_provider_config(api_key=input.api_key or None)
            # 不再对「anthropic_api + 无 api_key」特殊跳过：glm-anthropic 走 auth_token
            # （validate_credentials 新增 Bearer 预检分支），凭据全空（web 不 load_env 错配）
            # 由 validate_credentials fail-fast。避免静默放行后 worker 满屏 "Not logged in"。
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
async def run_code_index(input: ActivityInput) -> dict:
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    try:
        import logging
        from supernova_core.code_index import build_code_index_with_gitnexus, write_index_files
        from supernova_core.code_index.gitnexus_mcp import GitNexusMCPClient

        logger = logging.getLogger(__name__)

        repo, deliverables, _ = _get_paths(input)

        async with get_audit_session().track_step("pre-recon", "code-index", intent=intent_for("code-index")):
            # Create LLM client for taint analysis (+ LLM sink discovery)
            def _make_gitnexus_llm_client(repo_path: str, provider_config: dict | None = None):   # P3c 阶段 1：透传 run_claude_prompt
                """封装 run_claude_prompt 成 analyze_taint_llm/discover 期望的
                async (prompt, output_format=None)->str 契约。

                output_format（JSON Schema）透传给 run_claude_prompt -> CLI --json-schema
                强制模型吐合法 JSON + SDK structured_output 预解析（对齐 TS outputFormat 通道，
                根因治本：GLM 不再返回 Markdown 文本致 json.loads 崩）。优先返回
                structured_output（json.dumps 成 str 保持契约）；空则回退 result.text，由下游
                _extract_json_payload + 各自 fallback 兜底（三重防线）。

                env 关时返回 None → consumer 入口各自静默降级(discover_sinks/sources 早退返回空,
                analyze_taint_llm 走 deterministic fallback), 不再跑 N 个无用的 raise-task 刷屏。"""
                if not is_gitnexus_llm_enabled():
                    return None

                async def _client(prompt: str, **kwargs) -> str:
                    result = await run_claude_prompt(
                        prompt=prompt, repo_path=repo_path, model_tier="medium",
                        structured_output_schema=kwargs.get("output_format"),
                        provider_config=provider_config,
                    )
                    # structured_output 由 provider 填充（SDK 原生优先，缺失时 _extract_json_payload
                    # 从 collected_text 兜底）。非空时它是已解析的 dict/list，json.dumps 还原成 str
                    # 契约供下游 parse。空 → 回退 .text（下游 _extract_json_payload + fallback）。
                    so = result.structured_output
                    if so is not None:
                        import json as _json
                        return _json.dumps(so)
                    return result.text
                return _client

            _llm_taint_client = _make_gitnexus_llm_client(str(repo), provider_config=input.provider_config)   # P3c 阶段 1

            # --- GitNexus integration ---
            # GitNexus MCP serves ALL indexed repos from its global registry
            # (~/.gitnexus/registry.json).  The correct order is:
            #   1. `gitnexus analyze <repo>`  — index & register the repo
            #   2. `gitnexus mcp`             — start MCP server (no --repo flag)
            # If GitNexus is unavailable, indexing fails, or MCP fails, we raise PentestError — no degradation.
            from supernova_core.code_index.gitnexus_engine import GitNexusEngine

            engine = GitNexusEngine(Path(repo))
            if not engine.is_available():
                raise PentestError(
                    "GitNexus CLI not available, cannot build code index. "
                    "Install with: npm install -g gitnexus",
                    category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
                )
            result = await engine.ensure_indexed_async()
            if not result.success:
                raise PentestError(
                    f"GitNexus indexing failed: {result.error_message}. "
                    "Code index requires a working GitNexus index.",
                    category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
                )

            try:
                # resolve medium-tier model 名(spec 2026-07-10): 传给 discovery 做 chunk
                # threshold 派生(按模型 context 自适应)。不裸读 SUPERNOVA_MODEL(=large tier,
                # 与 gitnexus 轨实际用的 medium tier 错配会估大 context 致爆)。resolve 失败
                # -> None -> discovery 走默认 context, 不阻断。
                from supernova_core.agents.providers import build_provider_config, resolve_tier_model
                from supernova_core.agents.runner import ProviderConfig
                try:
                    # P3c 阶段 1：web 穿线优先（input.provider_config），CLI 兜底 build from env。
                    if input.provider_config:
                        _pcfg = ProviderConfig(**input.provider_config)
                    else:
                        _pcfg = build_provider_config(api_key=input.api_key or None)
                    _medium_model = resolve_tier_model(_pcfg, "medium")
                except Exception:
                    _medium_model = None

                async with GitNexusMCPClient(Path(repo)) as mcp:
                    index, rule_gaps, source_gaps, storage_gaps = await build_code_index_with_gitnexus(
                        str(repo),
                        mcp_client=mcp,
                        llm_client=_llm_taint_client,
                        auto_index=False,
                        progress_cb=_make_gitnexus_progress_cb(get_audit_session()),
                        model=_medium_model,
                    )
            except PentestError:
                raise
            except ConnectionError as exc:
                # MCP 子进程冷启动/握手超时是并发负载下的瞬时环境错误（真机
                # NodeGoat-20260821-044404：LLM subagent 抢 CPU，node 冷启动 >30s，
                # initialize 读超时）。retryable=True 让 CODE_INDEX_RETRY 重试——
                # 重试时 node 二进制已进 page cache，二次启动快；仍失败才真死。
                # engine 不可用/索引失败等配置类错误不在此列，保持 non-retryable。
                raise PentestError(
                    f"GitNexus MCP query failed: {exc}. "
                    "Code index requires a working GitNexus MCP connection.",
                    category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
                    retryable=True,
                ) from exc
            except Exception as exc:
                raise PentestError(
                    f"GitNexus MCP query failed: {exc}. "
                    "Code index requires a working GitNexus MCP connection.",
                    category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
                ) from exc

            json_path, summary_path = write_index_files(
                index, str(deliverables),
                rule_gaps=rule_gaps, source_gaps=source_gaps,
                storage_gaps=storage_gaps,
            )

            # tiering 保护（git_manager.commit_index，spec 2026-08-18）：中间产物
            # 提交为跟踪文件，防并发 pre-recon agent 失败时 rollback(clean -fd)清掉
            # 未跟踪的 intermediate/（run_code_index 已成功、不在重试循环 → 永不重生，
            # 下游 fusion/merge 硬报 FileNotFoundError）。commit 失败不阻断：产物仍在
            # 盘上，读侧 resolve_intermediate 兜底。
            try:
                from supernova_core.git_manager import GitManager
                await GitManager.commit_index(deliverables)
            except Exception as exc:
                logger.warning("commit_index failed (non-fatal): %s", exc)

            # 可观测性：调用图统计。chains=0 是 GitNexus 轨空壳的核心信号
            #（→ taint_flows=0 → 3 类 builder 全空 → GitNexus 轨无结果）。
            # 对齐 06-29 authz/injection-gitnexus-track-observability 的 InfoEvent 风格。
            try:
                empty_call_graph = index.total_chains == 0
                await get_audit_session().log_info(
                    f"GitNexus code-index：blocks={index.total_blocks}, "
                    f"entry_points={index.total_entry_points}, chains={index.total_chains}, "
                    f"degradation={index.degradation_level}"
                    + (" → ⚠️ 调用图空壳（chains=0 → taint_flows=0 → GitNexus 轨将无结果）"
                       if empty_call_graph else ""),
                    "warning" if empty_call_graph else "info",
                )
            except Exception:
                pass

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
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    try:
        from supernova_core.code_index import run_entry_point_fusion as _fusion

        repo, deliverables, _ = _get_paths(input)
        async with get_audit_session().track_step("pre-recon", "entry-point-fusion", intent=intent_for("entry-point-fusion")):
            index = _fusion(str(deliverables), repo_path=str(repo))

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
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    try:
        from supernova_core.code_index import save_adjudication

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
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    try:
        from supernova_core.code_index.sink_merger import merge_sink_reports
        from supernova_core.code_index.parameter_models import SinkCallSite
        from supernova_core.code_index.models import CodeIndex

        repo, deliverables, _ = _get_paths(input)

        async with get_audit_session().track_step("pre-recon", "merge-sinks", intent=intent_for("merge-sinks")):
            # Load deterministic sinks from code_index.json
            code_index_path = resolve_intermediate(deliverables, "code_index.json")  # tiering 双路径
            det_sinks: list[SinkCallSite] = []
            index = None
            if code_index_path is not None:
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
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    try:
        from supernova_core.code_index.dual_track_merger import merge_dual_track_queues
        from supernova_core.code_index.gitnexus_track_status import read_track_status
        from supernova_core.models.queue_schemas import VulnerabilityQueue

        _, deliverables, _ = _get_paths(input)
        # GitNexus 轨 per-class 状态(Task 4 写,文件缺/损坏返 {} 容错)。
        # 仅供 merger 标记 gitnexus_status + 报告标红;合并逻辑不读它(failed 类
        # 自然 gitnexus_findings=[] -> llm-only 或 continue 跳过)。
        track_status = read_track_status(deliverables)

        merged_classes: list[str] = []
        per_class_counts: dict[str, dict] = {}

        async with get_audit_session().track_step(
            "vulnerability-analysis",
            "merge-dual-track",
            intent=intent_for("merge-dual-track"),
        ):
            for vuln_class in ("injection", "xss", "ssrf", "authz", "auth"):
                # tiering 双路径读：LLM 轨 queue 由 executor auto-write 落 intermediate/，
                # GitNexus 轨写侧同迁（老 session 平铺兜底）。平铺直拼曾致 LLM 轨
                # findings 恒空 → verdict OR 丢边。
                exploitation_path = resolve_intermediate(
                    deliverables, f"{vuln_class}_exploitation_queue.json")
                gitnexus_path = resolve_intermediate(
                    deliverables, f"{vuln_class}_gitnexus_queue.json")

                # GitNexus-track findings (may exist independently of LLM track)
                gitnexus_findings = []
                if gitnexus_path is not None:
                    gitnexus_parsed = VulnerabilityQueue.parse_lenient(
                        gitnexus_path.read_text(encoding="utf-8")
                    )
                    gitnexus_findings = gitnexus_parsed.queue.vulnerabilities

                # LLM-track findings. A4: LLM queue absent -> empty list, still merge
                # (GitNexus-only must reach the report, not be dropped). Skip only
                # when BOTH tracks are empty.
                llm_findings = []
                llm_warnings = []
                if exploitation_path is not None:
                    # 保 LLM 轨原始副本（tier：*_llm_queue.json → intermediate/）
                    llm_path = intermediate_path(
                        deliverables, f"{vuln_class}_llm_queue.json")
                    llm_path.parent.mkdir(parents=True, exist_ok=True)
                    llm_path.write_text(exploitation_path.read_text(encoding="utf-8"), encoding="utf-8")
                    llm_parsed = VulnerabilityQueue.parse_lenient(llm_path.read_text(encoding="utf-8"))
                    llm_findings = llm_parsed.queue.vulnerabilities
                    llm_warnings = llm_parsed.warnings
                elif not gitnexus_findings:
                    continue  # both tracks empty

                merged = merge_dual_track_queues(
                    llm_findings,
                    gitnexus_findings,
                    mode="verdict",
                )
                # 合并版写回 intermediate/（SSOT；下游 resolve_intermediate 优先读到合并版）
                atomic_write_json(
                    intermediate_path(deliverables, f"{vuln_class}_exploitation_queue.json"),
                    {"vulnerabilities": [finding.model_dump() for finding in merged]},
                )

                merged_classes.append(vuln_class)
                _ts = track_status.get(vuln_class, {})
                _gn_status = _ts.get("status", "absent")
                per_class_counts[vuln_class] = {
                    "llm": len(llm_findings),
                    "gitnexus": len(gitnexus_findings),
                    "merged": len(merged),
                    "both": sum(1 for finding in merged if finding.merge_source == "both"),
                    "llm_only": sum(1 for finding in merged if finding.merge_source == "llm-only"),
                    "gitnexus_only": sum(
                        1 for finding in merged if finding.merge_source == "gitnexus-only"
                    ),
                    "warnings": llm_warnings,
                    # GitNexus 轨状态(ok/failed/absent)。failed 类合并退 llm-only
                    # 或被跳过(双轨空);此处仅标红供报告,不影响合并产物。
                    "gitnexus_status": _gn_status,
                }
                if _gn_status == "failed":
                    per_class_counts[vuln_class]["gitnexus_fail_reason"] = _ts.get("reason")
                    logger.info(
                        "merge: vuln=%s GitNexus track failed (%s) - 标红供报告,合并退 llm-only",
                        vuln_class, _ts.get("reason"))
                gn_only = sum(1 for f in merged if f.merge_source == "gitnexus-only")
                if gn_only:
                    logger.info(
                        "merge: vuln=%s merged %d gitnexus-only findings (LLM track did not cover)",
                        vuln_class, gn_only)

        return {"merged_classes": merged_classes, "per_class_counts": per_class_counts}
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_assemble_dataflow_view(input: ActivityInput) -> dict:
    """P4: 组装 dataflow_view.json（spec 2026-08-20 §3；失败不阻塞扫描）。

    读 merge 后 SSOT queue + chain_verdicts 等 intermediate 产物，调 Task 7
    纯函数 assemble_dataflow_view 组装数据流视图，落
    intermediate/dataflow_view.json（报告页数据流视图用）。non-fatal 报告增强：
    全产物缺 → skipped 不落盘；任何异常 → logger.warning + skipped 返回值，
    **不抛 ApplicationFailure**（区别于本文件 fatal 活动惯例）。
    """
    try:
        from supernova_core.services.dataflow_view import assemble_dataflow_view

        _repo, deliverables, _ws = _get_paths(input)
        view = assemble_dataflow_view(deliverables)
        if view is None:
            return {"status": "skipped", "reason": "no products"}
        atomic_write_json(intermediate_path(deliverables, "dataflow_view.json"), view)
        return {"status": "ok", "trees": len(view.get("trees", []))}
    except Exception as exc:  # noqa: BLE001 — non-blocking（报告增强，绝不阻塞扫描）
        logger.warning("run_assemble_dataflow_view failed (non-blocking): %s", exc)
        return {"status": "skipped", "reason": str(exc)}


@activity.defn
async def run_risk_scoring(input: ActivityInput) -> dict:
    """Score call chains and produce tiered audit plan."""
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    try:
        from supernova_core.code_index.models import CodeIndex
        from supernova_core.code_index.parameter_models import ParameterPropagationGraph
        from supernova_core.code_index.risk_scorer import AuditBudget
        from supernova_core.code_index.tiered_audit import TieredAuditPlanner

        repo, deliverables, _ = _get_paths(input)

        async with get_audit_session().track_step("risk-scoring", "risk-scoring", intent=intent_for("risk-scoring")):
            # Load code index
            code_index_path = resolve_intermediate(deliverables, "code_index.json")  # tiering 双路径
            if code_index_path is None:
                return {"total_chains": 0, "tier3_count": 0, "tier2_count": 0, "tier1_count": 0}

            index = CodeIndex.model_validate_json(code_index_path.read_text())

            # Load parameter graph
            param_graph_path = resolve_intermediate(deliverables, "parameter_graph.json")  # tiering 双路径
            taint_flows_by_chain: dict[str, list] = {}
            if param_graph_path is not None:
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
            plan_path = intermediate_path(deliverables, "audit_plan.json")  # tiering
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
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    try:
        from supernova_core.services.findings_renderer import FindingsRenderer
        from supernova_core.config.parser import parse_config

        repo, deliverables, _ = _get_paths(input)
        async with get_audit_session().track_step("reporting", "render-findings", intent=intent_for("render-findings")):
            report_config = None
            if input.config_path:
                cfg = parse_config(input.config_path)
                report_config = cfg.report
            # repo_root：卡片「问题代码」snippet 确定性提取（spec 2026-08-25 §10.4）
            await FindingsRenderer.render_findings_from_queues(
                deliverables, report_config, repo_root=repo)
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
    加执行摘要并清理。攻击链章节由后续 inject_attack_chains activity 注入
    （report-executive 之后），避免被覆盖。GitNexus 轨判定状态注记由后续
    inject_gitnexus_track_status activity 注入（同样 report-executive 之后）。
    """
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    try:
        from supernova_core.services.report_assembler import ReportAssembler

        _, deliverables, _ = _get_paths(input)
        report_path = deliverables / "comprehensive_security_assessment_report.md"
        vuln_classes = input.vuln_classes or list(ALL_VULN_CLASSES)
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


@activity.defn
async def verify_report_vuln_blocks(input: ActivityInput) -> None:
    """report-executive 后校验 + 自愈:最终报告 ### ID 节数 vs 底稿期望数。

    回归(2026-08-19 另一环境):report agent 自写 cleanup 脚本把正文压成
    「模式汇总+行内 ID 引用」,丢掉全部结构化漏洞节——前端 splitByVulnBlocks
    解析 0 节 → 报告页统计全 0、PoC 无卡片可并。prompt 三处锁定节格式仍被
    绕过(SCRATCHPAD 允许 scripts 是口子),故加确定性防线:节数不足 → 重新
    assemble 覆盖 agent 版(丢执行摘要、保漏洞数据——数据完整性 > 摘要美化),
    后续 inject_* 照常追加。必须在 run-report-agent 之后、inject_attack_chains
    之前运行。校验/自愈失败不阻塞主报告(吞异常 + warning)。
    """
    log = logging.getLogger(__name__)
    try:
        from supernova_core.services.report_assembler import ReportAssembler
        from supernova_core.utils.file_io import async_path_exists

        _, deliverables, _ = _get_paths(input)
        report_path = deliverables / "comprehensive_security_assessment_report.md"
        if not await async_path_exists(report_path):
            return  # 主报告不存在(agent 失败),无处校验
        vuln_classes = input.vuln_classes or list(ALL_VULN_CLASSES)
        actual, expected = await ReportAssembler.verify_vuln_block_coverage(
            deliverables, vuln_classes, report_path)
        if actual >= expected:
            return  # 覆盖完好(含无漏洞扫描:0 >= 0)
        log.warning(
            "报告漏洞节覆盖不足(actual=%d < expected=%d):report-executive 丢失结构化"
            "漏洞节,重新 assemble 覆盖 agent 版(执行摘要丢弃,漏洞数据恢复)",
            actual, expected,
        )
        await ReportAssembler.assemble(deliverables, vuln_classes, report_path)
    except Exception as exc:  # noqa: BLE001 — 校验/自愈失败不阻塞主报告
        log.warning("verify_report_vuln_blocks failed (non-blocking): %s", exc)


@activity.defn
async def inject_attack_chains(input: ActivityInput) -> None:
    """报告阶段最后注入：attack_chains.json → ## 攻击链 章节追加到最终报告。

    必须在 run-report-agent 之后运行——report-executive agent 重写
    comprehensive_security_assessment_report.md（同 deliverable_filename）,
    若在此之前追加攻击链章节会被覆盖丢失（回归 hr_20260713-104726）。
    放最后注入,确保攻击链章节留存。幂等（标题已存在则跳过）。失败不阻塞。
    """
    log = logging.getLogger(__name__)
    try:
        from supernova_core.services.report_assembler import ReportAssembler
        from supernova_core.utils.file_io import (
            async_path_exists, async_read_file, async_write_file,
        )

        _, deliverables, _ = _get_paths(input)
        report_path = deliverables / "comprehensive_security_assessment_report.md"
        if not await async_path_exists(report_path):
            return  # 主报告不存在,无处追加
        chains_md = await ReportAssembler.render_attack_chains(deliverables)
        if not chains_md:
            return  # 无攻击链 / 渲染为空
        content = await async_read_file(report_path)
        if "## 攻击链（多步利用路径）" in content:
            return  # 幂等：已注入（resume/重跑）
        await async_write_file(report_path, content + chains_md)
    except Exception as exc:  # noqa: BLE001 — 攻击链注入失败不阻塞主报告
        log.warning("inject_attack_chains failed (non-blocking): %s", exc)


@activity.defn
async def inject_gitnexus_track_status(input: ActivityInput) -> None:
    """GitNexus 轨 fail-fast 状态注记(report-executive 之后注入,防覆盖)。

    必须在 run-report-agent 之后运行——report-executive agent 重写整个
    comprehensive_security_assessment_report.md,若在此之前注入会被覆盖丢失
    (对齐 inject_attack_chains 的模式,fail-fast plan Task 6 fix 2026-07-19)。
    读 Task 1 的 gitnexus_track_status.json,若有 failed 类,在报告顶部(原 H1
    之后)插入 ## GitNexus 轨判定状态 章节,逐条列出 failed 类 + reason +
    「结果由 LLM 轨提供」。

    幂等(标题已存在则跳过)。失败不阻塞主报告(吞异常 + warning)。
    无 failed 类 / 主报告缺 → 直接 return。
    """
    log = logging.getLogger(__name__)
    try:
        from supernova_core.code_index.gitnexus_track_status import read_track_status
        from supernova_core.utils.file_io import (
            async_path_exists, async_read_file, async_write_file,
        )

        _, deliverables, _ = _get_paths(input)
        report_path = deliverables / "comprehensive_security_assessment_report.md"
        if not await async_path_exists(report_path):
            return  # 主报告不存在,无处注入

        track_status = read_track_status(deliverables)
        failed_notes = [
            f"- {vc}: GitNexus 轨判定失败({s.get('reason', 'unknown')}),结果由 LLM 轨提供"
            for vc, s in track_status.items()
            if isinstance(s, dict) and s.get("status") == "failed"
        ]
        if not failed_notes:
            return  # 无 failed 类,不改报告

        content = await async_read_file(report_path)
        if "## GitNexus 轨判定状态" in content:
            return  # 幂等:已注入(resume/重跑)

        banner = (
            "## GitNexus 轨判定状态\n\n"
            + "\n".join(failed_notes)
            + "\n\n---\n\n"
        )
        # 插在报告顶部;若首个非空行是 H1,插在 H1 之后更自然(避免 H2 先于 H1)
        lines = content.split("\n")
        i = 0
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i < len(lines) and lines[i].lstrip().startswith("# "):
            head = "\n".join(lines[: i + 1])
            tail = "\n".join(lines[i + 1 :]).lstrip("\n")
            new_content = head + "\n\n" + banner + tail
        else:
            new_content = banner + content
        await async_write_file(report_path, new_content)
    except Exception as exc:  # noqa: BLE001 — 注记注入失败不阻塞主报告
        log.warning("inject_gitnexus_track_status failed (non-blocking): %s", exc)


@activity.defn
async def generate_poc_report(input: ActivityInput) -> None:
    """报告增强：生成 curl/Burp PoC md。失败不阻塞主报告（吞异常）。"""
    import logging
    log = logging.getLogger(__name__)
    try:
        from supernova_core.services.poc_generator import PoCGenerator
        from supernova_core.models.config import ALL_VULN_CLASSES

        _, deliverables, _ = _get_paths(input)
        await PoCGenerator.generate(
            deliverables_dir=deliverables,
            vuln_classes=input.vuln_classes or list(ALL_VULN_CLASSES),
            target_url=(input.web_url or None),
            track="whitebox",
            repo_path=input.repo_path,
            api_key=input.api_key,
            provider_config=input.provider_config,   # P3c 阶段 1
        )
    except Exception as exc:  # noqa: BLE001 — 报告增强失败绝不阻塞主流程
        log.warning("poc: whitebox generate_poc_report failed (non-blocking): %s", exc)


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


def _make_verdict_llm_client(repo_path: str, provider_config: dict | None = None):   # P3c 阶段 1：透传 run_claude_prompt
    """接通后: 真 client; env 关时返回 raise-client(降级)。

    output_format（JSON Schema）透传 run_claude_prompt -> CLI --json-schema，强制模型
    吐合法 JSON + SDK structured_output（对齐 TS outputFormat，根因治本 GLM Markdown 崩）。
    优先返回 structured_output（json.dumps 还原 str 契约），空则回退 result.text 走 extract。
    """
    if not is_gitnexus_llm_enabled():
        return _gitnexus_verdict_llm_client  # 模块级 raise 兜底
    from supernova_core.agents.runner import run_claude_prompt

    async def _client(prompt: str, **kwargs) -> str:
        result = await run_claude_prompt(
            prompt=prompt, repo_path=repo_path, model_tier="medium",
            structured_output_schema=kwargs.get("output_format"),
            provider_config=provider_config,
        )
        so = result.structured_output
        if so is not None:
            import json as _json
            return _json.dumps(so)
        return result.text
    return _client


async def run_gitnexus_verdict_agent(
    *,
    prompt: str,
    repo_path: str,
    structured_output_schema: dict | None = None,
    audit_session: "AuditSession | None" = None,
    provider_config: dict | None = None,   # P3c 阶段 1：穿线下传 run_claude_prompt
) -> "ClaudeRunResult":
    """GitNexus 多轮 verdict agent：带 grep/read 自主追链，吃确定性候选做深度判定。

    max_turns 走 SUPERNOVA_GITNEXUS_VERDICT_MAX_TURNS（默认 30）。返回完整 ClaudeRunResult
    （含 turns/cost/structured_output），不截断为 str——区别于 _make_verdict_llm_client 的单次薄包装。

    audit_session 非 None 时构造 SessionToolAuditLogger（对齐 run_agent :167/183/198），多轮
    grep/read 工具调用经逐轮审计；为 None 时 tool_audit_logger=None（行为同前，向后兼容）。

    供 spec-1 的 run_authz_gitnexus_judge 多轮判定用。单测 mock run_claude_prompt 验证
    max_turns 透传 / audit_session 注入 tool_audit_logger。
    """
    from supernova_core.agents.runner import run_claude_prompt  # 延迟 import，对齐 :859
    tool_audit_logger = None
    if audit_session is not None:
        from supernova_whitebox.audit.session_tool_audit_logger import (
            SessionToolAuditLogger,
        )
        tool_audit_logger = SessionToolAuditLogger(
            audit_session, "gitnexus-verdict", attempt=1
        )
    agent_start = time.monotonic()
    try:
        if tool_audit_logger is not None:
            await tool_audit_logger.initialize()
        return await run_claude_prompt(
            prompt=prompt,
            repo_path=repo_path,
            model_tier="medium",
            max_turns=int(os.getenv("SUPERNOVA_GITNEXUS_VERDICT_MAX_TURNS", "30")),
            structured_output_schema=structured_output_schema,
            tool_audit_logger=tool_audit_logger,
            provider_config=provider_config,   # P3c 阶段 1
        )
    finally:
        if tool_audit_logger is not None:
            # 异常向上抛由 caller 处理；finally 内保守传 success（best-effort，对齐 run_agent）。
            await tool_audit_logger.close(
                success=True,
                duration_ms=int((time.monotonic() - agent_start) * 1000),
            )


def _make_gitnexus_progress_cb(session):
    """采样 + 包装 session.log_gitnexus_progress。best-effort（吞 session 异常）。

    触发规则：final→summary；note 非空→note；detail 非空→hit；done==1 或 done%10==0→progress；其余静默。
    phase 透传自 sample.phase（core 的 ProgressEmitter 已带 sink-discovery /
    source-discovery / taint-analysis / chain-verdict）。cb=None 路径（LLM 关）由
    core emitter 兜底（emitter 自身 cb=None no-op），非本层职责。
    """
    async def cb(sample) -> None:
        if sample.final:
            kind, detail = "summary", sample.detail
        elif sample.note:
            kind, detail = "note", sample.note
        elif sample.detail:
            kind, detail = "hit", sample.detail
        elif sample.done == 1 or sample.done % 10 == 0:
            kind, detail = "progress", None
        else:
            return
        try:
            await session.log_gitnexus_progress(
                sample.phase, kind, sample.done, sample.total, sample.hits, detail)
        except Exception:
            pass
    return cb


def _ann_to_dict(item):
    """sanitizer_annotations 元素归一为可 JSON 序列化的 dict。

    真实数据流：CandidateChain.sanitizer_annotations 的元素是
    SanitizerAnnotation（sanitizer_library.py:33，frozen dataclass，字段
    rule_id/defense_type/applies_to/code_location/matched_text），finding 的
    原始属性存的是这些实例——原样塞 json.dumps 抛 TypeError（Fix round 2：
    safe 链才带 sanitizer → 恰好 safe 链炸掉整个 chain_verdicts.json 产物）。
    注意 gitnexus_queue 路径不受影响（f.model_dump() 会自动转 dict），只有
    本 dump 的 raw getattr 路径需要归一。

    防御顺序：dict 原样保留 → pydantic model_dump() → stdlib dataclass
    asdict() → str() 最终兜底（不崩，保留可读 repr）。
    """
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump()
    import dataclasses
    if dataclasses.is_dataclass(item) and not isinstance(item, type):
        return dataclasses.asdict(item)
    return str(item)


def _dump_chain_verdicts(
    deliverables: Path,
    vc: str,
    findings: list,
) -> None:
    """落 intermediate/{vc}_chain_verdicts.json；safe 链也进。零 finding 不落盘。

    findings 已含 safe 链（builder 对所有 candidate 都产 finding，safe 的
    externally_exploitable=False/verdict='safe'）。shape 对齐 spec 2026-08-20 §4
    P1 + Task 7 组装器读 ``verdicts.get("verdicts", [])``。

    finding→dump 字段映射（防御 getattr 兜底，适配三类 taint finding 不同字段名）：
    - flow_id: 三类均补（Task 2）；缺失降级空串。
    - sink_call_site_id: injection/xss 取 sink_call；ssrf 无 sink_call →
      降级 vulnerable_code_location（builder 写的即 chain.sink_call_site_id）→ 空。
    - verdict/mismatch_reason/confidence: 三类 builder 均从 ChainVerdict 复制。
    - reason: inj/xss 走 mismatch_reason；ssrf 无 mismatch_reason → 兜底取
      missing_defense（ssrf_builder 把 verdict.mismatch_reason 写进 missing_defense）。
    - sanitizer_annotations: 三类 builder 均从 CandidateChain.sanitizer_annotations
      复制进 finding（Task 2 Fix F2）；元素经 _ann_to_dict 归一（SanitizerAnnotation
      dataclass 实例 → dict，Fix round 2）；降级空 list（spec §6 容忍旧 finding）。
    """
    if not findings:
        return
    rows = []
    for f in findings:
        rows.append({
            "flow_id": getattr(f, "flow_id", "") or "",
            "sink_call_site_id": (
                getattr(f, "sink_call", "") or getattr(f, "vulnerable_code_location", "") or ""
            ),
            "vuln_class": vc,
            "verdict": getattr(f, "verdict", "") or "",
            "reason": (
                getattr(f, "mismatch_reason", "") or getattr(f, "missing_defense", "") or ""
            ),
            "sanitizer_annotations": [
                _ann_to_dict(a) for a in (getattr(f, "sanitizer_annotations", []) or [])
            ],
            "confidence": getattr(f, "confidence", "") or "",
        })
    atomic_write_json(
        intermediate_path(deliverables, f"{vc}_chain_verdicts.json"),
        {"verdicts": rows},
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
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    try:
        from supernova_core.code_index.models import CodeIndex, EntryPoint
        from supernova_core.code_index.parameter_models import (
            ParameterPropagationGraph,
            SinkCallSite,
        )
        from supernova_core.code_index.vuln_chain_builders.injection_builder import (
            build_injection_findings,
        )
        from supernova_core.code_index.vuln_chain_builders.xss_builder import (
            build_xss_findings,
        )
        from supernova_core.code_index.vuln_chain_builders.ssrf_builder import (
            build_ssrf_findings,
        )
        from supernova_core.utils.atomic_write import atomic_write_json

        repo, deliverables, _ = _get_paths(input)
        per_class: dict[str, int] = {}
        failed_classes: list[str] = []
        fail_reasons: dict[str, str] = {}

        pgraph_path = resolve_intermediate(deliverables, "parameter_graph.json")  # tiering 双路径
        if pgraph_path is None:
            try:
                await get_audit_session().log_info(
                    "GitNexus 注入轨：parameter_graph.json 缺失 → 3 类判定失败（fail-fast，不降级）。",
                    "warning",
                )
            except Exception:
                pass
            _reason = "parameter_graph.json missing"
            for _vc in ("injection", "xss", "ssrf"):
                failed_classes.append(_vc)
                fail_reasons[_vc] = _reason
            return {
                "per_class": {},
                "failed_classes": failed_classes,
                "fail_reasons": fail_reasons,
            }
        try:
            pgraph = ParameterPropagationGraph.model_validate_json(pgraph_path.read_text())
        except Exception:
            try:
                await get_audit_session().log_info(
                    "GitNexus 注入轨：parameter_graph.json 无效 → 3 类判定失败（fail-fast，不降级）。",
                    "warning",
                )
            except Exception:
                pass
            _reason = "parameter_graph.json invalid"
            for _vc in ("injection", "xss", "ssrf"):
                failed_classes.append(_vc)
                fail_reasons[_vc] = _reason
            return {
                "per_class": {},
                "failed_classes": failed_classes,
                "fail_reasons": fail_reasons,
            }

        # XSS routes by SinkCallSite.category == XSS (SlotContext has no render
        # context), so read code_index.json for the sink call sites.
        sink_call_sites: dict[str, SinkCallSite] = {}
        # O2 前半：已解析的 HTTP 路由（entry_points.py 产物，code_index.json 落盘）
        # 按 func_block_id join 给 builder，让 GN 轨漏洞带 "METHOD /path"（PoC
        # 模板层直接命中，免 gap-fill LLM）。join miss → builder 保持原样兜底。
        entry_point_map: dict[str, EntryPoint] = {}
        code_index_path = resolve_intermediate(deliverables, "code_index.json")  # tiering 双路径
        if code_index_path is not None:
            try:
                index = CodeIndex.model_validate_json(code_index_path.read_text())
                sink_call_sites = {s.id: s for s in index.sink_call_sites}
                entry_point_map = {
                    ep.func_block_id: ep for ep in index.entry_points if ep.route
                }
            except Exception as exc:
                logger.warning("gitnexus chain-verdict: code_index.json parse failed (%s)", exc)

        async with get_audit_session().track_step(
            "vulnerability-analysis", "gitnexus-chain-verdict",
            intent=None,
        ):
            llm = _make_verdict_llm_client(str(repo), provider_config=input.provider_config)   # P3c 阶段 1
            _chain_cb = _make_gitnexus_progress_cb(get_audit_session())

            # Second-order storage-taint findings (子项⑤): compute once, group
            # by vuln class, then merge into the per-class queue inside the
            # builder loop. Guarded on ``index`` being defined (only set when
            # code_index.json parsed successfully above) — absent/invalid
            # code_index.json ⇒ no second-order findings, no crash.
            second_order_by_vc: dict[str, list] = {}
            try:
                storage_writes = list(index.storage_write_points)
                reads_by_id = {s.param_name: s for s in index.source_points
                               if s.source_type.value == "storage"}
                if storage_writes and reads_by_id:
                    from supernova_core.code_index.vuln_chain_builders.second_order_builder import (
                        build_second_order_findings,
                    )

                    def _second_order_source_provider(w):
                        # Lazy-load a write point's file source for table-name
                        # resolution (@Table / naming convention, Tasks 2-4).
                        # Missing/unreadable file → None → join degrades
                        # conservatively (under-recall, never a crash).
                        try:
                            return (repo / w.file_path).read_bytes()
                        except (FileNotFoundError, OSError, IsADirectoryError):
                            return None

                    second_order = await build_second_order_findings(
                        storage_writes, pgraph, llm_client=llm,
                        sink_call_sites=sink_call_sites, reads_by_id=reads_by_id,
                        source_provider=_second_order_source_provider,
                        progress_cb=_chain_cb,
                    )
                    for f in second_order:
                        vc2 = f.vulnerability_type.replace("second_order_", "")
                        second_order_by_vc.setdefault(vc2, []).append(f)
            except NameError:
                # index undefined — code_index.json absent/failed to parse.
                pass
            except Exception as exc:
                logger.warning(
                    "gitnexus chain-verdict: second-order builder failed (%s)", exc)

            # P3 (spec 2026-08-21 safe-branch-recall): presumed-safe 来源候选
            # (chain_propagator 对 intra 否定 sink 的表达式兜底,notes='presumed-safe')
            # 判 vulnerable → 只进 chain_verdicts.json(数据流视图可见该枝终审),
            # 不进 exploitation queue —— 防确定性兜底假阳污染报告。intra 报的
            # 候选(无此 notes)判 vulnerable 是真阳,照常进 queue(现状不变)。
            presumed_safe_flow_ids = {
                f.flow_id for f in pgraph.taint_flows
                if getattr(f, "notes", "") == "presumed-safe"
            }

            for vc, builder in (
                ("injection", build_injection_findings),
                ("xss", build_xss_findings),
                ("ssrf", build_ssrf_findings),
            ):
                try:
                    findings = await builder(pgraph, llm_client=llm,
                                             sink_call_sites=sink_call_sites,
                                             entry_points=entry_point_map,
                                             progress_cb=_chain_cb)
                except Exception as exc:
                    # one vuln class failing must not block the others
                    failed_classes.append(vc)
                    fail_reasons[vc] = f"builder raised: {exc}"
                    logger.warning("gitnexus chain-verdict %s failed: %s", vc, exc)
                    continue
                # Merge second-order findings into this vc's queue so they
                # get written + counted alongside the single-hop ones.
                findings = list(findings or []) + second_order_by_vc.get(vc, [])
                # P3 分流：presumed-safe 来源判 vulnerable 的条目出 queue
                # (chain_verdicts 落盘仍用全量 findings,见下方 _dump_chain_verdicts)。
                queue_findings = [
                    f for f in findings
                    if not (getattr(f, "flow_id", "") in presumed_safe_flow_ids
                            and getattr(f, "verdict", "") == "vulnerable")
                ]
                if len(queue_findings) < len(findings):
                    logger.info(
                        "gitnexus chain-verdict %s: %d presumed-safe vulnerable "
                        "finding(s) routed to chain_verdicts only (not queue)",
                        vc, len(findings) - len(queue_findings),
                    )
                if queue_findings:
                    # tiering：*_gitnexus_queue.json 属中间产物 → 桶内 intermediate/
                    atomic_write_json(
                        intermediate_path(deliverables, f"{vc}_gitnexus_queue.json"),
                        {"vulnerabilities": [f.model_dump() for f in queue_findings]},
                    )
                    per_class[vc] = len(queue_findings)
                # 数据流视图（spec 2026-08-20 §4 P1）：落 chain_verdicts
                # 产物（safe 链也进），供 P4 组装器按 flow_id 拼 GitNexus 枝。
                # 与 gitnexus_queue 同条件（有 findings 才落盘），零 finding
                # 不产空文件。
                _dump_chain_verdicts(deliverables, vc, findings or [])

            taint_flows_count = len(pgraph.taint_flows)
            sink_call_sites_count = len(sink_call_sites)
            try:
                _sess = get_audit_session()
                if not per_class:  # 3 类全 0 findings
                    await _sess.log_info(
                        f"GitNexus 注入轨：3 类 0 findings（taint_flows={taint_flows_count}，"
                        f"sink_call_sites={sink_call_sites_count}）— 合法结论"
                        f"（流程跑通，本类无 taint）。下游按 fail-fast 策略编排。",
                        "info",
                    )
                else:
                    await _sess.log_info(
                        f"GitNexus 注入轨：inj={per_class.get('injection', 0)}, "
                        f"xss={per_class.get('xss', 0)}, ssrf={per_class.get('ssrf', 0)} "
                        f"findings（taint_flows={taint_flows_count}）。",
                        "info",
                    )
            except Exception:
                pass

        return {
            "per_class": per_class,
            "failed_classes": failed_classes,
            "fail_reasons": fail_reasons,
        }
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_framework_analysis(input: ActivityInput) -> dict:
    """Detect auto-REST frameworks, infer endpoints, write deliverable."""
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    try:
        from supernova_core.services.framework_analyzer import analyze_frameworks

        repo, deliverables, _ = _get_paths(input)
        async with get_audit_session().track_step("pre-recon", "framework-analysis", intent=intent_for("framework-analysis")):
            result = await analyze_frameworks(str(repo))

            # Write result as JSON deliverable
            import dataclasses
            result_data = dataclasses.asdict(result)
            result_path = intermediate_path(deliverables, "framework_analysis.json")  # tiering
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
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    try:
        from supernova_core.services.frontend_mapper import map_frontend_routes

        repo, deliverables, _ = _get_paths(input)
        async with get_audit_session().track_step("pre-recon", "frontend-mapping", intent=intent_for("frontend-mapping")):
            result = await map_frontend_routes(str(repo))

            # Write result as JSON deliverable
            import dataclasses
            result_data = dataclasses.asdict(result)
            result_path = intermediate_path(deliverables, "frontend_mapping.json")  # tiering
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
async def run_route_chain_building(input: ActivityInput) -> dict:
    """Build route chain map from framework + frontend analysis results."""
    from supernova_whitebox.audit.session_registry import get_audit_session
    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    try:
        from supernova_core.services.framework_analyzer import FrameworkAnalysisResult
        from supernova_core.services.frontend_mapper import FrontendAnalysisResult, XssAttackChain, FrontendRoute
        from supernova_core.services.route_chain_builder import build_attack_chains_from_analysis
        import dataclasses
        import logging

        repo, deliverables, _ = _get_paths(input)
        log = logging.getLogger(__name__)

        async with get_audit_session().track_step("pre-recon", "route-chain-building", intent=intent_for("route-chain-building")):
            # Load framework analysis result
            framework_result = FrameworkAnalysisResult()
            framework_path = resolve_intermediate(deliverables, "framework_analysis.json")  # tiering 双路径
            if framework_path is not None:
                data = json.loads(framework_path.read_text())
                endpoints = [_to_endpoint(ep) for ep in data.get("inferred_endpoints", []) if isinstance(ep, dict)]
                framework_result = FrameworkAnalysisResult(
                    inferred_endpoints=endpoints,
                    recommendations=data.get("recommendations", []),
                )

            # Load frontend mapping result
            frontend_result = FrontendAnalysisResult()
            frontend_path = resolve_intermediate(deliverables, "frontend_mapping.json")  # tiering 双路径
            if frontend_path is not None:
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
            chains_path = intermediate_path(deliverables, "route_chains.json")  # tiering
            atomic_write_json(chains_path, chains_data)

        return {"chain_count": len(chains)}
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e


@activity.defn
async def run_attack_chain_llm_agent(input: ActivityInput) -> dict:
    """LLM-track attack chain agent (creative-driven, multi-step inference).

    Runs the ATTACK_CHAIN agent (attack-chain.txt prompt) via the shared executor.
    Reads recon + exploitation_queue (LLM-track self-produced) — NEVER GitNexus
    deterministic artifacts (CLAUDE.md §1). The prompt instructs the agent to
    Write attack_chains_llm_queue.json itself; we do NOT pass structured_output
    schema (consistent with report agent — see _vuln_output_schema returning None).
    """
    try:
        act_input = ActivityInput(
            **{**input.__dict__, "agent_name": AgentName.ATTACK_CHAIN.value}
        )
        result = await run_agent(act_input)
        # run_agent 返回 metrics.model_dump() (dict)，字段名 num_turns。
        # chain_count 这里仅是 activity-level metadata（真实 chain 数由
        # assembly_v2 读 attack_chains_llm_queue.json 计数返回）。
        return {"chain_count": result.get("num_turns", 0), "track": "llm"}
    except (PentestError, Exception) as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(
            str(e), type=error_type, non_retryable=not retryable
        ) from e


@activity.defn
async def run_attack_chain_assembly_v2(input: ActivityInput) -> dict:
    """GitNexus-track assembly + dual-track merge → attack_chains.json.

    1. Read {vt}_gitnexus_queue.json findings (GitNexus own output).
    2. assemble_attack_chains (deterministic cross-endpoint correlation).
    3. Read attack_chains_llm_queue.json (from run_attack_chain_llm_agent).
    4. merge_attack_chains → attack_chains.json.

    GitNexus unavailable → gitnexus_chains=[] (graceful), LLM track covers.
    CLAUDE.md §1: 不反向喂 LLM 轨 prompt — assembly 只读两轨各自产物做合并。
    """
    try:
        repo, deliverables, _ = _get_paths(input)
        log = logging.getLogger(__name__)

        # 1. GitNexus findings per class
        gn_by_class: dict[str, list] = {}
        for vt in ("injection", "xss", "ssrf", "authz"):
            qpath = resolve_intermediate(deliverables, f"{vt}_gitnexus_queue.json")
            if qpath is not None:
                try:
                    data = json.loads(qpath.read_text("utf-8"))
                    gn_by_class[vt] = data.get("vulnerabilities", []) or []
                except (json.JSONDecodeError, OSError):
                    gn_by_class[vt] = []

        # 2. Assemble GitNexus chains
        from supernova_core.code_index.attack_chain_assembler import assemble_attack_chains
        gn_chains = assemble_attack_chains(gn_by_class, log)
        gn_path = intermediate_path(deliverables, "attack_chains_gitnexus_queue.json")  # tiering
        atomic_write_json(gn_path, {"chains": gn_chains})

        # 3. LLM chains（attack-chain agent Write 落盘）
        llm_chains: list = []
        llm_path = resolve_intermediate(deliverables, "attack_chains_llm_queue.json")  # tiering 双路径(agent self-Write 落顶层)
        if llm_path is not None:
            try:
                llm_chains = (
                    json.loads(llm_path.read_text("utf-8")).get("chains", []) or []
                )
            except (json.JSONDecodeError, OSError):
                llm_chains = []

        # 4. Merge → attack_chains.json
        from supernova_core.code_index.dual_track_merger import merge_attack_chains
        merged = merge_attack_chains(llm_chains, gn_chains)
        atomic_write_json(intermediate_path(deliverables, "attack_chains.json"), {"chains": merged})

        return {
            "chain_count": len(merged),
            "llm_count": len(llm_chains),
            "gitnexus_count": len(gn_chains),
        }
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(
            str(e), type=error_type, non_retryable=not retryable
        ) from e


# ── C1 worker-container-path 迁移 activity ──────────────────────────────
# 这 3 个 activity 仅 worker 容器路径调用（Task 4 workflow 接入）；CLI run_scan 不调用
# （CLI 自行 inline AuditSession/HeartbeatManager，零改动）。setup_display 注入进程全局
# AuditSession 使后续 activity 的 get_audit_session() 可用；run_heartbeat 长驻写 heartbeat
# 供 web 判活；finalize_summary 写 scan_end 事件 + 清理全局 session。

@activity.defn
async def setup_display(input: ActivityInput) -> None:
    """C1 前导 activity: 构造 headless AuditSession(event_file 来自 input) + set_audit_session。

    worker 容器无 TTY，用 use_rich=False + Console()（自动检测非 TTY → 纯文本）。
    AuditSession 构造逻辑复用 run_with_display(display_lifecycle.py) 的 headless 分支：
    构造 AuditSession + initialize(workflow_id, event_file=input.event_file)。
    event_file 透传到 WorkflowLogger.initialize → 挂 StructuredEventRenderer 写 events.ndjson。

    SessionMetadata.output_path 取 workspace_path 的父目录（workspaces dir），id 取
    workspace_name，使 generate_audit_path = workspaces/<session> = workspace_path（与 CLI
    run_scan 的 meta 语义一致：audit 产物落 session 目录下）。

    构造逻辑抽到 core ``build_headless_audit_session``，与 ``ensure_audit_session``（worker
    重启后可观测恢复）共用--见 session_recovery.py。
    """
    await build_headless_audit_session(input)
    from supernova_core.config.scan_env import set_scan_env
    set_scan_env(input.env_overrides)


@activity.defn
async def finalize_summary(input: ActivityInput, summary: dict) -> None:
    """C1 后置 activity: log_workflow_complete(触发 StructuredEventRenderer 写 scan_end) + 清 AuditSession。

    summary 由 workflow 从 self._state 构建（等价 run_scan worker.py:312-328 的逻辑，
    移进 workflow）。log_workflow_complete 内部调 workflow_logger.close()（关 LogStream +
    StructuredEventRenderer 句柄），故无需额外 close。clear_audit_session 清进程全局。
    """
    from supernova_whitebox.audit.session_registry import (
        NullAuditSession, clear_audit_session, get_audit_session,
    )

    await ensure_audit_session(input)  # worker 重启后可观测恢复(幂等;见 session_recovery.py)
    session = get_audit_session()
    if not isinstance(session, NullAuditSession):
        # cost / cost_currency 取 session.get_metrics()(MetricsTracker 累积所有 agent,完整),
        # 非 summary dict(其 total_cost 来自 workflow self._state.agent_metrics,LLM 轨关时
        # 残缺→0)。对齐 CLI 路径 worker._build_final_summary 同源修复:两条路径 cost 数据源
        # 一致 = MetricsTracker(回归 NodeGoat ``Total Cost: $0.0000``)。
        final_metrics = await session.get_metrics() or {}
        ws = WorkflowSummary(
            status=summary.get("status", "failed"),
            total_duration_ms=summary.get("total_duration_ms", 0),
            total_cost_usd=final_metrics.get("total_cost_usd") or 0.0,
            cost_currency=final_metrics.get("cost_currency") or "USD",
            completed_agents=summary.get("completed_agents", []),
            agent_metrics=summary.get("agent_metrics", {}),
            error=summary.get("error"),
        )
        # 统一日志总线：final flush（dispatch 余下 LogEvent 到 workflow.log/events.ndjson）
        # 在 log_workflow_complete 关闭 workflow_logger 之前，对齐 display_lifecycle
        # finally 顺序（drain_and_detach → session.close）。
        await LogBus.drain_and_detach()
        if input.combined:
            # D1：组合扫描白盒阶段——调现有 log_phase_complete（写 PhaseEvent，非 scan_end），
            # 不写终态 status，留 scan 非终态供编排器在同目录追加黑盒阶段。
            await session.log_phase_complete("whitebox")
            # drain-task 收尾（2026-08-18 NodeGoat 真机回归）：黑盒阶段走自己的
            # setup_display 自建 session，白盒 session 在 combined finalize 后无人复用。
            # 不 close 的话 clear_audit_session() 紧接着摘走 _SESSIONS 最后引用 →
            # dispatcher drain task 成孤儿（纯 PENDING 挂 queue.get()）→ 下个 scan
            # 期间被 GC 销毁，"Task was destroyed but it is pending!" 经 LogBus 误路由
            # 进当时活跃 scan 的 live 页。close 会先 join 队列（phase 事件不丢）再
            # cancel+await drain task。
            await session.close()
        else:
            await session.log_workflow_complete(ws)
    await stop_heartbeat()  # 停 heartbeat daemon(启动于 setup_display); 终态自停兜底
    clear_audit_session()
    from supernova_core.config.scan_env import clear_scan_env
    clear_scan_env()


@activity.defn
async def cleanup_auth_state_activity(workspace_path: str) -> None:
    """finally 收尾: 清理 auth-state*.json（glob 文件 I/O，sandbox 禁 workflow 直调）。

    workflow finally 原裸调 cleanup_auth_state_sync（glob.glob）→ 抛
    RestrictedWorkflowAccessError → WorkflowTask 反复 TimedOut → scan failed（与
    blackbox 同根因：带 auth config 的扫描登录后生成 auth-state.json，finally 走 cleanup 触发）。
    挪进 activity（worker 进程，不受 workflow sandbox 限制）。best-effort，失败由 workflow
    侧 try/except 吞掉不阻断收尾。对齐 blackbox cleanup_auth_state_activity。
    """
    from supernova_core.services.validate_authentication import cleanup_auth_state

    if not workspace_path:
        return
    try:
        await cleanup_auth_state(workspace_path)
    except Exception:  # noqa: BLE001 - best-effort cleanup
        pass
