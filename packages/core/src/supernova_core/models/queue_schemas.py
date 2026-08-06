import json
from dataclasses import dataclass, field
from typing import Union

from pydantic import BaseModel, TypeAdapter

class BaseVulnerability(BaseModel):
    ID: str
    vulnerability_type: str
    externally_exploitable: bool
    confidence: str
    # 一句话概括标题（spec 2026-08-06）：报告 ### ID: title 的 title SSOT。
    # 可选以兼容旧 queue；vuln agent 的 _vuln_output_schema 把它加进 required 强制新数据必给。
    title: str | None = None
    notes: str | None = None
    # Spec §4.1 dual-track merge fields. All are optional for backward compatibility.
    source_track: str | None = None
    evidence_chain: str | None = None
    merge_source: str | None = None

class InjectionVulnerability(BaseVulnerability):
    # LLM 轨实际输出字段(injection vuln agent 与 xss 共用同一套输出 schema,
    # 故字段名是 XSS 风格——见 collectors/vuln.py)。
    source: str | None = None
    source_detail: str | None = None
    path: str | None = None
    sink_function: str | None = None
    render_context: str | None = None
    encoding_observed: str | None = None
    verdict: str | None = None
    mismatch_reason: str | None = None
    witness_payload: str | None = None
    # 旧字段保留兼容(GitNexus 轨未来可能输出;当前 LLM 不产出)。
    combined_sources: str | None = None
    sink_call: str | None = None
    slot_type: str | None = None
    sanitization_observed: str | None = None
    concat_occurrences: str | None = None

class XssVulnerability(BaseVulnerability):
    source: str | None = None
    source_detail: str | None = None
    path: str | None = None
    sink_function: str | None = None
    render_context: str | None = None
    encoding_observed: str | None = None
    verdict: str | None = None
    mismatch_reason: str | None = None
    witness_payload: str | None = None

class AuthVulnerability(BaseVulnerability):
    source_endpoint: str | None = None
    vulnerable_code_location: str | None = None
    missing_defense: str | None = None
    exploitation_hypothesis: str | None = None
    suggested_exploit_technique: str | None = None

class SsrfVulnerability(BaseVulnerability):
    source_endpoint: str | None = None
    vulnerable_parameter: str | None = None
    vulnerable_code_location: str | None = None
    missing_defense: str | None = None
    exploitation_hypothesis: str | None = None
    suggested_exploit_technique: str | None = None
    # Spec §5.6: align with injection/xss so the Plan 3 merger can do verdict OR
    # via the verdict field (not just externally_exploitable).
    path: str | None = None
    verdict: str | None = None
    witness_payload: str | None = None

class AuthzVulnerability(BaseVulnerability):
    endpoint: str | None = None
    vulnerable_code_location: str | None = None
    role_context: str | None = None
    guard_evidence: str | None = None
    side_effect: str | None = None
    reason: str | None = None
    minimal_witness: str | None = None

Vulnerability = Union[InjectionVulnerability, XssVulnerability, AuthVulnerability, SsrfVulnerability, AuthzVulnerability, BaseVulnerability]

# pydantic 2: bare typing.Union has no .model_validate — use a TypeAdapter.
_VulnerabilityAdapter = TypeAdapter(Vulnerability)

# 按 vuln_class 强制解析的子类 adapter——规避 smart-union 在字段重叠时误判类型
# (injection 与 xss 共用 LLM 输出 schema,injection entry 会被误判为 XssVulnerability →
# render_injection_entry 访问 sink_call 崩 → 整章"渲染错误"占位)。
# 调用方已知 queue 的 class 时(如 findings_renderer 按 CLASS_CONFIG key 遍历)应传
# vuln_class 用对应子类解析,而非让通用 Union 猜类型。
_CLASS_ADAPTERS: dict[str, TypeAdapter] = {
    "injection": TypeAdapter(InjectionVulnerability),
    "xss": TypeAdapter(XssVulnerability),
    "auth": TypeAdapter(AuthVulnerability),
    "ssrf": TypeAdapter(SsrfVulnerability),
    "authz": TypeAdapter(AuthzVulnerability),
}


