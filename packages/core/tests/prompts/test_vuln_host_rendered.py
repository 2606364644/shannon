"""Plan 3 / Task 4 — 5 vuln prompt host-rendered conversion assertions.

Asserts each of the 5 vuln-<class>.txt prompts:
- no longer instructs the agent to self-Write `<class>_analysis_deliverable.md`
  (CHUNKED WRITING / Write-tool / "synthesize into a Markdown report" patterns gone);
- carries the MANDATORY 4 `set_*` tools instruction + a `<deliverable_tools>` block;
- preserves interpolation markers (`{{DELIVERABLES_PATH}}` etc.) and the
  `<exploitation_queue_format>` block (queue is a separate channel).

Per-class strategic-intelligence field names are asserted in their human-readable
form (mirrors TS upstream wording); the snake_case field names live in the
harness-injected tool catalog, not the prompt body.
"""
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"

# (filename, class_key, human_readable_intel_fields)
VULN_PROMPTS: list[tuple[str, str, list[str]]] = [
    (
        "vuln-injection.txt",
        "injection",
        [
            "defensive evasion / WAF analysis",
            "error-based injection potential",
            "confirmed database technology",
        ],
    ),
    (
        "vuln-xss.txt",
        "xss",
        ["CSP analysis", "cookie security"],
    ),
    (
        "vuln-auth.txt",
        "auth",
        ["authentication method", "session token details", "password policy"],
    ),
    (
        "vuln-ssrf.txt",
        "ssrf",
        ["HTTP client library", "request architecture", "internal services"],
    ),
    (
        "vuln-authz.txt",
        "authz",
        [
            "session management architecture",
            "role/permission model",
            "resource access patterns",
            "workflow implementation",
        ],
    ),
]

SET_TOOLS = (
    "set_findings_summary",
    "set_strategic_intelligence",
    "set_safe_vectors",
    "set_blind_spots",
)

# Patterns that MUST be absent — self-Write deliverable instructions.
# `{cls}` is replaced with the vuln class name (injection/xss/auth/ssrf/authz).
FORBIDDEN_PATTERNS = (
    "CHUNKED WRITING",
    "Use the **Write** tool to create `{{DELIVERABLES_PATH}}/{cls}_analysis_deliverable.md`",
    "write it to `{{DELIVERABLES_PATH}}/{cls}_analysis_deliverable.md` using the Write tool",
    "Write your deliverable markdown via the Write tool first",
)


def _read(name: str) -> str:
    return (PROMPTS_DIR / name).read_text("utf-8")


# ─── per-prompt: host-rendered conversion invariants ────────────────────────

def test_host_rendered_block_present():
    """Each vuln prompt has the MANDATORY set_* + <deliverable_tools> block."""
    for name, vuln_class, _ in VULN_PROMPTS:
        text = _read(name)
        # MANDATORY host-rendered instruction
        assert "MANDATORY — Analysis Deliverable (host-rendered)" in text, (
            f"{name}: missing MANDATORY host-rendered instruction"
        )
        # 4 set_* tool names present
        for tool in SET_TOOLS:
            assert tool in text, f"{name}: missing tool name {tool}"
        # <deliverable_tools> marker
        assert "<deliverable_tools>" in text and "</deliverable_tools>" in text, (
            f"{name}: missing <deliverable_tools> block"
        )
        # host-rendered wording (links md to set_* calls)
        assert "host renders" in text.lower(), (
            f"{name}: missing 'host renders' wording"
        )


def test_self_write_instructions_gone():
    """Each vuln prompt MUST NOT tell the agent to Write the deliverable md."""
    for name, vuln_class, _ in VULN_PROMPTS:
        text = _read(name)
        for pat in FORBIDDEN_PATTERNS:
            actual = pat.format(cls=vuln_class)
            assert actual not in text, (
                f"{name}: forbidden self-Write pattern present: {actual!r}"
            )
        # No "synthesize ... into a ... Markdown report located at" + write it yourself
        assert (
            "synthesize all of your findings into a single, detailed Markdown report located at"
            not in text
        ), f"{name}: synthesize-into-Markdown-write-it-yourself pattern still present"


