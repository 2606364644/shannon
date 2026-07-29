"""T1: 1 ws : N scans 存储层（web 层 ScanStore）。

core SessionManager 的读写方法（get_session_data/update_session/get_status/
get_created_at/...）只收 workspace_path、不依赖 workspaces_dir；create_workspace/
list_workspaces 用 self.workspaces_dir。故 SessionManager(ws_dir/"scans") 把 scans
目录当 workspaces 根即可复用全部 scan 读写--create_workspace(name=scan_id) 即在
scans/<scan_id>/session.json 建 scan；list_workspaces() 即列该 ws 的 scans。
**core session.py 源码零改动**（CLAUDE.md §1 铁律不碰）。

双源兼容（design decision 7）：list_scans 同时识别
  ① workspaces/<ws>/scans/<scan_id>/session.json（新模型，web 建）
  ② workspaces/<ws>/session.json（legacy：CLI/worker.py 旧路径产出，或未迁移的 web scan）
统一列为 scan，按 created_at 倒序。worker.py:122-123 据 event_file.parent 推导
workspace_path=scan_dir，产物自然落 scan 子目录（worker 零改动）。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from supernova_core.session import SessionManager
from supernova_core.workspace import get_workspace_vuln_counts

from .scan_liveness import is_scan_alive  # noqa: F401  (语义导出，便于测试 monkeypatch)
from .workspaces_indexer import _compute_status, _to_unix


def _now_local() -> datetime:
    """scan_id 时间戳取本地时区 now（抽函数便于测试 monkeypatch 同秒碰撞）。"""
    return datetime.now()


def _repo_label(repo_path: str) -> str:
    """从 repo_path 派生仓库名标签（scan_id 前缀）：basename 合法化为目录名/标识符安全
    字符——连续的路径分隔符 / 空白 / 点 -> 单个 '-'，去首尾 '-'，空 fallback 'repo'。
    对齐 core _default_workspace_name 的 hostname 推导（Path.name.replace('.', '-')）。"""
    name = Path(repo_path).name
    label = re.sub(r"[/\\\s.]+", "-", name).strip("-")
    return label or "repo"


def resolve_workflow_id(ws: str, scan_dir: Path, scan_id: str) -> str:
    """temporal workflow_id（前端「扫描任务名」展示，替代纯日期 scan_id）。

    优先读 scan_dir/events.ndjson 首行 WorkflowHeader.workflow_id —— 真实 temporal id 的
    single source of truth：CLI/legacy scan 的 workflow_id = workspace_name（CLI scheme，如
    sentinel_dashboard_20260721-201435），与 web scan 的 {ws}-{scan_id}（web scheme）不同，
    算不出来只能读。web scan 的 ndjson WorkflowHeader.workflow_id 亦是 {ws}-{scan_id}
    （scan_manager 提交时由 worker 写入），与 fallback 一致。

    读不到（无 events.ndjson，如未启动 scan）才 fallback 算 {ws}-{scan_id}[-resume-N]
    （读 session.json resumeAttempts 算 N，对齐 scan_manager._resolve_workflow_id）。
    """
    wf = _read_workflow_id_from_ndjson(scan_dir)
    if wf:
        return wf
    n = 0
    session_file = scan_dir / "session.json"
    if session_file.exists():
        try:
            data = json.loads(session_file.read_text("utf-8"))
            attempts = data.get("resumeAttempts") or []
            if isinstance(attempts, list):
                n = len(attempts)
        except (json.JSONDecodeError, OSError):
            n = 0
    base = f"{ws}-{scan_id}"
    return f"{base}-resume-{n}" if n > 0 else base


def _read_workflow_id_from_ndjson(scan_dir: Path) -> str | None:
    """读 events.ndjson 首行 WorkflowHeader.workflow_id（scan 启动即写，真实 temporal id）。

    WorkflowHeader 是 scan 首个 event（必为首行）；只读首个非空行即停，避免扫整个大 ndjson。
    损坏 / 首行非 WorkflowHeader -> None（调用方 fallback 算）。
    """
    ndjson = scan_dir / "events.ndjson"
    if not ndjson.exists():
        return None
    try:
        with ndjson.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                evt = json.loads(line)
                if isinstance(evt, dict) and evt.get("type") == "WorkflowHeader":
                    wf = evt.get("workflow_id")
                    if isinstance(wf, str) and wf:
                        return wf
                break  # 首个非空行非 WorkflowHeader -> 放弃（不扫全文件）
    except (json.JSONDecodeError, OSError):
        return None
    return None


# ws 级保留目录/文件（非 scan 产物）：T5 legacy 整体搬迁时留在 ws 根不动。
# indexer 列 ws 也据此区分 ws 级元数据 vs scan 产物。
WORKSPACE_META_FILENAME = "workspace.json"


def read_workspace_meta(ws_dir: Path) -> dict | None:
    """读 ws 元数据：workspace.json 优先，回退 legacy ws 根 session.json（无 workspace.json
    的旧 ws）。两者皆无 -> None（不是 ws）。供 indexer 列 ws 用。"""
    wf = ws_dir / WORKSPACE_META_FILENAME
    if wf.exists():
        try:
            data = json.loads(wf.read_text("utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    sf = ws_dir / "session.json"
    if sf.exists():
        try:
            data = json.loads(sf.read_text("utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return None


def write_workspace_meta(ws_dir: Path, name: str, owner: str,
                         description: str | None = None,
                         created_at: str | None = None) -> None:
    """写 workspace.json = {name, created_at, owner, description?}。

    created_at 缺省取 now（ISO）；迁移场景可传入既有时间戳保真。
    """
    data = {
        "name": name,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "owner": owner,
    }
    if description is not None:
        data["description"] = description
    (ws_dir / WORKSPACE_META_FILENAME).write_text(
        json.dumps(data, indent=2), encoding="utf-8")


@dataclass
class ScanSummary:
    """ws 内单个 scan 的摘要（GET /{ws}/scans 与 GET /{ws} 的 scans[] 共用）。

    含 indexer 旧 row 所需的全部 scan 级字段（vuln_counts/total_duration_ms/links/
    is_correlation），使 1 ws : N scans 后 ws 列表行能从 latest scan 聚合，兼容旧前端。
    """
    scan_id: str
    scan_type: str
    status: str
    created_at: float | None
    completed_at: float | None
    vuln_count: int
    vuln_counts: dict
    total_cost_usd: float | None
    cost_currency: str | None
    total_duration_ms: float | None
    links: dict
    is_running: bool
    is_correlation: bool
    workflow_id: str  # temporal workflow 标识 {ws}-{scan_id}[-resume-N]，前端任务名展示

    def as_dict(self) -> dict:
        return {
            "scan_id": self.scan_id,
            "scan_type": self.scan_type,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "vuln_count": self.vuln_count,
            "vuln_counts": self.vuln_counts,
            "total_cost_usd": self.total_cost_usd,
            "cost_currency": self.cost_currency,
            "total_duration_ms": self.total_duration_ms,
            "links": self.links,
            "is_running": self.is_running,
            "is_correlation": self.is_correlation,
            "workflow_id": self.workflow_id,
        }


class ScanStore:
    """1 ws : N scans 存储：每个 scan 独立 scans/<scan_id>/ 子目录，兼容 legacy ws 根 scan。"""

    def __init__(self, workspaces_dir: Path) -> None:
        self._dir = Path(workspaces_dir)

    # ---- 创建 ----
    def create_scan(self, ws: str, web_url: str, repo_path: str,
                    scan_type: str = "whitebox") -> tuple[str, Path]:
        """在 ws 内建新 scan_id 目录 + session.json（不复位 resume；重扫=新 scan）。

        返回 (scan_id, scan_dir)。scan_id = <repo>-YYYYMMDD-HHMMSS，同秒碰撞 -2/-3。
        """
        ws_dir = self._dir / ws
        scans_dir = ws_dir / "scans"
        scan_id = self._gen_scan_id(scans_dir, repo_path)
        # SessionManager(scans_dir) 复用 create_workspace：建 scans/<scan_id>/session.json。
        # core 零改动；幂等（session.json 已存在则不覆盖），但 scan_id 经碰撞规避保证新。
        mgr = SessionManager(scans_dir)
        scan_dir = mgr.create_workspace(
            web_url=web_url, repo_path=repo_path, name=scan_id, scan_type=scan_type)
        return scan_id, scan_dir

    def _gen_scan_id(self, scans_dir: Path, repo_path: str) -> str:
        """scan_id = <repo>-YYYYMMDD-HHMMSS（仓库名前缀 + 本地时区紧凑秒级）；同秒碰撞 -2/-3...
        仓库名前缀让扫描目录一眼可辨（对齐 legacy NodeGoat_<ts> 可读性），取代纯日期 scan_id。"""
        base = f"{_repo_label(repo_path)}-{_now_local().strftime('%Y%m%d-%H%M%S')}"
        scan_id = base
        i = 2
        while (scans_dir / scan_id / "session.json").exists():
            scan_id = f"{base}-{i}"
            i += 1
        return scan_id

    # ---- 列举（双源）----
    def _scan_entries(self, ws: str) -> list[tuple[str, Path]]:
        """返回 (scan_id, scan_dir) 双源列表，按 created_at 倒序。

        源 ① scans/<id>/session.json（新模型）；源 ② ws 根 session.json（legacy）。
        """
        ws_dir = self._dir / ws
        if not ws_dir.is_dir():
            return []
        entries: list[tuple[str, Path, float]] = []  # (scan_id, scan_dir, created_at_unix)
        # 源 ①
        scans_dir = ws_dir / "scans"
        if scans_dir.is_dir():
            mgr = SessionManager(scans_dir)
            for scan_dir in mgr.list_workspaces():
                created = _to_unix(mgr.get_created_at(scan_dir)) or 0.0
                entries.append((scan_dir.name, scan_dir, created))
        # 源 ② legacy ws 根 session.json
        root_session = ws_dir / "session.json"
        if root_session.exists():
            legacy_id = self._legacy_scan_id(ws_dir)
            root_mgr = SessionManager(ws_dir.parent)
            created = _to_unix(root_mgr.get_created_at(ws_dir)) or 0.0
            entries.append((legacy_id, ws_dir, created))
        entries.sort(key=lambda e: e[2], reverse=True)
        return [(eid, edir) for eid, edir, _ in entries]

    def list_scans(self, ws: str) -> list[ScanSummary]:
        return [self._summarize(ws, scan_dir, scan_id)
                for scan_id, scan_dir in self._scan_entries(ws)]

    def _summarize(self, ws: str, scan_dir: Path, scan_id: str) -> ScanSummary:
        """聚合单个 scan 的摘要。SessionManager 读写只依赖 workspace_path，parent 作
        workspaces_dir 仅供 list/delete（此处不用）。"""
        mgr = SessionManager(scan_dir.parent)
        data = mgr.get_session_data(scan_dir)
        status = _compute_status(scan_dir, mgr.get_status(scan_dir))
        metrics = data.get("metrics", {}) if isinstance(data, dict) else {}
        try:
            vuln_counts = get_workspace_vuln_counts(scan_dir)
        except Exception:
            vuln_counts = {}
        scan_type = mgr.get_scan_type(scan_dir)
        links = data.get("links", {}) if isinstance(data, dict) else {}
        return ScanSummary(
            scan_id=scan_id,
            scan_type=scan_type,
            status=status,
            created_at=_to_unix(mgr.get_created_at(scan_dir)),
            completed_at=_to_unix(mgr.get_completed_at(scan_dir)),
            vuln_count=sum(vuln_counts.values()) if vuln_counts else 0,
            vuln_counts=vuln_counts,
            total_cost_usd=metrics.get("total_cost_usd"),
            cost_currency=metrics.get("cost_currency"),
            total_duration_ms=metrics.get("total_duration_ms"),
            links=links,
            is_running=(status == "running"),
            is_correlation=(scan_type == "correlation"),
            workflow_id=resolve_workflow_id(ws, scan_dir, scan_id),
        )

    def _legacy_scan_id(self, ws_dir: Path) -> str:
        """legacy ws 根 scan 的 scan_id：从 created_at 派生 YYYYMMDD-HHMMSS（本地时区），
        缺失/异常回退 ws 目录名。get_scan_dir/list_scans 双源定位用同一派生口径。"""
        mgr = SessionManager(ws_dir.parent)
        ts = _to_unix(mgr.get_created_at(ws_dir))
        if ts:
            return datetime.fromtimestamp(ts).strftime("%Y%m%d-%H%M%S")
        return ws_dir.name

    # ---- 定位 ----
    def get_scan_dir(self, ws: str, scan_id: str) -> Path | None:
        """按 scan_id 定位 scan 目录（双源）。路径校验：scan_id 不得含分隔符/越界。"""
        if not scan_id or "/" in scan_id or "\\" in scan_id or ".." in scan_id:
            return None
        # 源 ① scans/<scan_id>/
        scan_dir = self._dir / ws / "scans" / scan_id
        if (scan_dir / "session.json").exists():
            return scan_dir
        # 源 ② legacy ws 根（派生 legacy_id 匹配）
        ws_dir = self._dir / ws
        if (ws_dir / "session.json").exists() and self._legacy_scan_id(ws_dir) == scan_id:
            return ws_dir
        return None

    def latest_scan(self, ws: str) -> Path | None:
        """ws 内「最新」scan 目录（spec §5.2 shim 用）：active 优先，否则 max created_at。

        active 优先 = 找一个 status=running（heartbeat fresh，worker 在跑）的 scan；
        多个 running 取最新（_scan_entries 已按 created_at 倒序，首个 running 即最新 running）。
        无 running -> 取最新（entries[0]）。无 scan -> None。

        shim DELETE /api/scan/{ws}（cancel latest/active）依赖此正确性：同 ws 多 scan 时
        应 cancel 正在跑的那个，而非更新的已完成 scan。
        """
        entries = self._scan_entries(ws)
        if not entries:
            return None
        for _scan_id, scan_dir in entries:
            mgr = SessionManager(scan_dir.parent)
            if _compute_status(scan_dir, mgr.get_status(scan_dir)) == "running":
                return scan_dir
        return entries[0][1]
