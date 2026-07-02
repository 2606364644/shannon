from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from shannon_core.models.multi_repo_config import MultiRepoConfig


@dataclass
class RepoScanPlan:
    service: str
    repo_path: str | None
    workspace: str | None
    reuse: bool
    scan_config: str | None


def plan_repo_scans(config: MultiRepoConfig) -> list[RepoScanPlan]:
    """纯函数:决定每个 repo 复用已有 workspace 还是现扫。
    复用条件:声明了 workspace(交付物完整性由编排器后续检查)。
    否则需要 path → 现扫。
    """
    plans: list[RepoScanPlan] = []
    for service, spec in config.repos.items():
        if spec.workspace:
            plans.append(RepoScanPlan(service=service, repo_path=spec.path,
                                      workspace=spec.workspace, reuse=True,
                                      scan_config=spec.scan_config))
        else:
            plans.append(RepoScanPlan(service=service, repo_path=spec.path,
                                      workspace=None, reuse=False,
                                      scan_config=spec.scan_config))
    return plans


# ---------------------------------------------------------------------------
# Task A6: per-edge asyncio + 单边隔离 + merge
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
from shannon_core.correlation.schemas import (  # noqa: E402
    CrossServiceTopology, ServiceNode, TopologyEdge, Call, CallSite, TrustBoundary,
)
from shannon_core.correlation.queue_merge import merge_exploitation_queues  # noqa: E402
from shannon_core.correlation.drift import detect_drift  # noqa: E402
from shannon_core.utils.paths import WHITEBOX_SUBDIR  # noqa: E402

logger = logging.getLogger(__name__)


def _prompts_dir() -> Path:
    """Absolute prompts dir, independent of process CWD.

    orchestrator.py is at <repo>/packages/multi/src/shannon_multi/orchestrator.py,
    so parents[4] is the repo root that holds prompts/.
    (final-review IMPORTANT 1: 避免非 repo-root CWD 调用时 Prompt file not found 崩溃)
    """
    return Path(__file__).resolve().parents[4] / "prompts"


async def _run_edge(from_svc: str, to_svc: str, *, runner) -> dict:
    """单条 edge 推断。runner 是 async(f,t)->dict(真实=AgentExecutor 调用)。
    失败 → 标 status=error,不抛(spec §8 单边隔离)。"""
    try:
        return await runner(from_svc, to_svc)
    except Exception as e:  # noqa: BLE001
        return {"from": from_svc, "to": to_svc, "protocol": "grpc",
                "calls": [], "status": "error", "error": str(e), "boundaries": []}


def _merge_edge_results(edge_results: list[dict]) -> dict:
    edges, boundaries = [], []
    for r in edge_results:
        edges.append({"from": r["from"], "to": r["to"], "protocol": r.get("protocol", "grpc"),
                      "calls": r.get("calls", []), "status": r.get("status", "ok"),
                      "error": r.get("error")})
        boundaries.extend(r.get("boundaries", []))
    return {"edges": edges, "boundaries": boundaries}


