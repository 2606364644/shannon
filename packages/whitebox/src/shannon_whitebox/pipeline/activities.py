import json
import logging
import time
import os
from datetime import timedelta
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError as ApplicationFailure

from shannon_core.models.agents import AgentName, AGENTS, ALL_VULN_CLASSES, VulnType
from shannon_core.models.errors import ErrorCode, PentestError, classify_error_for_temporal
from shannon_core.models.metrics import AgentMetrics
from shannon_core.models.retry import agent_retry_category, retry_for
from shannon_core.utils.atomic_write import atomic_write_json
from shannon_core.utils.paths import resolve_deliverables_path
from shannon_core.utils.credential_validator import validate_credentials
from shannon_core.logging import create_activity_logger
from shannon_core.agents.executor import AgentExecutor
from shannon_core.agents.runner import run_claude_prompt
from shannon_core.agents.recon_context_summarizer import summarize_recon_context
from shannon_core.config.concurrency import is_gitnexus_llm_enabled
from shannon_core.prompts.manager import PromptManager
from shannon_core.session import SessionManager
from shannon_whitebox.audit.session import AuditSession

from .shared import ActivityInput
from .step_intents import intent_for

logger = logging.getLogger(__name__)


def _get_paths(input: ActivityInput) -> tuple[Path, Path, Path]:
    from shannon_core.utils.paths import WHITEBOX_SUBDIR

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


def _vuln_max_turns(agent_name: str) -> int | None:
    """vuln agent 用专用 max_turns(SHANNON_VULN_MAX_TURNS,默认 500);其他返回 None。

    返回 None 时,executor → run_claude_prompt → provider 沿用各引擎全局 env 默认
    (CLAUDE_MAX_TURNS / SHANNON_OPENAI_MAX_TURNS = 200),行为零变更。
    B2: 仅 vuln 单独配,不污染 pre-recon/recon/report。
    """
    if agent_retry_category(agent_name) == "vuln":
        return int(os.getenv("SHANNON_VULN_MAX_TURNS", "500"))
    return None


def _vuln_output_schema(agent_name: AgentName) -> dict | None:
    """对齐原始 TS getOutputFormat:vuln agent(*-vuln)的结构化输出 schema。

    原始 shannon 的 exploitation queue 由 agent 的 final structured output 捕获
    (agent-execution.ts:222 把 result.structuredOutput 写盘)。PY executor.py:132-135
    移植了同一落盘分支,但 run_agent 一直没传 schema → result.structured_output 恒为
    None → ``{vt}_exploitation_queue.json`` 永不落盘,黑盒 preflight 永远报 "No
    whitebox results found"。本 helper 补上这根线。

    顶层 ``{vulnerabilities: [...]}``;item 仅约束基线必填字段
    (ID/vulnerability_type/externally_exploitable/confidence),类特定字段不约束
    (agent 自由填,VulnerabilityQueue.parse_lenient 容错解析)。宽松基线 schema 比 TS
    的逐类 Zod schema 兼容性风险更低;真机验证 OK 后可升级为 pydantic 具体生成。

    仅 ``*-vuln`` 返回 schema(对齐 TS VULN_AGENT_QUEUE_FILENAMES 只映射 *-vuln,
    排除 *-exploit,避免 exploit agent 的 structured_output 覆写 vuln queue)。
    """
    if not agent_name.value.endswith("-vuln"):
        return None
    return {
        "type": "object",
        "properties": {
            "vulnerabilities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "ID",
                        "vulnerability_type",
                        "externally_exploitable",
                        "confidence",
                    ],
                    "additionalProperties": True,
                },
            },
        },
        "required": ["vulnerabilities"],
    }


