import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from supernova_core.config.parser import distribute_config, parse_config
from supernova_core.config.scan_env import ws_getenv
from supernova_core.models.agents import AgentName, AGENTS
from supernova_core.models.config import Config
from supernova_core.models.errors import ErrorCode, PentestError
from supernova_core.models.metrics import AgentMetrics
from supernova_core.utils.atomic_write import atomic_write_json
from supernova_core.utils.billing import is_spending_cap_behavior
from supernova_core.utils.paths import intermediate_path

from supernova_core.agents.runner import UsageSink, run_claude_prompt
from supernova_core.agents.validators import get_queue_filename, validate_deliverable
from supernova_core.agents.vuln_queue_reconcile import (
    backfill_titles_from_roster,
    reconcile_findings,
)
from supernova_core.agents.progress_tool import make_progress
from supernova_core.collectors import make_collector
from supernova_core.git_manager import GitManager
from supernova_core.prompts.manager import PromptManager
from supernova_core.renderers import render_deliverable
from supernova_core.services.validate_authentication import auth_state_path

if TYPE_CHECKING:
    from supernova_core.logging.activity_logger import ActivityLogger
    from supernova_core.agents.tool_audit_logger import ToolAuditLogger


logger = logging.getLogger(__name__)


def resolve_template_name(
    agent_name: AgentName,
    prompt_override: str | None,
    default_template: str,
    web_url: str,
) -> str:
    """决定 agent 实际使用的 prompt template 名。

    - 显式 prompt_override 优先(不被覆盖)。
    - 其余情况用 AGENTS 字典里的默认 prompt_template。
    - spec 2026-08-03 白盒去动态:RECON 的 prompt_template 已固定为 recon-static
      (纯静态,只要仓库就开扫),不再按 web_url 分叉动态/静态。web_url 参数保留为
      兼容签名(逻辑层不再使用);动态 live 侦察职责移交黑盒端点验证 agent。
    """
    if prompt_override:
        return prompt_override
    return default_template


def _none_safe_add(a, b):
    """None-safe 相加：两边都 None 保持 None（provider 未提），否则 None 按 0。

    定向重查 result 并入主 result 的 cost/turns/tokens 时用（final review fix 1）。
    """
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


def _result_cost_context(result) -> dict:
    """失败路径 raise PentestError 时携带 result 的 cost/tokens，供 activities 失败
    分支记进 metrics（修 error path cost 归 0——失败 agent 也记已产生的真实 LLM 消耗）。

    成功路径走 AgentMetrics（见 execute 末尾）；失败路径 raise PentestError 原本丢弃了
    result.cost，现经 PentestError.context 桥接到 activities 的 except PentestError →
    end_agent → metrics。tokens 可能为 None（provider 异常路径未提），各 token 字段回落 None。
    """
    tokens = result.tokens
    return {
        "cost_usd": result.cost,
        "cost_currency": result.cost_currency,
        "model": result.model,
        "num_turns": result.turns,
        "input_tokens": tokens.input_tokens if tokens else None,
        "output_tokens": tokens.output_tokens if tokens else None,
        "cache_read_tokens": tokens.cache_read_input_tokens if tokens else None,
        "cache_creation_tokens": tokens.cache_creation_input_tokens if tokens else None,
    }