async def run_cross_repo(config_path: Path, temporal_address: str, *, pipeline_testing: bool = False) -> dict:
    from shannon_core.config.parser import parse_multi_repo_config
    from shannon_core.session import SessionManager
    from shannon_core.utils.paths import deliverables_dir_for_workspace
    from shannon_core.agents.executor import AgentExecutor
    from shannon_core.prompts.manager import PromptManager
    from shannon_core.models.agents import AgentName
    from shannon_core.correlation.report import write_correlation_deliverables
    from shannon_whitebox.worker import run_scan as run_whitebox
    from shannon_whitebox.pipeline.shared import PipelineInput

    config = parse_multi_repo_config(config_path)
    plans = plan_repo_scans(config)
    # workspace 根必须与 run_whitebox 写入根一致(run_whitebox 用 resolve_workspaces_dir(repo_path)),
    # 否则 SHANNON_WORKER_ROOT 或 cwd≠project-root 时读取会落空(A6 review Important #1)。
    from shannon_core.utils.paths import resolve_workspaces_dir
    from shannon_multi.correlation_event_writer import CorrelationEventWriter

    # 联动进度 writer：在 repo 扫描开始前就绪，events.ndjson 路径与下方
    # SessionManager.create_workspace(name=config.correlation.out_workspace) 同根同目录。
    # （create_workspace 幂等：目录已存在不报错；session.json 仍由它写。）
    corr_writer = CorrelationEventWriter(
        resolve_workspaces_dir() / config.correlation.out_workspace / "events.ndjson")
    overall_failed = False

    # 1. N repo 白盒:复用 or 现扫
    per_repo_deliverables: dict[str, Path] = {}
    per_repo_queue: dict[str, list[dict]] = {}
    drift_warnings: list[str] = []
    for p in plans:
        # 每个 repo 的 workspace 都从其写入根(resolve_workspaces_dir(repo_path))读取,
        # 与 run_whitebox 的 resolve_workspaces_dir(input.repo_path) 对齐。
        repo_ws_root = resolve_workspaces_dir(p.repo_path)
        if p.reuse:
            await corr_writer.repo(p.service, "started")
            ws_path = repo_ws_root / p.workspace
            # A2 版本漂移检测(时间戳粗判,仅复用且 repo path 已知且盘上存在时)。
            # final-review MINOR 5: 复用 workspace 的 path 可能已失配/移动,
            # getmtime 会 FileNotFoundError 并中止整个编排 —— 加 Path.exists() 守卫优雅降级(跳过漂移检测)。
            if p.repo_path and (ws_path / "session.json").exists() and Path(p.repo_path).exists():
                sess = json.loads((ws_path / "session.json").read_text(encoding="utf-8"))
                rpt = detect_drift(sess.get("created_at", 0.0), os.path.getmtime(p.repo_path))
                if rpt.drifted:
                    drift_warnings.append(f"{p.service}: {rpt.note}")
            await corr_writer.repo(p.service, "completed", detail="reused")
        else:
            await corr_writer.repo(p.service, "started")
            wb_input = PipelineInput(repo_path=p.repo_path, workspace_name=p.workspace,
                                     config_path=p.scan_config,
                                     pipeline_testing_mode=pipeline_testing)
            try:
                result = await run_whitebox(wb_input, temporal_address)
            except Exception:
                overall_failed = True
                await corr_writer.repo(p.service, "failed", detail="scan error")
                raise
            ws_path = repo_ws_root / result["workspace_name"]
            await corr_writer.repo(p.service, "completed")
        dlv = deliverables_dir_for_workspace(ws_path)
        per_repo_deliverables[p.service] = dlv
        # 收集该仓所有 exploitation_queue(spec §7 合并, B1)
        # 白盒 queue 新结构在 whitebox/ 子目录,老结构在 deliverables 根;
        # 合并去重(whitebox/ 优先,根条目仅补白)。
        queue_files: dict[str, Path] = {}
        for q in (dlv / WHITEBOX_SUBDIR).glob("*_exploitation_queue.json"):
            queue_files[q.name] = q
        for q in dlv.glob("*_exploitation_queue.json"):
            queue_files.setdefault(q.name, q)
        for q in queue_files.values():
            vc = q.stem.replace("_exploitation_queue", "")
            try:
                entries = json.loads(q.read_text(encoding="utf-8")).get("vulnerabilities", [])
            except (json.JSONDecodeError, OSError) as e:
                # final-review MINOR 6: 仅捕解析/IO 错并留痕,不再静默吞所有异常。
                logger.warning("跳过损坏的 exploitation queue %s: %s", q, e)
                entries = []
            per_repo_queue.setdefault(vc, []).extend(
                [{"__service": p.service, **e} for e in entries])

    # 2. 关联 workspace —— 无归属 repo,用标准 workspaces 根(resolve_workspaces_dir() 无参),
    # 与 run_whitebox 默认写入根一致,Phase B --correlated-workspace 可在该标准位置找到。
    mgr = SessionManager(resolve_workspaces_dir())
    out_ws = mgr.create_workspace(web_url="", repo_path="",
                                  name=config.correlation.out_workspace,
                                  scan_type="correlation")
    out_dlv = deliverables_dir_for_workspace(out_ws)

    # 3. per-edge 关联 Agent(asyncio.Semaphore 限并发, B5)
    role_map = {s: spec.role for s, spec in config.repos.items()}
    repo_paths = {s: spec.path for s, spec in config.repos.items()}
    # final-review MINOR 8: 并发上限改接 SHANNON_MAX_CONCURRENT(whitebox/blackbox 同源 env 驱动,
    # 默认 3),闭合 spec B5 风险登记 TODO。
    from shannon_core.config.concurrency import get_max_concurrent
    sem = asyncio.Semaphore(get_max_concurrent())
    # final-review IMPORTANT 1+2: PromptManager 用绝对路径(_prompts_dir() 不受 CWD 影响),
    # 且单实例提升到 run_cross_repo 作用域 —— N 条 edge 不再重复构造 executor / 重编译 prompt。
    executor = AgentExecutor(PromptManager(_prompts_dir()))
    edge_output_schema = {
        "type": "object",
        "properties": {
            "from": {"type": "string"}, "to": {"type": "string"},
            "protocol": {"type": "string"}, "status": {"type": "string"},
            "calls": {"type": "array"}, "boundaries": {"type": "array"},
        },
        "required": ["from", "to", "status"],
    }

    async def edge_runner(f: str, t: str) -> dict:
        async with sem:
            prompt_vars = {
                "relations_json": json.dumps({"from": f, "to": t,
                    "protocol": next((r.protocol for r in config.relations
                                      if r.from_ == f and r.to == t), "grpc")}),
                "role_map": json.dumps(role_map),
                "repo_paths": json.dumps({f: repo_paths.get(f), t: repo_paths.get(t)}),
                "deliverables_path": str(out_dlv),
            }
            metrics = await executor.execute(
                agent_name=AgentName.CROSS_REPO_CORRELATION,
                repo_path=str(out_ws),  # 注:非 git repo,但 git ops 全在 deliverables(见 Task A6 风险 #1)
                deliverables_path=str(out_dlv),
                pipeline_testing=pipeline_testing,
                prompt_variables=prompt_vars,
                structured_output_schema=edge_output_schema,  # 强制单 edge JSON 输出
            )
            # A6 风险 #3:AgentMetrics 真实属性是 structured_output(非 brief 的 output)。
            # 取不到合法 payload 则降级 unverified(spec §8 per-edge 隔离)。
            payload = getattr(metrics, "structured_output", None)
            if isinstance(payload, dict) and "from" in payload:
                return payload
            return {"from": f, "to": t, "protocol": "grpc", "calls": [],
                    "status": "unverified", "boundaries": []}

    edge_pairs = [(r.from_, r.to) for r in config.relations]
    await corr_writer.phase("correlation", "started")
    edge_results = await asyncio.gather(
        *[_run_edge(f, t, runner=edge_runner) for f, t in edge_pairs])
    # per-edge 进度（_run_edge 已把异常映射为 status=error,spec §8 单边隔离,不致 scan 失败）
    for er in edge_results:
        await corr_writer.edge(f"{er['from']}->{er['to']}", er.get("status", "ok"))
    merged = _merge_edge_results(edge_results)

    # 4. 组装 topology + boundaries
    topology = CrossServiceTopology(
        services=[ServiceNode(name=s, role=spec.role, repo=spec.path or spec.workspace or "")
                  for s, spec in config.repos.items()],
        edges=[TopologyEdge(from_=e["from"], to=e["to"], protocol=e["protocol"],
                            calls=[Call(method=c["method"],
                                        call_site=CallSite(**c["call_site"]),
                                        confidence=c["confidence"], evidence=c["evidence"])
                                   for c in e.get("calls", [])],
                            status=e["status"], error=e.get("error"))
               for e in merged["edges"]])
    boundaries = [TrustBoundary(**b) for b in merged["boundaries"]]

    # 5. 合并 queue(B1 四字段)+ 落盘
    merged_queues = {vc: merge_exploitation_queues(
        _group_by_service(entries)) for vc, entries in per_repo_queue.items()}
    report_md = _render_report(topology, boundaries, merged_queues, drift_warnings)
    write_correlation_deliverables(out_dlv, topology, boundaries, merged_queues, report_md)

    await corr_writer.scan_end("failed" if overall_failed else "completed")

    return {"out_workspace": config.correlation.out_workspace,
            "deliverables_path": str(out_dlv),
            "edge_statuses": [e["status"] for e in merged["edges"]]}


def _group_by_service(entries: list[dict]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = {}
    for e in entries:
        svc = e.pop("__service", "unknown")
        g.setdefault(svc, []).append(e)
    return g


def _render_report(topology, boundaries, merged_queues, drift_warnings) -> str:
    lines = ["# Cross-Repo Correlation Report", "",
             "## 服务拓扑", ""]
    for e in topology.edges:
        lines.append(f"- {e.from_} → {e.to} ({e.protocol}) [{e.status}]")
    lines += ["", "## 未验证/低置信/失败项(透明单列)", ""]
    for e in topology.edges:
        if e.status in ("low", "unverified", "error", "declared-missing"):
            lines.append(f"- {e.from_}→{e.to}: {e.status} {e.error or ''}")
    if drift_warnings:
        lines += ["", "## 版本漂移警告(A2)", ""]
        lines += [f"- {w}" for w in drift_warnings]
    return "\n".join(lines)
