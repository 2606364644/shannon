"""dismissed_findings.json 留档（spec 2026-08-27 §4）——白盒两轨非漏洞判定留档。

口径（用户 2026-08-27 锁定）：
- 白盒判非漏洞（GN chain_verdict not_vulnerable / LLM 轨探索排除）→ 留档，
  不进报告（GN queue 不含、SSOT 天然干净）。
- 白盒拿不准（needs_review / unadjudicated）→ 保守进 queue / 报告。
- 黑盒验证失败进黑盒报告（带步骤+原因），不进本留档。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def split_dismissed(findings, *, vuln_class: str):
    """GN builder 产物分流：(queue_findings, dismissed_entries)。

    判非漏洞卡（verdict="safe"，chain_verdict 值域：safe|vulnerable）→
    dismissed entry dict（人工分析留档，不进 queue）；其余（vulnerable /
    needs_review / unadjudicated / verdict 缺失）保守进 queue——「没判成 ≠
    非漏洞」。duck-typing 读卡字段（inj/xss/ssrf/second_order 模型各异，
    字段名一致）。
    """
    queue_findings = []
    dismissed_entries = []
    for f in findings:
        if getattr(f, "verdict", None) == "safe":
            dismissed_entries.append({
                "ID": getattr(f, "ID", ""),
                "source_track": "gitnexus",
                "vuln_class": vuln_class,
                "title": getattr(f, "title", None),
                "dismiss_reason": (getattr(f, "mismatch_reason", None)
                                   or "judged safe by chain verdict"),
                "evidence": getattr(f, "evidence_chain", None),
                "confidence": getattr(f, "confidence", None),
                "source": getattr(f, "source", None),
                "sink_call": getattr(f, "sink_call", None),
                "dismissed_at_stage": "chain-verdict",
            })
        else:
            queue_findings.append(f)
    return queue_findings, dismissed_entries


def append_dismissed(path: Path, entries: list[dict]) -> None:
    """读-合并-原子写 dismissed_findings.json（同 ID 后写覆盖）。

    空 entries 不写文件（不留垃圾）；已有文件损坏 → 覆盖重写（best-effort）。
    """
    if not entries:
        return
    existing: list[dict] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("dismissed"), list):
                existing = [e for e in data["dismissed"] if isinstance(e, dict)]
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("dismissed archive %s unreadable (%s); rewriting", path, exc)
    by_id = {str(e.get("ID", "")): e for e in existing}
    order = [str(e.get("ID", "")) for e in existing]
    for e in entries:
        eid = str(e.get("ID", ""))
        if eid not in by_id:
            order.append(eid)
        by_id[eid] = e  # 同 ID 后写覆盖（dedupe_by_id 模式）
    from supernova_core.utils.atomic_write import atomic_write_json
    atomic_write_json(Path(path), {"dismissed": [by_id[i] for i in order if i in by_id]})
