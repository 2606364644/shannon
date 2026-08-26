# packages/core/src/supernova_core/code_index/llm_collapse.py
"""LLM 轨条目按接口归并（数据层，用户口径 2026-08-26：多参数不拆卡、多接口才拆卡）。

黑盒实证（NodeGoat-20260820）：LLM 轨 agent 同接口每参数一条
（INJ-VULN-01/02/03 = POST /contributions 的 preTax/afterTax/roth）→ 黑盒
add_exploit 每 queue ID 一次 → evidence/报告拆 3 卡；且 agent 输出粒度不稳定
（0826 同洞自己合 1 条）。归并放在 merge activity（SSOT queue 落盘前）——
渲染/速查表/黑盒 evidence 全下游自动跟随，非渲染层呈现归并（根因层修复）。

对照 gn_collapse（GN 轨按 (file:line, sink_func) 折叠、行级分卡）：LLM 轨是
叙事权威，按**接口**整卡归并——同接口多参数 = 同一漏洞单位（一份叙事 +
受影响入口表多行）；不同 sink 行不拆（LLM 叙事不区分行级）。

仅 taint 三类（injection/xss/ssrf）；auth/authz 直通（missing-control 每条
独立漏洞，同接口合并是灾难）。接口信息全无不合并（无 key 不能安全合）。
"""
from __future__ import annotations

import re

from supernova_core.code_index.gn_collapse import extract_endpoint

# file:line 提取——与渲染层 findings_renderer._FILE_LINE_RE 同款（entry 的
# sink_location 列与卡片位置链对齐；正则复制避免 code_index → services 反向
# import 环）。
_FILE_LINE_RE = re.compile(r"[\w./-]+\.[A-Za-z]{1,5}:\d+")
# endpoints/affected_parameters 元素的角色注记（"POST /memos (write)"）——
# key 一致性剥除（"POST /memos (write)" ≙ "POST /memos"）。
_ROLE_NOTE_RE = re.compile(r"\s*\([^)]*\)\s*$")

# 归并仅对 taint 三类生效（见模块 docstring）。
_TAINT_CLASSES = frozenset({"injection", "xss", "ssrf"})


def _strip_role_note(s: str) -> str:
    return _ROLE_NOTE_RE.sub("", s.strip()).strip()


def _norm_endpoint(raw: object) -> str | None:
    """单接口字符串归一：剥角色注记 + extract_endpoint（METHOD /path）。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    return extract_endpoint(_strip_role_note(raw))


def _llm_unit_endpoint(f) -> str | None:
    """LLM 条目的接口 key（None = 不参与归并）。

    回退链：endpoints[0]（T2 新字段，注记剥除）→ endpoint → path 提取 →
    source_endpoint。与 dual_track_merger._finding_key 的 endpoint 链同族
    （本模块不做跨轨、无需 sink 对）。
    """
    eps = getattr(f, "endpoints", None) or []
    for ep in eps:
        norm = _norm_endpoint(ep)
        if norm:
            return norm
    return (
        _norm_endpoint(getattr(f, "endpoint", None))
        or _norm_endpoint(getattr(f, "path", None))
        or _norm_endpoint(getattr(f, "source_endpoint", None))
    )


def _entry_param(f) -> str | None:
    """条目参数名（受影响入口表 parameter 列）。

    回退链：vulnerable_parameter → affected_parameters[0] → source 前缀剥除
    （"req.body.preTax @ …" / "preTax (app/…)" 两种真实形态）。
    """
    vp = getattr(f, "vulnerable_parameter", None)
    if isinstance(vp, str) and vp.strip():
        return _strip_role_note(vp)
    params = getattr(f, "affected_parameters", None) or []
    if params and str(params[0]).strip():
        return _strip_role_note(str(params[0]))
    src = getattr(f, "source", None)
    if isinstance(src, str) and src.strip():
        head = src.split("@", 1)[0].split("(", 1)[0].strip()
        head = head.rsplit(".", 1)[-1].strip() if head else head
        return head or None
    return None


def _entry_sink_location(f) -> str | None:
    """条目 sink 位置（尽力提取 file:line；无则 None，表格空列可接受）。"""
    for field in ("sink_call", "vulnerable_code_location", "path"):
        val = getattr(f, field, None)
        if isinstance(val, str):
            m = _FILE_LINE_RE.search(val)
            if m:
                return m.group(0)
    return None


def collapse_llm_entries(findings: list, vuln_class: str) -> list:
    """LLM 轨同接口多参数条目归并成单条（主条目叙事 + 入口表多行）。

    主条目 = effective_severity 最高者（叙事权威取最高危表述；severity 平
    时保持原顺序首条）。合并字段：affected_entries（每源条目一行，
    chain_id 溯源）/ affected_parameters / endpoints 并集、severity 取高；
    其余叙事字段（title/notes/impact/remediation/dataflow_steps…）取主条目。
    """
    if vuln_class not in _TAINT_CLASSES:
        return findings
    # 延迟 import 打破 code_index → services eager 环（gn_collapse 同模式）。
    from supernova_core.services.severity_rules import (
        SEVERITY_ORDER,
        effective_severity,
    )

    groups: dict[str, list] = {}
    order: list[str] = []
    orphans: list[tuple[int, object]] = []
    for idx, f in enumerate(findings):
        key = _llm_unit_endpoint(f)
        if key is None:
            orphans.append((idx, f))
            continue
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(f)

    if all(len(g) == 1 for g in groups.values()):
        return findings  # 无可归并组，原样返回（零开销快路径）

    def _rank(gf):
        return SEVERITY_ORDER.get(effective_severity(gf), 0)

    merged_by_key: dict[str, object] = {}
    for key in order:
        group = groups[key]
        if len(group) == 1:
            continue
        primary = max(group, key=_rank)  # max 保序：severity 平时取首条
        data = primary.model_dump()
        entries = []
        params: list[str] = []
        eps: list[str] = []
        for f in group:
            param = _entry_param(f)
            entries.append({
                "parameter": param,
                "sink_location": _entry_sink_location(f),
                "chain_id": getattr(f, "ID", None),
                "track": "llm",
            })
            if param and param not in params:
                params.append(param)
            for raw in getattr(f, "endpoints", None) or []:
                norm = _norm_endpoint(raw)
                if norm and norm not in eps:
                    eps.append(norm)
        data["affected_entries"] = entries or None
        data["affected_parameters"] = params or None
        if eps:
            data["endpoints"] = eps
        best = max(_rank(f) for f in group)
        for sev, rank in SEVERITY_ORDER.items():
            if rank == best:
                data["severity"] = sev
                break
        collapsed = type(primary).model_validate(data)
        merged_by_key[key] = collapsed

    # 保持原列表顺序输出（组以首条位置为代表，孤儿条目原位）。
    out: list = []
    seen: set[str] = set()
    for f in findings:
        key = _llm_unit_endpoint(f)
        if key is None or key not in merged_by_key:
            out.append(f)
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(merged_by_key[key])
    return out