def _validation_error_context(result, collector_counts: dict | None = None,
                              recheck_result=None) -> dict:
    """validate_deliverable 防线 raise 时的诊断 context（spec 2026-08-19 §3.2）。

    现状该 raise 只带 agent_name/expected_queue，stop_reason / 文本证据 /
    通道状态全丢（网关断流排障只能猜）。合并 _result_cost_context 的
    cost/tokens；collector 计数 Phase 2 起由调用方从对账结果传入。
    recheck_result（2026-08-20 follow-up）：raise 路径 AgentMetrics 无法返回，
    位于 validate 之后的重查 cost 并账段不执行——定向重查的 LLM 消耗在此
    并入 context，否则重查白烧在诊断链路不可见。
    """
    ctx = _result_cost_context(result)
    text = getattr(result, "text", "") or ""
    ctx.update({
        "stop_reason": getattr(result, "stop_reason", None),
        "collected_text_len": len(text),
        "collected_text_tail": text[-200:] if text else "",
        "structured_output_present": getattr(result, "structured_output", None) is not None,
        "collector_submitted_count": (collector_counts or {}).get("submitted", 0),
        "collector_roster_count": (collector_counts or {}).get("roster", 0),
    })
    if recheck_result is not None:
        ctx["recheck_cost"] = recheck_result.cost or 0.0
        ctx["recheck_turns"] = recheck_result.turns
    return ctx


# 定向重查输出 schema（spec §3.4）：宽松基线（ID required + 自由字段）——
# 下游 parse_lenient 逐条校验；比 vuln 主 schema 宽，重查只补 ID+内容。
_RECHECK_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "vulnerabilities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ID": {"type": "string", "minLength": 1,
                           "description": "The missing finding's ID, exactly as given."},
                    "title": {"type": "string", "minLength": 1},
                    "vulnerability_type": {"type": "string"},
                    "externally_exploitable": {"type": "boolean"},
                    "confidence": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["ID", "title"],
            },
        }
    },
    "required": ["vulnerabilities"],
}

_RECHECK_MAX_TURNS = 60


async def _targeted_recheck(
    agent_name,
    repo: str,
    deliverables,
    missing: list[dict],
    model_tier: str,
    api_key: str | None,
    provider_config: dict | None,
    proxy_url: str | None,
    audit_logger=None,
    tool_audit_logger=None,
) -> tuple[list[dict], object | None]:
    """漏交条目的定向重查小 agent（spec 2026-08-19 §3.4）。

    输入只有 LLM 自身产物（主 agent 的 deliverable md）+ repo 代码 + (ID,title)
    线索——守双轨铁律。一轮封顶；返回 (补交条目, result)：条目空=无收获（降级由
    调用方 warning 记录）；result 含重查 LLM 消耗（except 失败路径为 None——该轮
    无 result 可言），调用方并入 AgentMetrics（final review fix 1：重查 cost 原被
    丢弃，在 session 成本核算完全不可见）。
    """
    vc = agent_name.value.removesuffix("-vuln")
    md_path = deliverables / f"{vc}_analysis_deliverable.md"
    missing_lines = "\n".join(
        f'- ID: {m["id"]} — title: {m["title"]}' for m in missing)
    prompt = (
        "You are a security analyst performing a TARGETED RE-SUBMISSION pass.\n"
        f"During a prior {vc} vulnerability analysis of this repository, the "
        "following confirmed findings were lost in transit before their "
        "structured submissions reached the host:\n\n"
        f"{missing_lines}\n\n"
        "A full analysis deliverable from the prior pass is available for "
        f"context at: {md_path}\n\n"
        "For each missing finding above: locate the relevant code in this "
        "repository, re-derive the finding (same ID and title), and return it "
        "in your structured output. Return ONLY the missing findings via the "
        'structured output {"vulnerabilities": [...]}; do not re-report '
        "findings outside the missing list."
    )
    try:
        result = await run_claude_prompt(
            prompt=prompt,
            repo_path=repo,
            model_tier=model_tier,
            api_key=api_key,
            structured_output_schema=_RECHECK_OUTPUT_SCHEMA,
            max_turns=_RECHECK_MAX_TURNS,
            provider_config=provider_config,
            proxy_url=proxy_url,
            # 2026-08-20 follow-up（F2）：与主 agent 同参穿线——重查此前零观测。
            audit_logger=audit_logger,
            tool_audit_logger=tool_audit_logger,
        )
    except Exception:
        logger.warning("targeted recheck agent failed for %s (degraded)",
                       agent_name.value, exc_info=True)
        return [], None
    so = getattr(result, "structured_output", None)
    items = so.get("vulnerabilities") if isinstance(so, dict) else None
    if not isinstance(items, list):
        # 无合法 structured_output 也是消耗过的重查轮——result 照返（cost 记账）。
        return [], result
    return [it for it in items if isinstance(it, dict) and it.get("ID")], result


