"""General dual-track merger for vulnerability queues.

The verdict mode merges LLM-track and GitNexus-track findings into the
exploitation queue consumed by reporting. The intel mode is a best-effort
dangerous-side merge for future recon wiring.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from supernova_core.code_index.gn_collapse import (
    collapse_gn_entries,
    extract_endpoint,
    parse_sink_call_site_id,
)
from supernova_core.models.queue_schemas import Vulnerability
from supernova_core.services.severity_rules import effective_severity, max_severity

logger = logging.getLogger(__name__)

_LOCATION_FIELDS = (
    "source",
    "endpoint",
    "source_endpoint",
    "vulnerable_code_location",
    "path",
)
_SINK_FIELDS = ("sink_call", "sink_function", "vulnerable_parameter")
_DANGER_KEYWORDS = ("none", "missing", "no ", "auto-generated", "absent")


def _normalize_endpoint(endpoint: object) -> str | None:
    """Normalize an HTTP endpoint string for cross-track dedup.

    Uppercases the method and strips the query string + trailing slash, so the
    two tracks' phrasings of the same IDOR endpoint collapse to one key. Returns
    None when the endpoint is absent/blank (caller falls back to the strict key).
    """
    if not isinstance(endpoint, str):
        return None
    endpoint = endpoint.strip()
    if not endpoint:
        return None
    parts = endpoint.split(None, 1)
    if len(parts) == 2:
        method, raw_path = parts[0].upper(), parts[1]
    else:
        method, raw_path = "", parts[0]
    path = raw_path.split("?", 1)[0].rstrip("/") or "/"
    return f"{method} {path}".strip()


def _normalize_sink_func(name: object) -> str | None:
    """Sink 函数名归一化（F4）：小写 + 剥调用括号与 receiver 限定
    （`cursor.execute(sql)` → `execute`、`Cursor.Execute` → `execute`），
    使 LLM 轨带 receiver 的 sink 与 GN sink_call id 解析出的裸函数名
    落同一把 key。无括号无点的自然语言 sink（如 "eval at file:32"）
    原样小写保留（与现状同粒度，不劣化）。"""
    if not isinstance(name, str) or not name.strip():
        return None
    s = name.strip().lower().split("(", 1)[0].rsplit(".", 1)[-1]
    return s.strip() or None


def _strict_key(finding: Vulnerability) -> tuple:
    """Legacy strict key: full location + sink field tuples (exact-match)."""
    loc = tuple(getattr(finding, field, None) for field in _LOCATION_FIELDS)
    sink = tuple(getattr(finding, field, None) for field in _SINK_FIELDS)
    return (getattr(finding, "vulnerability_type", None), loc, sink)


def _finding_key(finding: Vulnerability) -> tuple:
    """Build a cross-track dedup key (spec 2026-08-25 §3.3: 漏洞单位).

    Horizontal authz (IDOR) is deduped by normalized endpoint ALONE: the two
    tracks describe the same IDOR with different code locations (LLM points at
    the service layer; GitNexus at controller+service), so a location-bearing key
    would never collapse them. If a Horizontal finding lacks an endpoint, fall
    back to the strict key (cannot endpoint-dedup blindly).

    Other classes use the unit key — (vuln_class, normalized endpoint, sink
    function) — replacing the old whole-chain exact match: the two tracks phrase
    the same unit differently (LLM narrates a chain; GitNexus emits one entry
    per param×line), so exact keys never collapse them. The sink function comes
    from `sink_function`, or is parsed out of a GitNexus `sink_call` id; a
    natural-language LLM sink ("eval at file:32") is used whole. When the unit
    identity is incomplete (no endpoint AND/OR no sink — e.g. authz classes,
    route-less chains), fall back to the strict key: keying everything of a
    class under (vtype, None, None) would blindly merge distinct findings.
    """
    vtype = getattr(finding, "vulnerability_type", None)
    if vtype == "Horizontal":
        norm = _normalize_endpoint(getattr(finding, "endpoint", None))
        if norm:
            return ("Horizontal", norm)
        return _strict_key(finding)
    endpoint = (
        extract_endpoint(getattr(finding, "endpoint", None))
        or extract_endpoint(getattr(finding, "path", None))
        or _normalize_endpoint(getattr(finding, "source_endpoint", None))
        or _normalize_endpoint(getattr(finding, "endpoint", None))
    )
    raw_sink = getattr(finding, "sink_function", None) or getattr(finding, "sink_call", None)
    sink_func, _loc = parse_sink_call_site_id(raw_sink or "")
    if not sink_func and isinstance(raw_sink, str) and raw_sink.strip():
        sink_func = raw_sink.strip()  # LLM 自然语言 sink（如 "eval at file:32"）整体作 key
    sink_func = _normalize_sink_func(sink_func)
    if endpoint and sink_func:
        return (vtype, endpoint, sink_func)
    return _strict_key(finding)


def _is_vulnerable(finding: Vulnerability) -> bool:
    verdict = getattr(finding, "verdict", None)
    if verdict is not None:
        return str(verdict).strip().lower() == "vulnerable"
    return bool(getattr(finding, "externally_exploitable", False))


_AUTHZ_VULN_TYPES = frozenset({"Horizontal", "Vertical", "Context_Workflow"})


def _authz_ee_or(llm: Vulnerability, gitnexus: Vulnerability) -> bool | None:
    """both 分支 ee OR, 仅限 authz 三类。

    authz 的 externally_exploitable 是 LLM 主观判 (prompt 未定义语义时易误判为
    "需登录 → 不可外部利用 → False"); 用 GitNexus 轨 OR 兜底纠错。非 authz 类返回
    None → 调用方不覆写, 保持 base ee (inj/xss/ssrf 跨服务可达性是客观事实, base
    权威, 不该被 OR 翻转 — 守 test_both_track_vulnerable_keeps_reachability_from_llm_base)。
    """
    if getattr(llm, "vulnerability_type", None) in _AUTHZ_VULN_TYPES:
        return bool(getattr(llm, "externally_exploitable", False)) or bool(
            getattr(gitnexus, "externally_exploitable", False)
        )
    return None


def _single_track_confidence(finding: Vulnerability) -> str:
    """单轨分支 confidence：GitNexus 轨 chain_verdict 判定通道失败（unadjudicated）
    显式保留（spec 2026-08-26 §5.7——不静默混入 needs_review 待复核，两种语义）；
    其余单轨卡照旧 needs_review。"""
    if str(getattr(finding, "confidence", None) or "").strip().lower() == "unadjudicated":
        return "unadjudicated"
    return "needs_review"


def _clone_with_merge_fields(
    finding: Vulnerability,
    *,
    merge_source: str,
    confidence: str,
    vulnerable: bool,
    evidence_chain: str | None = None,
    externally_exploitable_override: bool | None = None,
    other_severity: str | None = None,
    other_entries: list[dict] | None = None,
    other_params: list[str] | None = None,
    other_endpoint: str | None = None,
) -> Vulnerability:
    data = finding.model_dump()
    data["merge_source"] = merge_source
    data["confidence"] = confidence
    # Spec 改动 3′: externally_exploitable is a reachability tag (true =
    # public-internet; false = internal / cross-service), NOT part of the verdict
    # — do NOT recompute it from the verdict-OR. The both/llm-only branches use an
    # LLM-track finding as base (reachability authority); the gitnexus-only branch
    # uses the GitNexus finding. The `vulnerable` param still drives the `verdict`
    # rewrite below.
    # Spec 2026-08-04 改动 B (per-class): authz 三类 both 分支取 ee OR (LLM 主观判
    # 需 GitNexus 兜底, 见 _authz_ee_or); 非 authz 类不传 override → 保持 base。
    if externally_exploitable_override is not None:
        data["externally_exploitable"] = externally_exploitable_override
    if evidence_chain and not data.get("evidence_chain"):
        data["evidence_chain"] = evidence_chain
    # Spec 2026-08-25 §3.3 both 分支合并策略：severity 取高、受影响入口/参数并集、
    # endpoint base 缺失时补 other 侧。other_* 仅 both 分支传值（单轨分支默认 None，
    # 行为不变）。severity 两侧先经 effective_severity() 化再比较——max_severity
    # (None, "low") 会返回 None 丢档（T1 遗留 Minor），预归一规避之。
    if merge_source == "both":
        data["severity"] = max_severity(effective_severity(finding), other_severity)
        merged_entries = list(data.get("affected_entries") or [])
        seen = {(e.get("parameter"), e.get("sink_location")) for e in merged_entries}
        for e in other_entries or []:
            k = (e.get("parameter"), e.get("sink_location"))
            if k not in seen:
                merged_entries.append(e)
                seen.add(k)
        data["affected_entries"] = merged_entries or None
        params = list(data.get("affected_parameters") or [])
        for p in other_params or []:
            if p not in params:
                params.append(p)
        data["affected_parameters"] = params or None
        data["endpoint"] = data.get("endpoint") or other_endpoint
    if data.get("verdict") is not None:
        data["verdict"] = "vulnerable" if vulnerable else "safe"
    return type(finding).model_validate(data)


def merge_dual_track_queues(
    llm_findings: list[Vulnerability],
    gitnexus_findings: list[Vulnerability],
    *,
    mode: str = "verdict",
) -> list[Vulnerability]:
    """Merge LLM-track and GitNexus-track findings.

    In verdict mode, the output is the deduped union of both tracks. Findings
    present in both tracks get `merge_source="both"` and `confidence="high"`;
    single-track findings get `needs_review`. Vulnerability status is an OR:
    any vulnerable track makes the merged finding vulnerable.

    GitNexus findings are first collapsed per unit (spec 2026-08-25 §3,
    `collapse_gn_entries`): the param×line cartesian entries of one unit fold
    into a primary record + affected_entries list, so the merger sees units —
    not raw chains — and cross-track dedup keys line up.
    """
    gitnexus_findings = collapse_gn_entries(gitnexus_findings)
    if mode == "intel":
        return _merge_intel(llm_findings, gitnexus_findings)
    if mode != "verdict":
        raise ValueError(f"unsupported dual-track merge mode: {mode}")

    llm_by_key: dict[tuple, Vulnerability] = {}
    for finding in llm_findings:
        llm_by_key.setdefault(_finding_key(finding), finding)

    gitnexus_by_key: dict[tuple, Vulnerability] = {}
    for finding in gitnexus_findings:
        gitnexus_by_key.setdefault(_finding_key(finding), finding)

    ordered_keys = list(llm_by_key)
    ordered_keys.extend(key for key in gitnexus_by_key if key not in llm_by_key)

    merged: list[Vulnerability] = []
    for key in ordered_keys:
        llm = llm_by_key.get(key)
        gitnexus = gitnexus_by_key.get(key)

        if llm is not None and gitnexus is not None:
            vulnerable = _is_vulnerable(llm) or _is_vulnerable(gitnexus)
            evidence_chain = getattr(llm, "evidence_chain", None) or getattr(
                gitnexus, "evidence_chain", None
            )
            merged.append(
                _clone_with_merge_fields(
                    llm,
                    merge_source="both",
                    confidence="high",
                    vulnerable=vulnerable,
                    evidence_chain=evidence_chain,
                    externally_exploitable_override=_authz_ee_or(llm, gitnexus),
                    other_severity=effective_severity(gitnexus),
                    other_entries=getattr(gitnexus, "affected_entries", None),
                    other_params=getattr(gitnexus, "affected_parameters", None),
                    other_endpoint=getattr(gitnexus, "endpoint", None),
                )
            )
        elif llm is not None:
            merged.append(
                _clone_with_merge_fields(
                    llm,
                    merge_source="llm-only",
                    confidence="needs_review",
                    vulnerable=_is_vulnerable(llm),
                )
            )
        elif gitnexus is not None:
            # 防线（spec 2026-08-27 §4）：GN-only verdict="safe"（判非漏洞）卡
            # 不进 union——非漏洞卡不进报告 / SSOT / 黑盒输入。主修复在
            # activity 写入侧分流（split_dismissed + dismissed_findings.json
            # 留档）；此处兜 GN queue 文件被直接喂入的回归。both 配对卡不
            # 踢（verdict OR 语义不变，分歧信息保留）。
            if getattr(gitnexus, "verdict", None) == "safe":
                logger.info(
                    "dual-track merge: drop gitnexus-only safe finding %s "
                    "(judged not vulnerable; activity-side split should have "
                    "archived it)", getattr(gitnexus, "ID", "?"))
                continue
            merged.append(
                _clone_with_merge_fields(
                    gitnexus,
                    merge_source="gitnexus-only",
                    confidence=_single_track_confidence(gitnexus),
                    vulnerable=_is_vulnerable(gitnexus),
                )
            )

    logger.info(
        "dual-track merge: %d llm + %d gitnexus -> %d merged",
        len(llm_findings),
        len(gitnexus_findings),
        len(merged),
    )
    return merged


def _has_danger(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(keyword in lowered for keyword in _DANGER_KEYWORDS)


# --- LLM 辅助跨轨配对归并（spec 2026-08-26 §6.1 + §5.1 ①归并终审）---
# 确定性 _finding_key 配不上的同洞卡（sink 粒度/称谓不同、跨接口存储型链各见半条），
# 由轻量 LLM 单次比对配对；仅 high 置信对应用——误合并（两个不同的洞合成一张卡、
# 叙事互相污染）比漏合并更伤报告可信度，保守优先。
# 配对结果两种形态（§5.1）：mode=merge 并成 both（字段融合，现行为）；mode=attach
# 挂靠——LLM 卡为主体，GN 卡 ID 写入主体卡 merged_from、不再独立出现（呈现层同洞
# 合并，不改主体判定字段——spec §8「不改双轨判定结果」）。

@dataclass(frozen=True)
class TrackPair:
    """一条跨轨配对建议（LLM 输出，经 ID/置信度校验后存活）。"""
    gn_id: str
    llm_id: str
    confidence: str  # high | medium | low
    reason: str | None = None
    mode: str = "merge"   # merge=并成 both | attach=挂靠 merged_from


def _pairing_summary_line(f: Vulnerability) -> str:
    """单卡一行摘要：ID / title / 接口 / sink / 参数。"""
    endpoint = (getattr(f, "endpoint", None)
                or extract_endpoint(getattr(f, "path", None))
                or getattr(f, "source_endpoint", None) or "-")
    sink = (getattr(f, "sink_function", None) or getattr(f, "sink_call", None)
            or getattr(f, "vulnerable_parameter", None) or "-")
    params = getattr(f, "affected_parameters", None) or []
    if not params:
        src = getattr(f, "source", None)
        params = [src.split("(", 1)[0].strip()] if isinstance(src, str) and src else []
    return (f"- {f.ID} | title={getattr(f, 'title', None) or '-'} | "
            f"endpoint={endpoint} | sink={sink} | params={','.join(map(str, params)) or '-'}")


def build_pairing_prompt(llm_findings: list[Vulnerability],
                         gitnexus_findings: list[Vulnerability]) -> str:
    """每 class 一次的批量配对 prompt：双列摘要 → JSON 输出契约（两种形态）。

    输出每对带 mode："merge"（同 param→同 sink 单位，并成 both 字段融合）或
    "attach"（同洞但确定性细节对不上——粒度/文件/端点失配，LLM 卡为主体、GN 卡
    ID 挂靠 merged_from、不再独立出现）。
    """
    llm_lines = "\n".join(_pairing_summary_line(f) for f in llm_findings) or "- (none)"
    gn_lines = "\n".join(_pairing_summary_line(f) for f in gitnexus_findings) or "- (none)"
    return f"""Two vulnerability lists below describe findings from independent analysis tracks
