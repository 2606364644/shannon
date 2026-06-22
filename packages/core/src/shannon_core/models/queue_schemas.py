import json
from dataclasses import dataclass, field
from typing import Union

from pydantic import BaseModel, TypeAdapter

class BaseVulnerability(BaseModel):
    ID: str
    vulnerability_type: str
    externally_exploitable: bool
    confidence: str
    notes: str | None = None

class InjectionVulnerability(BaseVulnerability):
    source: str | None = None
    combined_sources: str | None = None
    path: str | None = None
    sink_call: str | None = None
    slot_type: str | None = None
    sanitization_observed: str | None = None
    concat_occurrences: str | None = None
    verdict: str | None = None
    mismatch_reason: str | None = None
    witness_payload: str | None = None

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
    def parse_lenient(cls, content: str) -> LenientParseResult:
        """Tolerantly parse a queue file, absorbing legacy/hand-written forms.

        Never raises. Supported forms:
        - {"vulnerabilities": [...]}            -> object (normal)
        - [...]                                  -> bare_list (wrapped)
        - {...} without "vulnerabilities"        -> object_no_key (empty)
        - invalid JSON                           -> invalid_json (empty)
        Per-entry schema failures are dropped (recorded in warnings).
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
        vulns: list[Vulnerability] = []
        dropped = 0
        for entry in entries:
            if not isinstance(entry, dict):
                dropped += 1
                continue
            try:
                vulns.append(_VulnerabilityAdapter.validate_python(entry))
            except Exception:
                dropped += 1
        if dropped:
            warnings.append(f"dropped {dropped} malformed entr{'y' if dropped == 1 else 'ies'}")

        return LenientParseResult(
            queue=cls(vulnerabilities=vulns),
            warnings=warnings,
            original_form=original_form,
        )