def _dump_safe_vectors(deliverables: Path, vc: str, payload_bag: dict) -> None:
    """P3: 同步落 intermediate/{vc}_safe_vectors.json（组装器需结构化源）。

    空/缺失不落盘。门控由调用方（execute 的 collector 分支）保证。
    atomic_write_json / intermediate_path 复用模块级 import。
    """
    sv = payload_bag.get("safe_vectors")
    if not sv:
        return
    vectors = sv.get("vectors") if isinstance(sv, dict) else sv
    if not vectors:
        return
    atomic_write_json(
        intermediate_path(deliverables, f"{vc}_safe_vectors.json"),
        {"vectors": vectors},
    )


def _archive_dismissed_from_safe_vectors(deliverables: Path, vc: str,
                                         payload_bag: dict) -> None:
    """LLM 轨判非漏洞留档（spec 2026-08-27 §6）：safe_vectors（分析后确认健壮
    防御的向量）→ dismissed_findings.json（source_track=llm）。

    复用 set_safe_vectors collector 通道（§4 语义即「探索过但判非漏洞」——
    spec 原计划新造 submit_dismissed 工具，实现时确认语义已被覆盖，零 prompt /
    collector 改动，防双通道漂移）。空/缺失不写；同文件读-合并由
    append_dismissed 承担（GN 轨 chain-verdict 分流先写、LLM 轨后并）。"""
    sv = payload_bag.get("safe_vectors")
    if not sv:
        return
    vectors = sv.get("vectors") if isinstance(sv, dict) else sv
    if not vectors:
        return
    from supernova_core.services.dismissed_archive import append_dismissed
    entries = []
    for i, v in enumerate(vectors, start=1):
        if not isinstance(v, dict):
            continue
        entries.append({
            "ID": f"{vc.upper()}-LLM-SAFE-{i:02d}",
            "source_track": "llm",
            "vuln_class": vc,
            "title": v.get("subject"),
            "dismiss_reason": v.get("defense_mechanism"),
            "evidence": v.get("location"),
            "confidence": None,
            "source": v.get("subject"),
            "sink_call": None,
            "dismissed_at_stage": "llm-exploration",
        })
    if entries:
        append_dismissed(
            intermediate_path(deliverables, "dismissed_findings.json"), entries)