of the SAME codebase. Some entries describe the SAME underlying vulnerability
(e.g. a stored XSS where one track names the template-expression sink and the
other the template render function; a chain seen from its write endpoint vs its
trigger endpoint; the LLM card recording the sink at memos.html:31 while the
GitNexus chain has it at memos.js:27). Match each such pair and choose a mode:

- "merge": the two cards clearly describe the same param -> same sink unit —
  fuse the GitNexus card into the LLM card (dual-track confirmed, fields merged).
- "attach": same underlying hole but the deterministic details do not line up
  (different granularity, file, or endpoint of one stored/reflected flow) —
  keep the LLM card as the primary and attach the GitNexus card by ID into its
  `merged_from` list (the GitNexus card stops appearing on its own; NO field
  fusion, NO verdict/severity change on the primary card).

LLM-track findings:
{llm_lines}

GitNexus-track findings:
{gn_lines}

Output STRICT JSON only (no markdown fence, no prose):
{{"merge": [{{"llm_id": "<LLM ID>", "gn_id": "<GitNexus ID>",
             "mode": "merge|attach", "confidence": "high|medium|low",
             "reason": "<one line>"}}]}}

Rules:
- Only pair entries that are the SAME vulnerability (same param flowing to the
  same dangerous sink location, even if named at different granularity or via
  different endpoints of one stored/reflected flow).
