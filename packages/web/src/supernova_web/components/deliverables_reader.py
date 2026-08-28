from __future__ import annotations

import json
from pathlib import Path

from supernova_core.models.deliverables import classify_tier
from supernova_core.utils.paths import COMBINED_SUBDIR, WHITEBOX_SUBDIR, resolve_track_deliverable

BIG_JSON_THRESHOLD = 50_000
_EXCLUDE_DIRS = {"__pycache__", "schemas"}

# PoC 集合 md 文件名——export_report_markdown_files 单源导出(前身 poc_generator,已退役)。
POC_FILENAME = "exploitable_poc_collection.md"


def _is_valid_queue_file(p: Path) -> bool:
    """非空且含 vulnerabilities 数组的 queue 文件。"""
    if not p.exists() or p.stat().st_size == 0:
        return False
    try:
        data = json.loads(p.read_text("utf-8"))
        vulns = data.get("vulnerabilities", [])
        return isinstance(vulns, list) and len(vulns) > 0
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False


def _classify(f: Path) -> str:
    """对齐前端 DeliverablesFile kind(types.ts)+ FilePreview 预览分支。"""
    name = f.name
    if name.endswith(".md"):
        return "md"
    if name.endswith("_exploitation_queue.json"):
        return "exploitation_queue" if _is_valid_queue_file(f) else "empty_json"
    if name.endswith("_llm_queue.json"):
        return "llm_queue" if _is_valid_queue_file(f) else "empty_json"
    if name.endswith("_gitnexus_queue.json"):
        return "gitnexus_queue" if _is_valid_queue_file(f) else "empty_json"
    if name.endswith(".json"):
        try:
            size = f.stat().st_size
            data = json.loads(f.read_text("utf-8"))
            if data in ([], {}, None):
                return "empty_json"
            return "big_json" if size > BIG_JSON_THRESHOLD else "other_json"
        except (json.JSONDecodeError, UnicodeDecodeError):
            return "other_json"
    return "other"