class AgentExecutor:
    def __init__(self, prompt_manager: PromptManager):
        self.prompt_manager = prompt_manager
        # cancel 中途已花 usage 出口（2026-08-28 authcheck 超时丢账修复）：
        # execute 开始时创建新实例下传 provider；被 cancel 时 provider 写入已累积
        # 消耗，activity 兜底从这里读。未 execute / 已正常返回时无意义。
        self.usage_sink: "UsageSink | None" = None

    async def execute(
        self,
        agent_name: AgentName,
        repo_path: str,
        web_url: str = "",
        deliverables_path: str | None = None,
        config_path: str | None = None,
        api_key: str | None = None,
        pipeline_testing: bool = False,
        prompt_variables: dict[str, str] | None = None,
        prompt_override: str | None = None,
        structured_output_schema: dict | None = None,
        audit_logger: "ActivityLogger | None" = None,
        tool_audit_logger: "ToolAuditLogger | None" = None,
        max_turns: int | None = None,
        skip_artifact_postprocess: bool = False,
        provider_config: dict | None = None,   # P3c 阶段 1：穿线下传 run_claude_prompt
        queue_root: str | None = None,   # spec 2026-08-08：读 queue 的根（黑盒=白盒 repo_path/deliverables），透传到 render_deliverable
        proxy_url: str | None = None,   # Task 4：per-scan 出口代理穿线（host_profile → CLI env / ToolContext）
        tool_policy: "ToolPolicy" = "default",
        prompt_suffix: str | None = None,   # MR 增量引导段（spec 2026-09-03 §5.2）：模板渲染后追加
    ) -> AgentMetrics:
        defn = AGENTS[agent_name]
        repo = Path(repo_path)
        if not deliverables_path:
            raise ValueError(
                "deliverables_path is required (deliverables 落 session, 不再 fallback 到 repo)"
            )
        deliverables = Path(deliverables_path)
        deliverables.mkdir(parents=True, exist_ok=True)
        await GitManager.ensure_repository(deliverables)

        config: Config | None = None
        if config_path:
            config = parse_config(config_path)
        distributed = distribute_config(config)

        variables = {
            "web_url": web_url,
            "repo_path": str(repo),
            "deliverables_path": str(deliverables),
            "scratchpad_path": str(deliverables.parent / "scratchpad"),
            # 统一注入 auth-state 路径（对齐 TS agent-execution.ts:133）。
            # workspace_path = deliverables.parent（≡ input.workspace_path，
            # 见 spec §3.3）。仅"有 auth 配置 + prompt include shared-session
            # partial"的 agent 生效；其余 manager strip block，no-op（spec §4）。
            "AUTH_STATE_FILE": str(auth_state_path(deliverables.parent)),
        }
        # Task 4：per-scan 代理 URL 注入 variables，供浏览器 session_flag（Task 5
        # manager L146）读 host_profile 代理。proxy_url=None 不写入键（backward-compat）。
        if proxy_url:
            variables["proxy_url"] = proxy_url
        if config:
            variables["browser_engine"] = config.browser_engine
        else:
            # 无 config（无认证配置的扫描）时经 ws_getenv 读 env 引擎——对齐
            # resolve_blackbox_engine 的解析口径（有 config 时 cfg.browser_engine
            # 已含 parser 的 env override；无 config 时 env 是唯一引擎来源）。
            # 否则 env 配 playwright 时 prompt 注入 fallback agent-browser，
            # 命令形态与引擎 config / cleanup 分裂（2026-09-03 收口）。
            env_engine = ws_getenv("SUPERNOVA_BROWSER_ENGINE")
            if env_engine:
                variables["browser_engine"] = env_engine.strip()
        if prompt_variables:
            variables.update(prompt_variables)
        template_name = resolve_template_name(
            agent_name, prompt_override, defn.prompt_template, web_url,
        )
        prompt = self.prompt_manager.load_sync(
            template_name,
            variables=variables,
            config=distributed,
            pipeline_testing=pipeline_testing,
        )
        if prompt_suffix:
            prompt = prompt + prompt_suffix

        await GitManager.create_checkpoint(deliverables, agent_name)

        # host-rendered deliverables:pre-recon(Plan 1)+ 5 vuln agent(Plan 3)用声明式
        # collector 接 set_* → host renderer 确定性渲染 md(对齐 TS)。其余 agent
        # make_collector 返 None(无 collector 通道,走 self-Write,不改行为)。
        collector = make_collector(agent_name)
        # progress（log_milestone 里程碑工具）：仅 validate-authentication agent 获得，
        # 驱动认证验证的进度步骤条。镜像 collector 通道，双引擎对称注入（progress_tool）。
        progress = make_progress(agent_name)

        start_time = time.monotonic()
        # 每次 execute 新建 sink（不跨次串账）；provider cancel 分支写入已花值。
        self.usage_sink = UsageSink()
        # 轨迹缓冲（spec 2026-09-03 验证缺口留痕）：包装审计 logger 无损收集
        # tool_start 原始参数，落盘 verdicts.json 时读 tool_events 做端点痕迹匹配。
        from supernova_core.agents.tool_audit_logger import BufferingToolAuditLogger

        trace_logger = BufferingToolAuditLogger(tool_audit_logger)
        result = await run_claude_prompt(
            prompt=prompt,
            repo_path=str(repo),
            model_tier=defn.model_tier,
            api_key=api_key,
            deliverables_subdir=str(deliverables.relative_to(repo)) if deliverables.is_relative_to(repo) else None,
            structured_output_schema=structured_output_schema,
            audit_logger=audit_logger,
            tool_audit_logger=trace_logger,
            max_turns=max_turns,
            collector=collector,
            progress=progress,
            provider_config=provider_config,   # P3c 阶段 1
            proxy_url=proxy_url,   # Task 4：per-scan 代理穿线到 provider
            usage_sink=self.usage_sink,   # cancel 兜底记账通道（2026-08-28）
            tool_policy=tool_policy,
        )
        duration_ms = int((time.monotonic() - start_time) * 1000)

        if result.success and is_spending_cap_behavior(result.turns, result.cost, result.text):
            await GitManager.rollback(deliverables, "spending cap detected")
            raise PentestError(
                f"Spending cap likely reached (turns={result.turns}, cost=${result.cost})",
                "billing",
                retryable=True,
                error_code=ErrorCode.SPENDING_CAP_REACHED,
                context=_result_cost_context(result),
            )

        if not result.success:
            await GitManager.rollback(deliverables, "execution failure")
            # 透传 provider 设的合法 ErrorCode（如 OUTPUT_VALIDATION_FAILED）；
            # provider 的字符串 error_code（Temporal error type，非 enum）不透传，
            # 保持 AGENT_EXECUTION_FAILED 现有行为（避免破坏 RateLimit/Timeout 分类）。
            error_code = (
                result.error_code
                if isinstance(result.error_code, ErrorCode)
                else ErrorCode.AGENT_EXECUTION_FAILED
            )
            # provider 语义错误类（字符串，如 AuthenticationError/RateLimitError）经
            # context 保留：下游（auth 探针等）据此区分"LLM 引擎失败"与目标站登录失败，
            # 不再让原始异常串（如 401 令牌过期）被误读为账号密码问题。
            context = _result_cost_context(result)
            if isinstance(result.error_code, str) and not isinstance(result.error_code, ErrorCode):
                context["provider_error_code"] = result.error_code
            if not result.error:
                # 空 error 防漏（2026-08-28 NodeGoat-20260828-054537 后续）：error 在到达
                # executor 前已丢失（falsy → 落 fallback 消息）——这一事实必须留痕，
                # 兜住 provider 上游任何再丢 error 的路径（warning 里带定位上下文）。
                logger.warning(
                    "agent failure with empty result.error — upstream error lost "
                    "(agent=%s error_code=%s retryable=%s turns=%s cost=%s stop_reason=%s)",
                    agent_name.value, result.error_code, result.retryable,
                    result.turns, result.cost, result.stop_reason,
                )
            raise PentestError(
                result.error or f"Agent {agent_name.value} execution failed",
                "validation",
                retryable=result.retryable,
                error_code=error_code,
                context=context,
            )

        queue_filename = get_queue_filename(agent_name)
        payload_bag = collector.get_all() if collector is not None else {}
        recheck_result = None   # 定向重查 result（cost 并账用；未触发/失败为 None）
        still_missing: list[str] = []   # 重查后真实缺口（M-4：info 计数不再用重查前值）
        if (
            not skip_artifact_postprocess
            and queue_filename
            and isinstance(agent_name, AgentName)
            and agent_name.value.endswith("-vuln")
            and result.structured_output is None
        ):
            # Phase 2 B 拓扑（spec 2026-08-19 §3.4）：vuln queue 走 collector 主通道
            # （submit_finding 单条上交）+ finding_roster 确定性对账；
            # structured_output 通道对 vuln 已停用（activities 停传 schema）。
            # 过渡守卫（Task 3→6 窗口）：Task 5 改 prompt / Task 6 停传 schema 落地前，
            # activities 仍传 schema + 旧 prompt 仍产 final structured output——此时
            # structured_output 非 None 走下方旧通道写盘（数据不丢、不整跑重试）；
            # Task 6 后 structured_output 恒 None，本分支（collector 对账）全面接管。
            roster = (payload_bag.get("findings_summary") or {}).get("finding_roster")
            rec = reconcile_findings(payload_bag.get("submitted_findings"), roster)
            if rec.skip_write:
                logger.warning(
                    "agent %s: no submit_finding submissions and no finding_roster — "
                    "queue %s NOT written (validator line will retry the whole agent)",
                    agent_name.value, queue_filename,
                )
            else:
                if rec.overwritten_ids:
                    # M-2：submit_finding 同 ID 重复上交（后交覆盖）曾静默——warning
                    # 留痕（模型修正场景，正常但应可观测）。
                    logger.warning(
                        "agent %s: %d finding id(s) resubmitted, later call "
                        "overwrote earlier: %s",
                        agent_name.value, len(rec.overwritten_ids),
                        rec.overwritten_ids,
                    )
                if rec.extra_ids:
                    logger.warning(
                        "agent %s: %d submitted findings not on roster (kept, "
                        "recall-first): %s",
                        agent_name.value, len(rec.extra_ids), rec.extra_ids,
                    )
                merged_by_id: dict[str, dict] = {str(f.get("ID", "")): f for f in rec.merged}
                if rec.missing:
                    # 重查 prompt 引用主 agent 的 deliverable md 作上下文，但主渲染
                    # 块在本写盘分支之后才跑——重查前先渲染一次，主渲染块幂等覆盖
                    # （fix round 1，spec §3.4：否则首跑重查 agent 读不到 md，
                    # 「自家 md 作重查上下文」从未生效，退化 title-only 线索）。
                    md = render_deliverable(
                        agent_name, collector.get_all(), deliverables,
                        queue_root=queue_root,
                    )
                    if md is not None:
                        (deliverables / defn.deliverable_filename).write_text(
                            md, encoding="utf-8")
                    recheck_items, recheck_result = await _targeted_recheck(
                        agent_name, str(repo), deliverables, rec.missing,
                        defn.model_tier, api_key, provider_config, proxy_url,
                        audit_logger=audit_logger,
                        tool_audit_logger=tool_audit_logger,
                    )
                    if recheck_result is not None:
                        logger.info(
                            "agent %s targeted recheck finished: cost=%.6f turns=%s",
                            agent_name.value,
                            recheck_result.cost or 0.0, recheck_result.turns,
                        )
                    missing_ids = {m["id"] for m in rec.missing}
                    off_target: list[str] = []
                    for it in recheck_items:
                        rid = str(it.get("ID", ""))
                        if rid and rid not in merged_by_id:
                            merged_by_id[rid] = it  # 不覆盖已交；off-target 追加
                            if rid not in missing_ids:
                                off_target.append(rid)
                    if off_target:
                        logger.warning(
                            "agent %s: recheck returned %d findings outside the "
                            "missing list (appended, recall-first): %s",
                            agent_name.value, len(off_target), off_target,
                        )
                    still_missing = [m["id"] for m in rec.missing if m["id"] not in merged_by_id]
                    if still_missing:
                        logger.warning(
                            "agent %s: %d findings still missing after targeted "
                            "recheck (accepted with degradation, no full retry): %s",
                            agent_name.value, len(still_missing), still_missing,
                        )
                rec.merged = list(merged_by_id.values())
                # roster title 回填（2026-09-03 NodeGoat 空标题回归）：submit_finding
                # 漏 title 的条目（含 targeted recheck 追加的）由 finding_roster
                # 全量账本兜底回填——零 LLM 成本，写盘前统一过一遍。
                filled_ids = backfill_titles_from_roster(rec.merged, roster)
                if filled_ids:
                    logger.warning(
                        "agent %s: %d finding(s) missing title backfilled "
                        "from finding_roster: %s",
                        agent_name.value, len(filled_ids), filled_ids,
                    )
                queue_path = intermediate_path(deliverables, queue_filename)
                atomic_write_json(
                    queue_path, {"vulnerabilities": rec.merged})
                logger.info(
                    "agent %s queue written from collector: submitted=%d "
                    "roster=%d merged=%d missing=%d",
                    agent_name.value, len(payload_bag.get("submitted_findings") or []),
                    len(roster or []), len(rec.merged), len(still_missing),
                )
            # P3（数据流视图）：queue 落盘同步落 {vc}_safe_vectors.json——
            # 组装器需结构化源（safe_vectors 目前只渲染进 md）。空/缺失不落盘。
            _dump_safe_vectors(deliverables, agent_name.value.removesuffix("-vuln"),
                               payload_bag)
            # spec 2026-08-27 §6：LLM 轨判非漏洞（safe_vectors）并档
            _archive_dismissed_from_safe_vectors(
                deliverables, agent_name.value.removesuffix("-vuln"), payload_bag)
        elif (
            not skip_artifact_postprocess
            and result.structured_output is not None
            and queue_filename
        ):
            # 旧通道（-exploit 等其余 agent；vuln 已由上方分支接管）。
            # spec 2026-08-18 tiering：queue json 下沉桶内 intermediate/（交付物留顶层）。
            queue_path = intermediate_path(deliverables, queue_filename)
            atomic_write_json(queue_path, result.structured_output)
        elif (
            not skip_artifact_postprocess
            and queue_filename
            and not (
                isinstance(agent_name, AgentName)
                and agent_name.value.endswith("-exploit")
            )
        ):
            # 诊断（spec 2026-08-19 §3.2）：现状此分支零日志静默跳过，网关断流
            # 排障全靠猜；warning 留第一现场（validate 防线随后 raise 补 context）。
            # -exploit 排除（2026-08-28 NodeGoat-20260828-054537 误报实证）：黑盒
            # exploit 的 verdicts 走 add_exploit collector 通道（host 渲染
            # {vc}_exploit_verdicts.json），调用侧不传 schema、structured_output
            # 恒 None；queue 文件无读方、validators 对 -exploit no-op（TS
            # createExploitValidator 同为 no-op）——不排除则每 run 每类必发一条
            # 误报 WARNING，淹没 -vuln 真静默漏盘信号。
            logger.warning(
                "agent %s produced no structured output — queue %s NOT written "
                "(text_len=%d, stop_reason=%r)",
                agent_name.value, queue_filename,
                len(getattr(result, "text", "") or ""), result.stop_reason,
            )

        # host 渲染写 md:有 collector 通道的 agent(Plan 1 = pre-recon)在 queue 写盘
        # 之后、validate 之前,用 collector payload 确定性渲染 deliverable md。这样
        # validate_deliverable 见文件即过(无需把 pre-recon validator 改 no-op)。对齐
        # TS agent-execution.ts:295-297 writeDeliverable。render_deliverable 对无
        # collector 的 agent 返 None → 跳过写盘(self-Write 路径不动)。
        if not skip_artifact_postprocess and collector is not None:
            md = render_deliverable(agent_name, collector.get_all(), deliverables, queue_root=queue_root)
            if md is not None:
                # tiering（spec 2026-08-18）：黑盒 exploit agent 的 evidence md 落
                # blackbox/ 桶内（修漂移——读方 coverage/rerun 一直按桶内找，靠
                # fallback 兜根顶层）；白盒 md 留桶顶层（activities 传桶内路径）。
                md_dir = deliverables
                if isinstance(agent_name, AgentName) and agent_name.value.endswith("-exploit"):
                    from supernova_core.utils.paths import blackbox_dir
                    md_dir = blackbox_dir(deliverables)
                    md_dir.mkdir(parents=True, exist_ok=True)
                (md_dir / defn.deliverable_filename).write_text(md, encoding="utf-8")

            # exploit agent 额外写结构化 verdicts.json（补全主线缺失产物，spec 2026-08-12）。
            # 计数器数 exploited、coverage/PoC 读 accepted_ids；与 evidence.md 同源
            # （build_exploit_verdicts_payload 复用 _build_exploit_validation）。
            if isinstance(agent_name, AgentName) and agent_name.value.endswith("-exploit"):
                from supernova_core.renderers import build_exploit_verdicts_payload
                from supernova_core.utils.paths import blackbox_dir

                vc = agent_name.value.removesuffix("-exploit")
                payload = build_exploit_verdicts_payload(
                    vc, collector.get_all(), deliverables, queue_root=queue_root,
                    agent_run={
                        "turns": int(getattr(result, "turns", 0) or 0),
                        "duration_ms": int(getattr(result, "duration", 0) or 0),
                        "success": bool(getattr(result, "success", False)),
                        "stop_reason": getattr(result, "stop_reason", None),
                        "error": getattr(result, "error", None),
                    },
                    tool_events=trace_logger.tool_events,
                )
                # tiering：verdicts 是机器交接数据 → blackbox/intermediate/（evidence
                # 同桶，机器数据下沉子层）。读方走 resolve_track_deliverable 三级链。
                atomic_write_json(
                    intermediate_path(blackbox_dir(deliverables), f"{vc}_exploit_verdicts.json"),
                    payload)

        if not skip_artifact_postprocess:
            try:
                await validate_deliverable(deliverables, agent_name)
            except PentestError as exc:
                # 诊断增补（spec 2026-08-19 §3.2/§3.4）：防线 raise 原地补 result 级
                # 证据（stop_reason/文本尾巴/通道状态/cost）+ collector 对账计数，
                # 再上抛——不改 validate_deliverable 签名（纯函数波及面大），也不吞
                # retryable /error_code 分类（原地 update，分类字段不动）。
                submitted = payload_bag.get("submitted_findings") or []
                roster = (payload_bag.get("findings_summary") or {}).get("finding_roster")
                exc.context.update(_validation_error_context(
                    result,
                    {"submitted": len(submitted),
                     "roster": len(roster) if roster is not None else 0},
                    recheck_result=recheck_result,
                ))
                raise

        await GitManager.commit(deliverables, agent_name)

        # 定向重查 LLM 消耗并入主 result（final review fix 1）：cost/turns/tokens
        # None-safe 相加（两边都 None 保持 None）；cost_currency 取主 result 的
        # （同一 provider/profile，重查不换引擎）。duration_ms 只计主 agent（重查
        # 已计入 LLM 消耗维度，时长维度不并避免误解为单 agent 耗时）。
        cost = result.cost
        turns = result.turns
        input_tokens = result.tokens.input_tokens if result.tokens else None
        output_tokens = result.tokens.output_tokens if result.tokens else None
        cache_read = result.tokens.cache_read_input_tokens if result.tokens else None
        cache_creation = (
            result.tokens.cache_creation_input_tokens if result.tokens else None)
        if recheck_result is not None:
            rt = recheck_result.tokens
            cost = _none_safe_add(cost, recheck_result.cost)
            turns = _none_safe_add(turns, recheck_result.turns)
            input_tokens = _none_safe_add(
                input_tokens, rt.input_tokens if rt else None)
            output_tokens = _none_safe_add(
                output_tokens, rt.output_tokens if rt else None)
            cache_read = _none_safe_add(
                cache_read, rt.cache_read_input_tokens if rt else None)
            cache_creation = _none_safe_add(
                cache_creation, rt.cache_creation_input_tokens if rt else None)

        return AgentMetrics(
            duration_ms=duration_ms,
            cost_usd=cost,
            cost_currency=result.cost_currency,
            num_turns=turns,
            model=result.model,
            structured_output=result.structured_output,
            stop_reason=result.stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
        )