@dataclass
class LenientParseResult:
    """Result of lenient queue parsing.

    ``queue`` is always a valid (possibly empty) VulnerabilityQueue.
    ``warnings`` is non-empty whenever lenient recovery was applied —
    callers MUST surface these (never silent).
    """
    queue: "VulnerabilityQueue"
    warnings: list[str] = field(default_factory=list)
    original_form: str = "object"  # object | bare_list | object_no_key | invalid_json


class VulnerabilityQueue(BaseModel):
    vulnerabilities: list[Vulnerability] = []

    @classmethod
    def parse_lenient(
        cls, content: str, vuln_class: str | None = None
    ) -> LenientParseResult:
        """Tolerantly parse a queue file, absorbing legacy/hand-written forms.

        Never raises. Supported forms:
        - {"vulnerabilities": [...]}            -> object (normal)
        - [...]                                  -> bare_list (wrapped)
        - {...} without "vulnerabilities"        -> object_no_key (empty)
        - invalid JSON                           -> invalid_json (empty)
        Per-entry schema failures are dropped (recorded in warnings).

        ``vuln_class``: when the caller knows which class a queue belongs to
        (e.g. ``injection_exploitation_queue.json`` → ``"injection"``), pass it to
        force each entry into the matching subtype via ``_CLASS_ADAPTERS`` instead of
        letting the bare ``Vulnerability`` Union smart-guess. This is required because
        injection and xss share the same LLM output schema, so smart-union misclassifies
        injection entries as ``XssVulnerability``. ``None`` (default) preserves the
        legacy union-guess behaviour for the many callers that parse mixed/unknown queues.
        Unknown class names fall back to union parsing with a warning.
        """
        warnings: list[str] = []

        # --- JSON decode ---
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            return LenientParseResult(
                queue=cls(vulnerabilities=[]),
                warnings=[f"invalid json: {exc}"],
                original_form="invalid_json",
            )

        # --- Normalize top-level form into an entries list ---
        if isinstance(data, list):
            warnings.append(f"wrapped bare-list form ({len(data)} {'entry' if len(data) == 1 else 'entries'})")
            original_form = "bare_list"
            entries = data
        elif isinstance(data, dict):
            entries = data.get("vulnerabilities")
            if not isinstance(entries, list):
                actual = type(entries).__name__ if entries is not None else "None"
                warnings.append(f"'vulnerabilities' is {actual}, expected list")
                return LenientParseResult(
                    queue=cls(vulnerabilities=[]),
                    warnings=warnings,
                    original_form="object_no_key",
                )
            original_form = "object"
        else:
            warnings.append(f"top-level JSON is {type(data).__name__}, expected object or array")
            return LenientParseResult(
                queue=cls(vulnerabilities=[]),
                warnings=warnings,
                original_form="invalid_json",
            )

        # --- Validate entries individually, drop malformed ---
        # 已知 vuln_class → 用对应子类 adapter(规避 smart-union 误判);否则通用 Union。
        if vuln_class and vuln_class in _CLASS_ADAPTERS:
            adapter = _CLASS_ADAPTERS[vuln_class]
        else:
            adapter = _VulnerabilityAdapter
            if vuln_class:
                warnings.append(
                    f"unknown vuln_class {vuln_class!r}; fell back to union parsing"
                )
        vulns: list[Vulnerability] = []
        dropped = 0
        for entry in entries:
            if not isinstance(entry, dict):
                dropped += 1
                continue
            try:
                vulns.append(adapter.validate_python(entry))
            except Exception:
                dropped += 1
        if dropped:
            warnings.append(f"dropped {dropped} malformed entr{'y' if dropped == 1 else 'ies'}")

        return LenientParseResult(
            queue=cls(vulnerabilities=vulns),
            warnings=warnings,
            original_form=original_form,
        )
