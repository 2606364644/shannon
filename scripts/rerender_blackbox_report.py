#!/usr/bin/env python3
"""一次性重渲染黑盒报告（改 renderer 后就地更新既有 scan，不重跑 exploit agent）。

背景：``comprehensive_security_assessment_report.md`` 两步生成——
  1. ``assemble_report``：``ReportAssembler.assemble`` 把各 ``{vc}_exploitation_evidence.md`` 拼接
  2. ``run_report_agent``：report-executive prompt 原地加执行摘要 + 清理 meta（evidence verbatim 保留）

改 renderer（去「已成功利用」冗余标题 + 利用步骤分点分行）后，重渲染 evidence 即让
evidence 部分更新；report agent 生成的执行摘要头部原地保留。

自包含（黑盒 scan 常复用白盒 queue，本目录可能无 queue 文件）：
  - ``valid_ids`` 取 ``verdicts.json`` 的 ``accepted_ids``
  - ``id_to_title`` 从原 evidence 的 ``### ID: title`` 行提取（保留描述性标题）
  - ``id_to_type`` 按 vc 推断

限制：``verdicts.json`` 只存 accepted verdicts（不含被 L2 拒的原始 raw），故 rejected /
unprocessed section 无法从 verdicts.json 完整重建——脚本只重建 accepted 部分。对仅含
exploited 的 scan（如案例 NodeGoat-20260811-165637~1）完整覆盖。

用法::

    python scripts/rerender_blackbox_report.py <scan_dir>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from supernova_core.collectors.exploit import validate_exploit_verdicts
from supernova_core.renderers.exploit import render_exploit

VULN_CLASSES = ["injection", "xss", "auth", "authz", "ssrf"]

# evidence 条目标题行：`### INJ-VULN-01: 描述性标题` → 提取 id→title（保留标题）。
_HEADING_RE = re.compile(r"^### ([A-Za-z][\w-]*): (.+)$", re.MULTILINE)
# 综合报告里 evidence 区起点：第一个 `# Xxx 漏洞利用报告` / `# Xxx Exploitation Report`。
_EVIDENCE_START_RE = re.compile(r"^# .*?(?:漏洞利用报告|Exploitation Report)", re.MULTILINE)


def _parse_id_to_title(evidence_text: str) -> dict[str, str]:
    """从原 evidence 的 `### ID: title` 行提取 id→title 映射（保留描述性标题）。"""
    return {m.group(1): m.group(2).strip() for m in _HEADING_RE.finditer(evidence_text)}


def rerender_evidence(bb: Path, id_to_title: dict[str, str]) -> list[str]:
    """重渲染各 evidence.md，返回成功重渲染的 vc 列表。

    ``id_to_title`` 从综合报告提取（report agent 补的描述性标题最完整），跨 vc 共享——
    黑盒 scan 本目录的 evidence.md 可能是裸 ID（renderer 无 queue 时拿不到 title）。
    """
    done: list[str] = []
    for vc in VULN_CLASSES:
        verdicts_path = bb / f"{vc}_exploit_verdicts.json"
        if not verdicts_path.exists():
            print(f"[skip] {vc}: 无 {verdicts_path.name}")
            continue
        data = json.loads(verdicts_path.read_text(encoding="utf-8"))
        verdicts = data.get("verdicts", [])
        if not verdicts:
            print(f"[skip] {vc}: verdicts 为空")
            continue

        valid_ids = set(data.get("accepted_ids", []))
        # 剔除 false_positive / out_of_scope_internal：report agent（report-executive）
        # 会移除这些 meta-section，重渲染同步剔除，与执行摘要（「误报已剔除」）保持一致，
        # 避免 evidence 里出现误报条目而摘要却说已剔除的矛盾。
        fp_ids = {v.get("vulnerability_id") for v in verdicts
                  if v.get("status") in ("false_positive", "out_of_scope_internal")}
        verdicts = [v for v in verdicts if v.get("vulnerability_id") not in fp_ids]
        valid_ids -= fp_ids
        evidence_path = bb / f"{vc}_exploitation_evidence.md"
        id_to_type = {vid: vc for vid in valid_ids}

        # valid_ids 自包含（取 accepted_ids），不依赖 queue（黑盒 scan 本目录常无 queue）。
        validation = validate_exploit_verdicts(verdicts, valid_ids)
        md = render_exploit(vc, validation, id_to_type, id_to_title)
        evidence_path.write_text(md, encoding="utf-8")
        print(f"[ok] {vc}: 重渲染 {evidence_path.name} "
              f"({len(validation.accepted)} accepted, {len(validation.rejected)} rejected)")
        done.append(vc)
    return done


def rebuild_report(bb: Path, done: list[str]) -> None:
    """原地替换综合报告的 evidence 部分（保留执行摘要头部）。"""
    report_path = bb / "comprehensive_security_assessment_report.md"
    if not report_path.exists():
        print(f"[warn] 综合报告不存在: {report_path}")
        return
    sections = [bb / f"{vc}_exploitation_evidence.md" for vc in VULN_CLASSES if vc in done]
    sections = [s.read_text(encoding="utf-8") for s in sections if s.exists()]
    if not sections:
        print("[warn] 无 evidence 可拼接")
        return

    new_tail = "\n\n---\n\n".join(sections)
    text = report_path.read_text(encoding="utf-8")
    m = _EVIDENCE_START_RE.search(text)
    if m:
        # 保留执行摘要头部（含尾部 --- 分隔），仅替换 evidence 部分。
        new_report = text[: m.start()] + new_tail + "\n"
        head_note = "执行摘要头部保留"
    else:
        # 退化：无执行摘要头部，整篇用 evidence 拼接。
        new_report = new_tail + "\n"
        head_note = "无执行摘要头部-全文重写"
    report_path.write_text(new_report, encoding="utf-8")
    print(f"[ok] 重装配综合报告: {report_path.name} ({head_note})")


def main(scan_dir: str) -> None:
    scan = Path(scan_dir)
    bb = scan / "deliverables" / "blackbox"
    if not bb.is_dir():
        sys.exit(f"未找到黑盒 deliverables 目录: {bb}")
    # id_to_title 从综合报告提取（覆盖前读）：report agent 补的描述性标题最完整，
    # 跨 vc 共享。须在 rebuild_report 覆盖综合报告之前读取。
    report_path = bb / "comprehensive_security_assessment_report.md"
    id_to_title: dict[str, str] = {}
    if report_path.exists():
        id_to_title = _parse_id_to_title(report_path.read_text(encoding="utf-8"))
        print(f"[info] 从综合报告提取 {len(id_to_title)} 个标题")
    done = rerender_evidence(bb, id_to_title)
    rebuild_report(bb, done)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("用法: python scripts/rerender_blackbox_report.py <scan_dir>")
    main(sys.argv[1])