@activity.defn
async def run_agent(input: ActivityInput) -> dict:
    from shannon_whitebox.audit.session_registry import get_audit_session
    from shannon_whitebox.audit.session_tool_audit_logger import SessionToolAuditLogger
    from shannon_core.models.audit import AgentEndResult

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
        )
        await tool_audit_logger.close(success=True, duration_ms=metrics.duration_ms)
        await session.end_agent(agent_name.value, AgentEndResult(
            success=True,
            duration_ms=metrics.duration_ms,
            cost_usd=metrics.cost_usd or 0.0,
            attempt_number=attempt,
            model=metrics.model,
            num_turns=metrics.num_turns,
        ))
        return metrics.model_dump()
    except PentestError as e:
        await tool_audit_logger.close(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000))
        await session.end_agent(agent_name.value, AgentEndResult(
            success=False, duration_ms=int((time.monotonic() - agent_start) * 1000), cost_usd=0.0,
            attempt_number=attempt, error=str(e)))
        await session.log_error(
            e, context=agent_name.value, attempt=attempt, max_attempts=max_attempts)
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
        await session.log_error(
            e, context=agent_name.value, attempt=attempt, max_attempts=max_attempts)
        error_type, retryable = classify_error_for_temporal(e)
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


def _make_recon_summary_llm_client(repo_path: str):
    """LLM client for summarize_recon_context.

    Always attempts an LLM call (not gated by GitNexus-LLM toggle, since the
    summarizer belongs to the LLM track, not GitNexus). When the LLM provider
    itself is unavailable, run_claude_prompt raises and the summarizer degrades
    gracefully to raw §4/§8 extraction (non-fatal).
    """
    async def _client(prompt: str, **kwargs) -> str:
        result = await run_claude_prompt(
            prompt=prompt, repo_path=repo_path, model_tier="medium",
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
    llm_client = _make_recon_summary_llm_client(str(repo))
    recon_context = await summarize_recon_context(recon_md, llm_client)
    base["RECON_CONTEXT"] = recon_context

    # FRAMEWORK_ANALYSIS: conditional — only when inferred_endpoints non-empty
    fw_path = deliverables / "framework_analysis.json"
    fw_lines: list[str] = []
    if fw_path.exists():
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
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        await get_audit_session().log_info(input.info_message, input.info_level)
    except Exception:
        pass  # best-effort: 显示侧通道失败绝不影响扫描（尤其 except 块里调，避免替换原异常）


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
            md, dom_cands, fw_cands, http_route_count, entry_point_total = build_authz_gitnexus_track(str(deliverables))
            candidate_count = len(dom_cands) + len(fw_cands)

            # 可观测性（spec §3.2）：GitNexus 轨候选状态经 InfoEvent 通道，避免静默空转。
            # best-effort：显示通道失败绝不影响扫描（对齐 log_info_activity 防御）。
            try:
                _session = get_audit_session()
                if candidate_count == 0:
                    await _session.log_info(
                        f"authz GitNexus 轨：0 候选（dominance={len(dom_cands)}, "
                        f"framework={len(fw_cands)}；http_route 入口点="
                        f"{http_route_count}/{entry_point_total}）→ 跳过 LLM 判定，"
                        f"authz 全靠 LLM 轨兜底。http_route=0 常因 code_index 入口点未识别"
                        f"（语言误判/调用图未就绪/纯静态页）。",
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
                    data["needs_review"] = True  # 探索发现，软候选（未经确定性 dominance 验证）
                    if not data.get("evidence_chain"):
                        data["evidence_chain"] = "gitnexus explore-discovered (0 deterministic candidates)"
                    vulnerabilities.append(data)

                try:
                    await get_audit_session().log_info(
                        f"authz GitNexus 轨（探索）：0 确定性候选 → 自主探索产出 "
                        f"{len(vulnerabilities)} 条软候选（needs_review=True）。",
                        "info",
                    )
                except Exception:
                    pass

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
async def run_auth_gitnexus_judge(input: ActivityInput) -> dict:
    """auth GitNexus 轨：候选多轮深度判定 + 追加 auth_gitnexus_queue.json。

    对标 run_authz_gitnexus_judge（spec-1a），差异在 queue 写策略：
    - authz：OVERWRITE（atomic_write_json 直接覆盖）。
    - auth ：APPEND——run_auth_config_scan 先产 config 类（cookie/HSTS/CORS/JWT/限流）
             条目，本 activity 产逻辑类（session 固定/明文密码/JWT 未验签/OAuth state 缺失/
             弱随机 token）verdict，读现有 + 合并，非覆盖。

    1. build_auth_gitnexus_track 读 code_index.json → 三信号识别 auth handler → 跑检查器
       → 产 0-N AuthCandidate + markdown 表格。
    2. candidate_count>0 → 多轮 verdict_agent（run_gitnexus_verdict_agent）判定；保守，
       不确定判 vulnerable。candidate_count==0 → 触发自主探索（多轮 agent 读 auth 源码，T6）。
    3. parse_lenient 容错解析；verdict 标 source_track="gitnexus"。
    4. 读现有 auth_gitnexus_queue.json（若存在）+ 追加逻辑类 verdict → atomic_write_json。
    """
    from shannon_whitebox.audit.session_registry import get_audit_session
    from shannon_core.code_index.auth_gitnexus_track import build_auth_gitnexus_track
    from shannon_core.models.queue_schemas import VulnerabilityQueue

    try:
        async with get_audit_session().track_step(
            "vulnerability-analysis", "auth-gitnexus-judge",
            intent=intent_for("auth-gitnexus-judge"),
        ):
            repo, deliverables, _ = _get_paths(input)
            md, candidates, handler_count, entry_point_total = build_auth_gitnexus_track(
                str(deliverables)
            )
            candidate_count = len(candidates)

            # 可观测性：候选状态经 InfoEvent（避免静默空转），best-effort 不影响扫描。
            try:
                _session = get_audit_session()
                if candidate_count == 0:
                    await _session.log_info(
                        f"auth GitNexus 轨：0 候选（handler={handler_count}, "
                        f"entry_point={entry_point_total}）→ 触发自主探索（多轮 agent 读 auth 源码）。"
                        f"handler=0 常因入口点未识别（语言误判/调用图未就绪）。",
                        "warning",
                    )
                else:
                    await _session.log_info(
                        f"auth GitNexus 轨：{candidate_count} 候选 → 调多轮 verdict_agent 判定。",
                        "info",
                    )
            except Exception:
                pass

            vulnerabilities: list[dict] = []
            prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
            prompt_manager = PromptManager(prompts_dir)
            if candidate_count > 0:
                prompt = prompt_manager.load_sync(
                    "auth_gitnexus_judge",
                    variables={"AUTH_GITNEXUS_CANDIDATES": md},
                )
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
                        data["evidence_chain"] = "gitnexus track candidate (auth logic)"
                    vulnerabilities.append(data)

                try:
                    await get_audit_session().log_info(
                        f"auth GitNexus 轨：产出 {len(vulnerabilities)} 条 verdict。",
                        "info",
                    )
                except Exception:
                    pass
            else:  # candidate_count == 0 → spec-2b T6：多轮 agent 自主探索（非静默空 queue）
                # 确定性层 0 候选（常见于 auth handler 漏召回/入口点未识别）时，agent
                # 自主 grep route + read auth handler 补软候选（needs_review=True，
                # evidence_chain 标 explore-discovered）。对标 authz 探索段 :375-419。
                try:
                    await get_audit_session().log_info(
                        "auth GitNexus 轨：0 候选 → 触发自主探索（多轮 agent 读 auth 源码）。",
                        "warning",
                    )
                except Exception:
                    pass
                explore_prompt = prompt_manager.load_sync(
                    "auth_gitnexus_explore",
                    variables={
                        "ENTRY_POINTS_SUMMARY": f"{entry_point_total} entry points (handler_count={handler_count})",
                    },
                )
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
                    data["needs_review"] = True  # 探索发现，软候选（未经确定性检查器验证）
                    if not data.get("evidence_chain"):
                        data["evidence_chain"] = "gitnexus explore-discovered (0 deterministic candidates)"
                    vulnerabilities.append(data)

                try:
                    await get_audit_session().log_info(
                        f"auth GitNexus 轨（探索）：0 确定性候选 → 自主探索产出 "
                        f"{len(vulnerabilities)} 条软候选（needs_review=True）。",
                        "info",
                    )
                except Exception:
                    pass

            # 追加 auth_gitnexus_queue.json（config_scan 先产；读现有 + 合并，非覆盖）。
            # 写操作放在 track_step 块内（对标 authz activities.py:421-430），归因到 step span
            # 且受 span 的错误边界覆盖；atomic_write_json 保证原子性，本 activity 唯一写点。
            queue_path = deliverables / "auth_gitnexus_queue.json"
            existing: list[dict] = []
            if queue_path.exists():
                try:
                    existing = json.loads(queue_path.read_text()).get("vulnerabilities", [])
                except Exception:
                    existing = []
            atomic_write_json(queue_path, {"vulnerabilities": existing + vulnerabilities})

            return {
                "candidate_count": candidate_count,
                "verdict_count": len(vulnerabilities),
                "handler_count": handler_count,
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
            # Create LLM client for taint analysis (+ LLM sink discovery)
            def _make_gitnexus_llm_client(repo_path: str):
                """封装 run_claude_prompt 成 analyze_taint_llm/discover 期望的
                async (prompt)->str 契约。env 关时返回 None → consumer 入口各自
                静默降级(discover_sinks/sources 早退返回空, analyze_taint_llm 走
                deterministic fallback), 不再跑 N 个无用的 raise-task 刷屏(2026-07-01)。"""
                if not is_gitnexus_llm_enabled():
                    return None

                async def _client(prompt: str, **kwargs) -> str:
                    result = await run_claude_prompt(
                        prompt=prompt, repo_path=repo_path, model_tier="medium",
                    )
                    return result.text  # ClaudeRunResult.text (runner.py:77) = 纯文本输出
                return _client

            _llm_taint_client = _make_gitnexus_llm_client(str(repo))

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
                        progress_cb=_make_gitnexus_progress_cb(get_audit_session()),
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
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.code_index import run_entry_point_fusion as _fusion

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
                gitnexus_path = deliverables / f"{vuln_class}_gitnexus_queue.json"

                # GitNexus-track findings (may exist independently of LLM track)
                gitnexus_findings = []
                if gitnexus_path.exists():
                    gitnexus_parsed = VulnerabilityQueue.parse_lenient(
                        gitnexus_path.read_text(encoding="utf-8")
                    )
                    gitnexus_findings = gitnexus_parsed.queue.vulnerabilities

                # LLM-track findings. A4: LLM queue absent -> empty list, still merge
                # (GitNexus-only must reach the report, not be dropped). Skip only
                # when BOTH tracks are empty.
                llm_findings = []
                llm_warnings = []
                if exploitation_path.exists():
                    llm_path = deliverables / f"{vuln_class}_llm_queue.json"
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
                    "warnings": llm_warnings,
                }
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
async def generate_poc_report(input: ActivityInput) -> None:
    """报告增强：生成 curl/Burp PoC md。失败不阻塞主报告（吞异常）。"""
    import logging
    log = logging.getLogger(__name__)
    try:
        from shannon_core.services.poc_generator import PoCGenerator
        from shannon_core.models.config import ALL_VULN_CLASSES

        _, deliverables, _ = _get_paths(input)
        await PoCGenerator.generate(
            deliverables_dir=deliverables,
            vuln_classes=input.vuln_classes or list(ALL_VULN_CLASSES),
            target_url=(input.web_url or None),
            track="whitebox",
            repo_path=input.repo_path,
            api_key=input.api_key,
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


def _make_verdict_llm_client(repo_path: str):
    """接通后: 真 client; env 关时返回 raise-client(降级)。"""
    if not is_gitnexus_llm_enabled():
        return _gitnexus_verdict_llm_client  # 模块级 raise 兜底
    from shannon_core.agents.runner import run_claude_prompt

    async def _client(prompt: str, **kwargs) -> str:
        result = await run_claude_prompt(
            prompt=prompt, repo_path=repo_path, model_tier="medium",
        )
        return result.text
    return _client


async def run_gitnexus_verdict_agent(
    *,
    prompt: str,
    repo_path: str,
    structured_output_schema: dict | None = None,
    audit_session: "AuditSession | None" = None,
) -> "ClaudeRunResult":
    """GitNexus 多轮 verdict agent：带 grep/read 自主追链，吃确定性候选做深度判定。

    max_turns 走 SHANNON_GITNEXUS_VERDICT_MAX_TURNS（默认 30）。返回完整 ClaudeRunResult
    （含 turns/cost/structured_output），不截断为 str——区别于 _make_verdict_llm_client 的单次薄包装。

    audit_session 非 None 时构造 SessionToolAuditLogger（对齐 run_agent :167/183/198），多轮
    grep/read 工具调用经逐轮审计；为 None 时 tool_audit_logger=None（行为同前，向后兼容）。

    供 spec-1 的 run_authz_gitnexus_judge 多轮判定用。单测 mock run_claude_prompt 验证
    max_turns 透传 / audit_session 注入 tool_audit_logger。
    """
    from shannon_core.agents.runner import run_claude_prompt  # 延迟 import，对齐 :859
    tool_audit_logger = None
    if audit_session is not None:
        from shannon_whitebox.audit.session_tool_audit_logger import (
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
            max_turns=int(os.getenv("SHANNON_GITNEXUS_VERDICT_MAX_TURNS", "30")),
            structured_output_schema=structured_output_schema,
            tool_audit_logger=tool_audit_logger,
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
            try:
                await get_audit_session().log_info(
                    "GitNexus 注入轨：parameter_graph.json 缺失 → 跳过 3 类判定，靠 LLM 轨兜底。",
                    "warning",
                )
            except Exception:
                pass
            return {"per_class": {}, "skipped": "no parameter_graph.json"}
        try:
            pgraph = ParameterPropagationGraph.model_validate_json(pgraph_path.read_text())
        except Exception:
            try:
                await get_audit_session().log_info(
                    "GitNexus 注入轨：parameter_graph.json 无效 → 跳过 3 类判定，靠 LLM 轨兜底。",
                    "warning",
                )
            except Exception:
                pass
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
            llm = _make_verdict_llm_client(str(repo))
            _chain_cb = _make_gitnexus_progress_cb(get_audit_session())

            for vc, builder in (
                ("injection", build_injection_findings),
                ("xss", build_xss_findings),
                ("ssrf", build_ssrf_findings),
            ):
                try:
                    findings = await builder(pgraph, llm_client=llm,
                                             sink_call_sites=sink_call_sites,
                                             progress_cb=_chain_cb)
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

            taint_flows_count = len(pgraph.taint_flows)
            sink_call_sites_count = len(sink_call_sites)
            try:
                _sess = get_audit_session()
                if not per_class:  # 3 类全 0 findings
                    await _sess.log_info(
                        f"GitNexus 注入轨：3 类 0 findings（taint_flows={taint_flows_count}，"
                        f"sink_call_sites={sink_call_sites_count}）→ 靠 LLM 轨兜底。"
                        f"常因 parameter_graph 空壳"
                        f"（GitNexus 调用图未产出 taint / Plan 1 未落地）。",
                        "warning",
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
            qpath = deliverables / f"{vt}_gitnexus_queue.json"
            if qpath.exists():
                try:
                    data = json.loads(qpath.read_text("utf-8"))
                    gn_by_class[vt] = data.get("vulnerabilities", []) or []
                except (json.JSONDecodeError, OSError):
                    gn_by_class[vt] = []

        # 2. Assemble GitNexus chains
        from shannon_core.code_index.attack_chain_assembler import assemble_attack_chains
        gn_chains = assemble_attack_chains(gn_by_class, log)
        gn_path = deliverables / "attack_chains_gitnexus_queue.json"
        atomic_write_json(gn_path, {"chains": gn_chains})

        # 3. LLM chains（attack-chain agent Write 落盘）
        llm_chains: list = []
        llm_path = deliverables / "attack_chains_llm_queue.json"
        if llm_path.exists():
            try:
                llm_chains = (
                    json.loads(llm_path.read_text("utf-8")).get("chains", []) or []
                )
            except (json.JSONDecodeError, OSError):
                llm_chains = []

        # 4. Merge → attack_chains.json
        from shannon_core.code_index.dual_track_merger import merge_attack_chains
        merged = merge_attack_chains(llm_chains, gn_chains)
        atomic_write_json(deliverables / "attack_chains.json", {"chains": merged})

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