- Prefer "merge" when param and sink location clearly correspond; use "attach"
  when it is the same hole but the recorded locations/endpoints do not line up.
- "high" ONLY when param and sink location clearly correspond; when unsure use
  "medium"/"low" or omit the pair entirely.
- Different vulnerabilities must NOT be paired."""


_PAIR_CONFIDENCES = frozenset({"high", "medium", "low"})
_PAIR_MODES = frozenset({"merge", "attach"})
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_pairing_response(raw: object, valid_gn_ids: set[str],
                           valid_llm_ids: set[str]) -> list[TrackPair]:
    """解析 LLM 配对输出：容忍 markdown fence / 前后杂文；幻觉 ID、非法置信度、
    非法 mode 整对丢弃；非法 JSON → []（调用方按无配对处理）。

    两种输入形态：新 ``{"merge": [{llm_id, gn_id, mode, confidence}]}``（§5.1
    归并终审）与旧 ``{"pairs": [{gn_id, llm_id, confidence}]}``（mode 缺省 merge，
    向后兼容）。"""
    if not isinstance(raw, str) or not raw.strip():
        return []
    import json
    text = raw.strip()
    m = _JSON_OBJ_RE.search(text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    entries = list(data.get("merge") or []) + list(data.get("pairs") or [])
    pairs: list[TrackPair] = []
    for p in entries:
        if not isinstance(p, dict):
            continue
        gn_id, llm_id = str(p.get("gn_id") or ""), str(p.get("llm_id") or "")
        conf = str(p.get("confidence") or "").strip().lower()
        mode = str(p.get("mode") or "merge").strip().lower()
        if gn_id not in valid_gn_ids or llm_id not in valid_llm_ids:
            continue
        if conf not in _PAIR_CONFIDENCES:
            continue
        if mode not in _PAIR_MODES:
            continue
        reason = p.get("reason")
        pairs.append(TrackPair(gn_id, llm_id, conf,
                               str(reason) if isinstance(reason, str) else None,
                               mode))
    return pairs


def _attach_merged_from(subject: Vulnerability, gn_id: str) -> Vulnerability:
    """attach 挂靠：主体卡 ``merged_from`` 追加 GN 卡 ID（去重），其余字段不动。"""
    data = subject.model_dump()
    merged_from = list(data.get("merged_from") or [])
    if gn_id not in merged_from:
        merged_from.append(gn_id)
    data["merged_from"] = merged_from
    return type(subject).model_validate(data)


def apply_pairing_merge(merged: list[Vulnerability],
                        pairs: list[TrackPair]) -> list[Vulnerability]:
    """应用 high 置信配对，两种形态（spec 2026-08-26 §5.1）：

    - ``mode="merge"``：GN 卡并入对应 LLM 卡成 both（复用 both 分支字段融合：
      entries 并集 / severity 取高 / evidence_chain 兜底 / verdict OR），GN 独立卡
      移除——现行为。
    - ``mode="attach"``：LLM 卡为主体，GN 卡 ID 挂靠其 ``merged_from``，GN 独立卡
      移除；主体卡 merge_source/confidence/verdict/severity 全不变（呈现层同洞
      合并，不改双轨判定结果——spec §8）。

    中低置信与找不到卡的配对跳过；同一 GN 卡只消费一次；同一 LLM 主体多对挂靠
    时 merged_from 累积。"""
    by_id = {f.ID: f for f in merged}
    consumed_gn: set[str] = set()
    replacements: dict[str, Vulnerability] = {}
    for pair in pairs:
        if pair.confidence != "high":
            continue
        llm = by_id.get(pair.llm_id)
        gn = by_id.get(pair.gn_id)
        if llm is None or gn is None or pair.gn_id in consumed_gn:
            continue
        base = replacements.get(pair.llm_id, llm)
        if pair.mode == "attach":
            replacements[pair.llm_id] = _attach_merged_from(base, pair.gn_id)
        else:
            replacements[pair.llm_id] = _clone_with_merge_fields(
                base,
                merge_source="both",
                confidence="high",
                vulnerable=_is_vulnerable(base) or _is_vulnerable(gn),
                evidence_chain=getattr(base, "evidence_chain", None)
                or getattr(gn, "evidence_chain", None),
                externally_exploitable_override=_authz_ee_or(base, gn),
                other_severity=effective_severity(gn),
                other_entries=getattr(gn, "affected_entries", None),
                other_params=getattr(gn, "affected_parameters", None),
                other_endpoint=getattr(gn, "endpoint", None),
            )
        consumed_gn.add(pair.gn_id)
    if not consumed_gn:
        return merged
    logger.info("pairing merge: %d high-confidence pair(s) applied", len(consumed_gn))
    return [replacements.get(f.ID, f) for f in merged
            if f.ID not in consumed_gn]


def _merge_intel(
    llm_findings: list[Vulnerability],
    gitnexus_findings: list[Vulnerability],
) -> list[Vulnerability]:
    """Best-effort dangerous-side merge for future recon/intel wiring."""
    llm_by_key = {_finding_key(finding): finding for finding in llm_findings}
    gitnexus_by_key = {_finding_key(finding): finding for finding in gitnexus_findings}

    ordered_keys = list(llm_by_key)
    ordered_keys.extend(key for key in gitnexus_by_key if key not in llm_by_key)

    merged: list[Vulnerability] = []
    for key in ordered_keys:
        llm = llm_by_key.get(key)
        gitnexus = gitnexus_by_key.get(key)
        if llm is not None and gitnexus is not None:
            data = llm.model_dump()
            data["merge_source"] = "both"
            data["confidence"] = "high"
            for field in ("notes", "role_context", "guard_evidence", "missing_defense"):
                gitnexus_value = getattr(gitnexus, field, None)
                if _has_danger(gitnexus_value) and not _has_danger(data.get(field)):
                    data[field] = gitnexus_value
            merged.append(type(llm).model_validate(data))
        elif llm is not None:
            merged.append(
                _clone_with_merge_fields(
                    llm,
                    merge_source="llm-only",
                    confidence="needs_review",
                    vulnerable=_is_vulnerable(llm),
                )
            )
        elif gitnexus is not None:
            merged.append(
                _clone_with_merge_fields(
                    gitnexus,
                    merge_source="gitnexus-only",
                    confidence="needs_review",
                    vulnerable=_is_vulnerable(gitnexus),
                )
            )
    return merged


def _chain_endpoint_sequence(chain: dict) -> tuple:
    """Dedup key: ordered tuple of step endpoints (normalized lower)."""
    steps = chain.get("steps", [])
    return tuple((s.get("endpoint", "") or "").strip().lower() for s in steps) + (chain.get("vuln_type", ""),)


def merge_attack_chains(
    llm_chains: list[dict],
    gitnexus_chains: list[dict],
) -> list[dict]:
    """Merge LLM-track and GitNexus-track attack chains by endpoint-sequence dedup.

    Unlike merge_dual_track_queues (which dedups Vulnerability by location+sink),
    attack chains dedup by the ordered endpoint sequence + vuln_type. merge_source:
    both / llm-only / gitnexus-only. When both tracks have the same chain, the
    GitNexus (evidence-driven) confidence wins if higher; the LLM (creative) fills
    coverage GitNexus misses.
    """
    by_seq: dict[tuple, dict] = {}

    for chain in llm_chains:
        chain = dict(chain)
        chain.pop("_source", None)
        seq = _chain_endpoint_sequence(chain)
        chain["merge_source"] = "llm-only"
        by_seq[seq] = chain

    for chain in gitnexus_chains:
        chain = dict(chain)
        chain.pop("_source", None)
        seq = _chain_endpoint_sequence(chain)
        if seq in by_seq:
            existing = by_seq[seq]
            existing["merge_source"] = "both"
            # evidence-driven confidence wins if higher
            rank = {"confirmed": 3, "probable": 2, "theoretical": 1}
            if rank.get(chain.get("confidence", ""), 0) > rank.get(existing.get("confidence", ""), 0):
                existing["confidence"] = chain["confidence"]
            # merge step descriptions (GitNexus adds file:line evidence)
            if chain.get("steps") and not existing.get("_gn_merged"):
                existing["gitnexus_evidence"] = chain.get("steps")
                existing["_gn_merged"] = True
        else:
            chain["merge_source"] = "gitnexus-only"
            by_seq[seq] = chain

    merged: list[dict] = list(by_seq.values())
    # pop internal `_gn_merged` guard so it never leaks into attack_chains.json output
    for chain in merged:
        chain.pop("_gn_merged", None)
    logger.info("merge_attack_chains: %d chain(s) (both/llm-only/gitnexus-only)", len(merged))
    return merged
