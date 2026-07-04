from __future__ import annotations

import json
from pathlib import Path

from shannon_core.utils.paths import WHITEBOX_SUBDIR, resolve_track_deliverable

BIG_JSON_THRESHOLD = 50_000
_EXCLUDE_DIRS = {".git", "__pycache__", "schemas"}


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

    def __init__(self, workspace_path: Path) -> None:
        self._ws = Path(workspace_path)
        self._deliverables = self._ws / "deliverables"

    def _iter_files(self):
        """扫 deliverables 所有文件,排除 .git/__pycache__/schemas(任意深度)。"""
        if not self._deliverables.exists():
            return
        for f in sorted(self._deliverables.rglob("*")):
            if not f.is_file():
                continue
            if any(part in _EXCLUDE_DIRS for part in f.parts):
                continue
            yield f

    def _infer_track(self, default: str = WHITEBOX_SUBDIR) -> str:
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
                "path": str(f.relative_to(self._deliverables)),  # 含 track 前缀:whitebox/xss.json
                "size": f.stat().st_size,
                "kind": _classify(f),
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
        """跨 *_exploitation_queue.json 聚合 vulnerabilities(空/无效跳过)。"""
        out = []
        for f in self._iter_files():
            if not f.name.endswith("_exploitation_queue.json"):
                continue
            if not _is_valid_queue_file(f):
                continue
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

    def read(self, filename: str, track: str = WHITEBOX_SUBDIR) -> dict | list | str:
        p = resolve_track_deliverable(self._deliverables, track, filename)
        if not p.exists():
            raise FileNotFoundError(filename)
        text = p.read_text("utf-8")
        if p.suffix == ".json":
            return json.loads(text) if text.strip() else []
        return text

    def read_log(self, name: str = "workflow.log") -> str:
        p = self._ws / name
        if not p.exists():
            p = self._ws / "agents" / name
        if not p.exists():
            raise FileNotFoundError(name)
        return p.read_text("utf-8")