class DeliverablesReader:
    """Read deliverables for a workspace, supporting both the new track-scoped
    layout (``deliverables/{whitebox,blackbox}/*``) and the legacy flat layout
    (``deliverables/*``). md / json / log reads + summary.
    """

    def __init__(self, workspace_path: Path, strip_track_prefix: str | None = None) -> None:
        self._ws = Path(workspace_path)
        self._deliverables = self._ws / "deliverables"
        # tiering 展示层归一（spec 2026-08-18 降级方案）：run 级黑盒产物物理在
        # deliverables/blackbox/，剥掉该前缀让 run 视图树不再有冗余桶层。
        self._strip = strip_track_prefix

    def _iter_files(self):
        """扫 deliverables 所有文件。排除 __pycache__/schemas + 一切 ``.`` 开头
        条目（.git/.whitebox-archive/.blackbox-archive/.poc_checkpoint.json 等，
        spec 2026-08-18：归档与管线状态不出现在产物页）。"""
        if not self._deliverables.exists():
            return
        for f in sorted(self._deliverables.rglob("*")):
            if not f.is_file():
                continue
            if any(part in _EXCLUDE_DIRS for part in f.parts):
                continue
            if any(part.startswith(".") for part in f.relative_to(self._deliverables).parts):
                continue
            yield f

    def _infer_track(self, default: str = WHITEBOX_SUBDIR) -> str:
        # combined 优先：融合报告 combined_report.md 存在 → track=combined（组合扫描终态）。
        # 文件级判定（非目录）避免组合扫描期三桶均存在时误判；combined/ 无报告则回退。
        if (self._deliverables / COMBINED_SUBDIR / "combined_report.md").is_file():
            return COMBINED_SUBDIR
        for t in ("whitebox", "blackbox"):
            if (self._deliverables / t).is_dir():
                return t
        return default

    def summary(self, track: str = WHITEBOX_SUBDIR) -> dict:
        """返 DeliverablesSummary {track, files, aggregated_vulnerabilities, notes},
        对齐前端 types.ts。不再透传 core {vuln_queues, reports}。"""
        if not self._deliverables.exists():
            return {"track": track, "files": [], "aggregated_vulnerabilities": [], "notes": {}}
        track = self._infer_track(track)
        files = [
            {
                "path": self._display_path(f),  # scan 级含 track 前缀；run 级已剥桶层
                "size": f.stat().st_size,
                "kind": _classify(f),
                "tier": classify_tier(str(f.relative_to(self._deliverables))),  # tiering
            }
            for f in self._iter_files()
        ]
        injection_has_no_queue = not any(
            f.name == "injection_exploitation_queue.json" and _is_valid_queue_file(f)
            for f in self._iter_files()
        )
        return {
            "track": track,
            "files": files,
            "aggregated_vulnerabilities": self._aggregate_vulns(),
            "notes": {"injection_has_no_queue": injection_has_no_queue},
        }

    def _aggregate_vulns(self) -> list:
        """跨 *_exploitation_queue.json 聚合 vulnerabilities(空/无效跳过)。
        同名 queue 在 track-scoped 与 legacy 平铺两处共存时只计一次
        （sorted 顺序 track 目录在前，取首个——迁移窗口防双计）。"""
        out = []
        seen_queues: set[str] = set()
        for f in self._iter_files():
            if not f.name.endswith("_exploitation_queue.json"):
                continue
            if f.name in seen_queues:
                continue
            if not _is_valid_queue_file(f):
                continue
            seen_queues.add(f.name)
            try:
                data = json.loads(f.read_text("utf-8"))
                for v in data.get("vulnerabilities", []):
                    if isinstance(v, dict):
                        out.append(v)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
        return out

    def list_reports(self) -> list[str]:
        """扫 *.md 报告(排除 .git/__pycache__/schemas),按文件名去重。"""
        out: list[str] = []
        for f in self._iter_files():
            if f.suffix == ".md" and f.name not in out:
                out.append(f.name)
        return out

    def list_logs(self) -> list[str]:
        """列日志文件:顶层 *.log + *.ndjson + agents|logs|.authcheck/*.log(返相对
        路径含前缀,前端回传 ?file=xxx 经 read_log 解析)。logs/ 是 diagnostic.log
        (底层引擎诊断流,GitNexus stderr 等确定性轨问题第一现场,CLI `supernova logs
        --diagnostic` 同能力);.authcheck/ 是 t0 认证预验证子 workflow 日志(precheck
        失败详细过程);*.ndjson 是主事件流原始文件(events.ndjson/authcheck-events)。"""
        out: list[str] = []
        for f in sorted(self._ws.glob("*.log")):
            if f.is_file():
                out.append(f.name)
        for f in sorted(self._ws.glob("*.ndjson")):
            if f.is_file():
                out.append(f.name)
        for sub in ("agents", "logs", ".authcheck"):
            sub_dir = self._ws / sub
            if sub_dir.is_dir():
                for f in sorted(sub_dir.glob("*.log")):
                    if f.is_file():
                        out.append(f"{sub}/{f.name}")
        return out

    def _display_path(self, f: Path) -> str:
        """summary 展示路径：strip 模式剥掉桶前缀（run 级归一），否则含 track 前缀。"""
        rel = str(f.relative_to(self._deliverables))
        if self._strip and rel.startswith(self._strip + "/"):
            return rel[len(self._strip) + 1:]
        return rel

    def resolve_path(self, filename: str, track: str | None = None) -> Path:
        """解析产物物理路径。track=None(未指定,如 report_for)→ 像 summary/read_poc
        那样按目录布局自动推断, 否则黑盒扫描报告落在 blackbox/ 却按默认 whitebox
        track 找不到 -> 500。显式传 track(如 deliverables_file_for 从路由 query
        param)时尊重之。不存在抛 FileNotFoundError——read() 与下载端点
        (FileResponse 附件,不读内容)共用。"""
        resolved = self._infer_track() if track is None else track
        p = resolve_track_deliverable(self._deliverables, resolved, filename)
        if not p.exists() and self._strip:
            # strip 模式：无前缀文件名也可能物理在 deliverables 根（老 run 结构）
            p = self._deliverables / filename
        if not p.exists():
            raise FileNotFoundError(filename)
        return p

    def read(self, filename: str, track: str | None = None,
             preview_limit: int | None = None) -> dict | list | str:
        """读单产物。preview_limit（spec 2026-08-18）：超限文件返回截断 str + 标注
        （跳过 json.loads，几十 MB 的 code_index.json 不再整载入/整传输）。"""
        p = self.resolve_path(filename, track)
        text = p.read_text("utf-8")
        if preview_limit is not None and len(text) > preview_limit:
            return (f"{text[:preview_limit]}\n\n…[truncated: showing {preview_limit} of "
                    f"{len(text)} characters — full file on disk]")
        if p.suffix == ".json":
            return json.loads(text) if text.strip() else []
        return text

    def read_log(self, name: str = "workflow.log") -> str:
        """读单日志:name 是 list_logs 返回的相对路径(可含 agents//logs//.authcheck/
        前缀或裸文件名,裸名回退 agents/ 兼容旧约定)。name 来自前端 ?file= query
        param——路径穿越(../ 越界/绝对路径注入)须拒绝:候选 resolve 后必须仍在
        _ws 内,否则 FileNotFoundError(不泄露存在性)。"""
        ws_root = self._ws.resolve()
        for cand in (self._ws / name, self._ws / "agents" / name):
            try:
                if cand.exists() and cand.resolve().is_relative_to(ws_root):
                    return cand.read_text("utf-8")
            except OSError:
                continue
        raise FileNotFoundError(name)

    def read_poc(self) -> str | None:
        """读 PoC md(exploitable_poc_collection.md),不存在返回 None(不抛,调用方按无 PoC 处理)。
        track-scoped(deliverables/{track}/)+ legacy flat 布局与 read() 同口径。
        PoC md 自带「# 可利用漏洞 PoC 集合」一级标题 + 概览/置信度统计。
        """
        p = resolve_track_deliverable(self._deliverables, self._infer_track(), POC_FILENAME)
        if not p.exists():
            return None
        try:
            return p.read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            return None