def test_safe_vector_reference_points_to_set_safe_vectors():
    """Safe-vector references should point to `set_safe_vectors`, not 'Markdown report'."""
    for name, vuln_class, _ in VULN_PROMPTS:
        text = _read(name)
        # Either the prompt mentions set_safe_vectors as the safe-vector channel
        # (it does — in <deliverable_tools> block and/or the safe verdict arm).
        assert "set_safe_vectors" in text
        # And no leftover "documented later in the Markdown report" / "final Markdown report"
        # for safe vectors specifically.
        bad_safe_patterns = (
            "documented later in the \"Vectors Analyzed and Confirmed Secure\" section of your final Markdown report",
            "documented in the \"Secure by Design: Validated Components\" section of your final Markdown report",
        )
        for bad in bad_safe_patterns:
            assert bad not in text, f"{name}: safe-vector still points to Markdown report: {bad!r}"


# ─── per-prompt: per-class strategic_intelligence field guidance ────────────

def test_per_class_strategic_intel_fields():
    """set_strategic_intelligence sub-field list matches this class's schema."""
    for name, vuln_class, fields in VULN_PROMPTS:
        text = _read(name)
        for field in fields:
            assert field in text, (
                f"{name}: missing {vuln_class} strategic-intel sub-field {field!r}"
            )


# ─── preservation: queue channel + interpolation ────────────────────────────

def test_exploitation_queue_format_block_preserved():
    """<exploitation_queue_format> block must remain (queue is a separate channel)."""
    for name, _, _ in VULN_PROMPTS:
        text = _read(name)
        assert "<exploitation_queue_format>" in text, (
            f"{name}: <exploitation_queue_format> block removed"
        )
        assert "</exploitation_queue_format>" in text, (
            f"{name}: <exploitation_queue_format> closing tag removed"
        )


def test_queue_note_preserved():
    """The Note about exploitation queue being captured from structured output stays."""
    for name, vuln_class, _ in VULN_PROMPTS:
        text = _read(name)
        # The Note must still tell the agent the queue is captured from final
        # structured output (separate from set_* tools).
        assert (
            "captured automatically" in text
            and "final structured output" in text
        ), f"{name}: queue-capture Note removed"
        assert (
            f"{vuln_class}_exploitation_queue.json" in text
        ), f"{name}: queue filename reference removed"


def test_interpolation_markers_intact():
    """`{{DELIVERABLES_PATH}}` etc. must still interpolate (no broken markers)."""
    must_keep = (
        "{{DELIVERABLES_PATH}}",
        "{{LOGIN_INSTRUCTIONS}}",
    )
    for name, _, _ in VULN_PROMPTS:
        text = _read(name)
        for marker in must_keep:
            assert marker in text, f"{name}: interpolation marker {marker!r} missing"

        # recon context / framework analysis markers (injection / xss / ssrf / authz have these;
        # auth has RECON_CONTEXT + FRAMEWORK_ANALYSIS too).
        assert "{{RECON_CONTEXT}}" in text, (
            f"{name}: {{{{RECON_CONTEXT}}}} marker missing"
        )
        assert "{{FRAMEWORK_ANALYSIS}}" in text, (
            f"{name}: {{{{FRAMEWORK_ANALYSIS}}}} marker missing"
        )

        # No obviously broken marker (unmatched `{{` or `}}`).
        # Soft check: count of `{{` == count of `}}` (allow @include not to use markers).
        assert text.count("{{") == text.count("}}"), (
            f"{name}: unbalanced {{ / }} (left={{text.count('{{')}} right={{text.count('}}')}})"
        )


def test_no_deterministic_hints_added():
    """§1 invariant: prompt must NOT reference deterministic-layer products."""
    forbidden_tokens = (
        "parameter_graph",
        "SinkCallSite",
        "static_dataflow_hints",
        "code_index.json",
    )
    for name, _, _ in VULN_PROMPTS:
        text = _read(name)
        for tok in forbidden_tokens:
            assert tok not in text, (
                f"{name}: references deterministic token {tok!r} (§1 violation)"
            )


# ─── Your Output line: host-rendered wording ────────────────────────────────

def test_your_output_line_reflects_host_rendering():
    """The 'Your Output' line should mention host-rendered + queue captured."""
    for name, vuln_class, _ in VULN_PROMPTS:
        text = _read(name)
        assert "host-rendered from your `set_*` tool calls" in text, (
            f"{name}: 'Your Output' line missing host-rendered wording"
        )
        assert (
            f"{vuln_class}_analysis_deliverable.md" in text
            and f"{vuln_class}_exploitation_queue.json" in text
        ), f"{name}: 'Your Output' line missing deliverable / queue filenames"
