"""双轨呈现一致性编排（spec 2026-08-26 §6）。

GitNexus 轨侧的轻量 LLM 补全层（同 chain_verdict 轻量判定 / llm-discovered
sink 模式——确定性兜底 + 可选 LLM 增强；不触碰「确定性产物不喂 LLM 轨 vuln
agent prompt」铁律，输入是 GN 自己的产物）：

1. **配对归并**（§6.1）：确定性 key 配不上的同洞卡（sink 粒度/称谓不同、跨
   接口存储型链各见半条），每 class 一次 LLM 批量比对，仅 high 置信对应用
   合并（复用 merger both 分支字段融合）。
2. **GN-only 卡补全**（§6.2）：配对后仍 gitnexus-only 的卡逐卡单次结构化输出，
   补 title/notes/impact/remediation/cvss/owasp_category/severity 校准——写
   BaseVulnerability 现成字段，零 schema 改动；不补 dataflow_steps（轻量单次
   不读码，编造中间节点有风险）。

LLM 不可用（raise / 超时 / 输出不可解析）优雅退化：维持确定性 merge 结果，
不阻塞报告（渲染层确定性文案兜底路径已存在）。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Awaitable, Callable

from supernova_core.code_index.dual_track_merger import (
    apply_pairing_merge,
    build_pairing_prompt,
    parse_pairing_response,
)
from supernova_core.i18n import current_lang
from supernova_core.models.queue_schemas import Vulnerability

logger = logging.getLogger(__name__)

LlmClient = Callable[..., Awaitable[str]]

_SEVERITIES = frozenset({"critical", "high", "medium", "low"})
# 补全输出 JSON Schema（output_format 透传 run_claude_prompt → CLI --json-schema，
# 对齐 chain_verdict 的 structured_output 根因治本口径）
COMPLETION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "叙事标题"},
        "notes": {"type": "string", "description": "成因 2-3 句"},
        "impact": {"type": "string", "description": "危害一句话"},
        "remediation": {"type": "string", "description": "代码级修复"},
        "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
        "cvss": {"type": "string", "description": "CVSS 向量+估分；不确定省略"},
        "owasp_category": {"type": "string", "description": "OWASP 分类；不确定省略"},
    },
    "required": ["title", "impact", "remediation"],
}

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def build_completion_prompt(vuln: Vulnerability, vuln_class: str) -> str:
    """GN-only 卡补全 prompt：输入 GN 确定性素材（参数/sink/位置/链），输出叙事
    与评级字段。轻量单次不读码——只依据给定素材归纳，不编造中间节点。"""
    sink = (getattr(vuln, "sink_function", None)
            or getattr(vuln, "sink_call", None) or "-")
    loc = getattr(vuln, "vulnerable_code_location", None) or "-"
    entries = getattr(vuln, "affected_entries", None) or []
    params = ", ".join(
        str(e.get("parameter")) for e in entries if isinstance(e, dict)
        and e.get("parameter")) or (getattr(vuln, "source", None) or "-")
    chain = getattr(vuln, "evidence_chain", None) or getattr(vuln, "path", None) or "-"
    zh = current_lang() == "zh"
    lang_directive = ("全部叙述字段用简体中文撰写（漏洞类型/参数/路径/端点/函数名"
                      "保留英文）。" if zh else "Write all narrative fields in English. ")
    return f"""You are completing a vulnerability report card. A static taint-chain
analysis (class: {vuln_class}) produced the deterministic facts below; write the
reader-facing narrative fields for the report card.

Facts (deterministic, from static analysis):
- vulnerability class: {vuln_class}
- parameter(s): {params}
- sink: {sink}
- location: {loc}
- taint chain: {chain}

Output STRICT JSON only (no markdown fence, no prose):
{{"title": "...", "notes": "...", "impact": "...", "remediation": "...",
  "severity": "critical|high|medium|low",
  "cvss": "..." (omit if unsure), "owasp_category": "..." (omit if unsure)}}

Rules:
- {lang_directive}Base every statement ONLY on the facts above; do NOT invent
  intermediate code steps, files, or functions not present in the facts.
- title: one sentence naming the flaw + where (param / sink / endpoint).
- notes: 2-3 sentences on how the parameter reaches the sink unvalidated.
- impact: what an attacker gains, conclusion first, <=3 sentences.
- remediation: code-level specific (which call to replace with what), no
  boilerplate like "validate input".
- severity: judge from actual impact, not always critical.
- cvss / owasp_category: omit when unsure — never fabricate."""


def parse_completion_response(raw: object) -> dict | None:
    """解析补全输出：容忍 fence/杂文；title/impact/remediation 任一缺失视为
    无效（补全核心价值是叙事），severity 非法剔除，None/空字段剔除。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    m = _JSON_OBJ_RE.search(raw.strip())
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    fields: dict = {}
    for key in ("title", "notes", "impact", "remediation", "cvss", "owasp_category"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            fields[key] = v.strip()
    if not all(k in fields for k in ("title", "impact", "remediation")):
        return None
    sev = str(data.get("severity") or "").strip().lower()
    if sev in _SEVERITIES:
        fields["severity"] = sev
    return fields


def apply_completion(vuln: Vulnerability, fields: dict) -> Vulnerability:
    """补全字段写回卡（None/空已在 parse 剔除；未产出键不覆盖）。"""
    data = vuln.model_dump()
    data.update(fields)
    return type(vuln).model_validate(data)


async def enhance_track_parity(
    merged: list[Vulnerability],
    vuln_class: str,
    llm_client: LlmClient,
) -> list[Vulnerability]:
    """merge activity 内的编排入口（确定性 merge 之后、落盘之前）。

    配对（每 class 一次）→ 补全（剩余 gn-only 逐卡一次）。任一 LLM 失败/
    不可解析都优雅退化（log + 保持现状），绝不抛出——报告管线不因增强层
    阻塞（spec §6 退化口径）。单侧空时零调用（成本守门）。"""
    llm_only = [f for f in merged if f.merge_source == "llm-only"]
    gn_only = [f for f in merged if f.merge_source == "gitnexus-only"]

    if llm_only and gn_only:
        try:
            raw = await llm_client(
                build_pairing_prompt(llm_only, gn_only))
            pairs = parse_pairing_response(
                raw,
                valid_gn_ids={f.ID for f in gn_only},
                valid_llm_ids={f.ID for f in llm_only},
            )
            if pairs:
                merged = apply_pairing_merge(merged, pairs)
        except Exception as exc:  # noqa: BLE001 — 增强层不阻塞
            logger.warning("track-parity pairing skipped (LLM unavailable): %s", exc)

    for i, f in enumerate(merged):
        if f.merge_source != "gitnexus-only":
            continue
        try:
            raw = await llm_client(build_completion_prompt(f, vuln_class))
        except Exception as exc:  # noqa: BLE001 — 增强层不阻塞
            logger.warning(
                "track-parity completion skipped for %s (LLM unavailable): %s",
                f.ID, exc)
            break  # client 已证不可用，剩余卡不再尝试
        fields = parse_completion_response(raw)
        if fields:
            merged[i] = apply_completion(f, fields)
            logger.info("track-parity: completed card %s", f.ID)
    return merged
