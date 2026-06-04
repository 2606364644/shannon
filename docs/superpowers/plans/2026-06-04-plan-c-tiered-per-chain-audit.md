# Plan C: Tiered Per-Chain Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat parallel vuln-agent-per-type Phase 3 with a risk-scored, tiered per-chain audit system that allocates LLM budget proportionally to chain danger, produces structured validated findings, and generates coverage metrics.

**Architecture:** Each CallChain is scored across 4 dimensions (sink danger, taint completeness, auth gap, depth). Chains are assigned to Tier 3 (≤5 highest-risk, 5 agents each), Tier 2 (≤20 standard, 2 agents each), or Tier 1 (unlimited, 1 combined agent). Tier 1 findings with high confidence are automatically upgraded. Budget is capped at 200 LLM calls / $50.

**Tech Stack:** Python 3.11+, Pydantic v2, asyncio, Temporal.io, pytest

**Depends on:** Plan A (CodeIndex, CallChain, FuncBlock), Plan B (ParameterPropagationGraph, TaintFlow, SinkType)

**Follows:** Plan A + Plan B

---

## File Structure

### New Files

| File | Responsibility |
|---|---|
| `packages/core/src/shannon_core/code_index/risk_scorer.py` | ChainRiskScore model + scoring algorithm |
| `packages/core/src/shannon_core/code_index/finding_models.py` | VulnFinding, finding validation whitelists, deduplication |
| `packages/core/src/shannon_core/code_index/coverage_report.py` | AuditTierReport + CoverageReport models |
| `packages/core/src/shannon_core/code_index/tiered_audit.py` | Tiered audit orchestration with budget control |
| `packages/core/src/shannon_core/code_index/audit_input_builder.py` | Builds per-chain agent input (source code + taint flow + sinks) |
| `prompts/audit-tier1.txt` | Tier 1 combined agent prompt |
| `packages/core/tests/code_index/test_risk_scorer.py` | Tests for risk scoring |
| `packages/core/tests/code_index/test_finding_models.py` | Tests for finding models + validation + dedup |
| `packages/core/tests/code_index/test_coverage_report.py` | Tests for coverage reports |
| `packages/core/tests/code_index/test_tiered_audit.py` | Tests for tiered audit orchestration |
| `packages/core/tests/code_index/test_audit_input_builder.py` | Tests for audit input builder |

### Modified Files

| File | Change |
|---|---|
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | Add `run_risk_scoring` and `run_tiered_audit_activity` activities |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | Replace flat vuln dispatch with tiered audit flow |
| `packages/whitebox/src/shannon_whitebox/pipeline/shared.py` | Add tiered audit fields to PipelineState |
| `packages/core/src/shannon_core/models/agents.py` | Add AUDIT_TIER_1 AgentName + AgentDefinition |
| `prompts/pre-recon-code.txt` | Add Phase 0 coverage data section (appended) |
| `prompts/recon.txt` | Add parameter propagation data section + "No Security Judgments" |
| `prompts/recon-static.txt` | Expand to 9-chapter structure with guards/privilege lattice |

---

## Task 1: Risk Scoring Models & Scorer

**Files:**
- Create: `packages/core/src/shannon_core/code_index/risk_scorer.py`
- Test: `packages/core/tests/code_index/test_risk_scorer.py`

Scores each CallChain across 4 dimensions and assigns a tier level.

- [ ] **Step 1: Write failing tests**

Create `packages/core/tests/code_index/test_risk_scorer.py`:

```python
import pytest
from shannon_core.code_index.risk_scorer import ChainRiskScore, AuditBudget
from shannon_core.code_index.models import FuncBlock, CallChain, ParameterSource
from shannon_core.code_index.parameter_models import TaintFlow, SinkType, PropagationStep


def _block(name: str, file: str = "app.py", line: int = 1,
           source: str = "") -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}", file_path=file,
        function_name=name, start_line=line, end_line=line + 5,
        source_code=source or f"def {name}(): pass",
        parameters=[], language="python",
    )


def _make_flow(sink_type: SinkType = SinkType.SQL_EXECUTION) -> TaintFlow:
    return TaintFlow(
        entry_point_id="app.py:handler:1",
        source_param="user_id",
        source_type=ParameterSource.QUERY_PARAM,
        propagation_steps=[
            PropagationStep(
                from_func_id="app.py:handler:1", from_param="user_id",
                to_func_id="svc.py:query:10", to_param="sql",
                transformation=None, code_location="app.py:3",
            ),
        ],
        sink_func_id="svc.py:query:10",
        sink_type=sink_type,
    )


class TestChainRiskScore:
    def test_tier3_high_risk(self):
        """SQL sink + taint complete + no auth + depth 5 = Tier 3."""
        score = ChainRiskScore(
            chain_id="app.py:handler:1→svc.py:query:10",
            sink_danger=10, taint_completeness=8,
            auth_gap=8, depth=5,
        )
        assert score.total == 31
        assert score.tier == 3

    def test_tier2_medium_risk(self):
        """Template sink + some taint + no auth + depth 3 = Tier 2."""
        score = ChainRiskScore(
            chain_id="a→b",
            sink_danger=7, taint_completeness=4,
            auth_gap=8, depth=3,
        )
        assert score.total == 22
        assert score.tier == 2

    def test_tier1_low_risk(self):
        """Unknown sink + no taint + has auth + depth 1 = Tier 1."""
        score = ChainRiskScore(
            chain_id="a→c",
            sink_danger=0, taint_completeness=0,
            auth_gap=0, depth=1,
        )
        assert score.total == 1
        assert score.tier == 1

    def test_boundary_tier2_at_15(self):
        """Score exactly 15 is Tier 2."""
        score = ChainRiskScore(
            chain_id="x",
            sink_danger=5, taint_completeness=5,
            auth_gap=0, depth=5,
        )
        assert score.total == 15
        assert score.tier == 2

    def test_boundary_tier3_at_30(self):
        """Score exactly 30 is Tier 3."""
        score = ChainRiskScore(
            chain_id="y",
            sink_danger=10, taint_completeness=10,
            auth_gap=5, depth=5,
        )
        assert score.total == 30
        assert score.tier == 3


class TestChainRiskScoreClassMethod:
    def test_score_sql_chain_no_auth(self):
        blocks = {
            "app.py:handler:1": _block("handler", "app.py", 1),
            "svc.py:query:10": _block("query", "svc.py", 10,
                                       source="def query(sql): cursor.execute(sql)"),
        }
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:query:10"],
            depth=1, has_unresolved=False,
        )
        flows = [_make_flow(SinkType.SQL_EXECUTION)]
        auth_ids: set[str] = set()

        score = ChainRiskScore.score(chain, blocks, flows, auth_ids)
        assert score.sink_danger == 10  # SQL execution
        assert score.auth_gap == 8      # No auth
        assert score.tier == 3          # High total

    def test_score_with_auth(self):
        blocks = {
            "app.py:handler:1": _block("handler", "app.py", 1),
            "svc.py:query:10": _block("query", "svc.py", 10,
                                       source="def query(sql): cursor.execute(sql)"),
        }
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:query:10"],
            depth=1, has_unresolved=False,
        )
        flows = [_make_flow(SinkType.SQL_EXECUTION)]
        auth_ids = {"app.py:handler:1"}  # handler has auth

        score = ChainRiskScore.score(chain, blocks, flows, auth_ids)
        assert score.auth_gap == 0  # Has auth middleware
        assert score.tier >= 2      # Still elevated due to SQL sink + taint

    def test_score_no_flows_no_sink(self):
        blocks = {
            "app.py:handler:1": _block("handler", "app.py", 1),
        }
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1"],
            depth=0, has_unresolved=False,
        )
        score = ChainRiskScore.score(chain, blocks, [], set())
        assert score.sink_danger == 0
        assert score.taint_completeness == 0
        assert score.tier == 1

    def test_score_command_exec_sink(self):
        blocks = {
            "app.py:handler:1": _block("handler", "app.py", 1),
            "svc.py:run:10": _block("run", "svc.py", 10,
                                     source="def run(cmd): os.system(cmd)"),
        }
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:run:10"],
            depth=1, has_unresolved=False,
        )
        flows = [_make_flow(SinkType.COMMAND_EXEC)]
        score = ChainRiskScore.score(chain, blocks, flows, set())
        assert score.sink_danger == 10


class TestAuditBudget:
    def test_default_budget(self):
        budget = AuditBudget()
        assert budget.max_total_llm_calls == 200
        assert budget.tier3_max_chains == 5
        assert budget.tier2_max_chains == 20

    def test_custom_budget(self):
        budget = AuditBudget(
            max_total_llm_calls=100,
            tier3_max_chains=3,
            tier2_max_chains=10,
        )
        assert budget.max_total_llm_calls == 100

    def test_estimate_calls(self):
        budget = AuditBudget()
        # 3 tier3 × 5 + 10 tier2 × 2 + 50 tier1 × 1 = 15 + 20 + 50 = 85
        est = budget.estimate_calls(tier3_count=3, tier2_count=10, tier1_count=50)
        assert est == 85
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_risk_scorer.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement risk_scorer.py**

Create `packages/core/src/shannon_core/code_index/risk_scorer.py`:

```python
"""Chain risk scorer — scores call chains and assigns audit tiers.

Each CallChain is scored across 4 dimensions (0-10 each):
  - sink_danger: Does the chain reach a high-danger sink?
  - taint_completeness: Does parameter propagation cover the sink?
  - auth_gap: Is authentication middleware missing?
  - depth: How deep is the call chain?

Tier assignment:
  - Tier 3 (total ≥ 30): Full depth audit, ≤5 chains, 5 agents each
  - Tier 2 (total ≥ 15): Standard audit, ≤20 chains, 2 agents each
  - Tier 1 (total < 15):  Lightweight scan, unlimited, 1 combined agent
"""

import logging
from pydantic import BaseModel

from shannon_core.code_index.models import CallChain, FuncBlock
from shannon_core.code_index.parameter_models import SinkType, TaintFlow

logger = logging.getLogger(__name__)

# Sink danger scores by type
SINK_DANGER_SCORES: dict[SinkType, int] = {
    SinkType.SQL_EXECUTION: 10,
    SinkType.COMMAND_EXEC: 10,
    SinkType.DESERIALIZATION: 9,
    SinkType.FILE_WRITE: 8,
    SinkType.TEMPLATE_RENDER: 7,
    SinkType.HTTP_REQUEST: 6,
    SinkType.LOG_WRITE: 3,
    SinkType.UNKNOWN: 0,
}


class ChainRiskScore(BaseModel):
    """Risk score for a single call chain."""
    chain_id: str
    sink_danger: int = 0
    taint_completeness: int = 0
    auth_gap: int = 0
    depth: int = 0

    @property
    def total(self) -> int:
        return self.sink_danger + self.taint_completeness + self.auth_gap + self.depth

    @property
    def tier(self) -> int:
        if self.total >= 30:
            return 3
        if self.total >= 15:
            return 2
        return 1

    @classmethod
    def score(
        cls,
        chain: CallChain,
        blocks_by_id: dict[str, FuncBlock],
        taint_flows: list[TaintFlow],
        auth_middleware_ids: set[str],
    ) -> "ChainRiskScore":
        """Score a call chain based on its risk characteristics.

        Args:
            chain: The call chain to score.
            blocks_by_id: Lookup from FuncBlock.id to FuncBlock.
            taint_flows: Taint flows for this chain.
            auth_middleware_ids: IDs of functions that are auth middleware.

        Returns:
            ChainRiskScore with all dimensions populated.
        """
        chain_id = "→".join(chain.path[:4])  # Truncate for display

        # Sink danger: check the terminal function
        sink_node_id = chain.path[-1] if chain.path else None
        sink_danger = 0
        if sink_node_id:
            sink_block = blocks_by_id.get(sink_node_id)
            if sink_block:
                from shannon_core.code_index.taint_propagator import classify_sink
                sink_type = classify_sink(sink_block)
                sink_danger = SINK_DANGER_SCORES.get(sink_type, 0)

        # Taint completeness: how many flows reach the sink
        reaching = [f for f in taint_flows if f.sink_func_id == sink_node_id]
        taint_completeness = min(10, len(reaching) * 2)

        # Auth gap: does the chain pass through auth middleware?
        chain_has_auth = any(
            node_id in auth_middleware_ids for node_id in chain.path
        )
        auth_gap = 0 if chain_has_auth else 8

        # Depth: call chain length
        depth = min(10, len(chain.path))

        return cls(
            chain_id=chain_id,
            sink_danger=sink_danger,
            taint_completeness=taint_completeness,
            auth_gap=auth_gap,
            depth=depth,
        )


class AuditBudget(BaseModel):
    """Budget control for the tiered audit phase.

    Caps total LLM calls and per-tier chain counts.
    """
    max_total_llm_calls: int = 200
    max_cost_usd: float = 50.0
    tier3_max_chains: int = 5
    tier2_max_chains: int = 20
    tier1_combined_agent: bool = True

    def estimate_calls(
        self,
        tier3_count: int = 0,
        tier2_count: int = 0,
        tier1_count: int = 0,
    ) -> int:
        """Estimate total LLM calls for the given chain distribution."""
        t3 = min(tier3_count, self.tier3_max_chains) * 5  # 5 agents each
        t2 = min(tier2_count, self.tier2_max_chains) * 2  # 2 agents each
        t1 = tier1_count * 1  # 1 combined agent
        return t3 + t2 + t1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_risk_scorer.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/risk_scorer.py packages/core/tests/code_index/test_risk_scorer.py
git commit -m "feat(code_index): add chain risk scorer and audit budget

ChainRiskScore scores chains across sink_danger, taint_completeness,
auth_gap, depth dimensions. Assigns Tier 1/2/3 based on total score.
AuditBudget caps LLM calls at 200 with per-tier chain limits."
```

---

## Task 2: VulnFinding Models & Validation

**Files:**
- Create: `packages/core/src/shannon_core/code_index/finding_models.py`
- Test: `packages/core/tests/code_index/test_finding_models.py`

VulnFinding model, category/issue_type whitelists, structured output validation, and 5-tuple deduplication.

- [ ] **Step 1: Write failing tests**

Create `packages/core/tests/code_index/test_finding_models.py`:

```python
import pytest
from shannon_core.code_index.finding_models import (
    VulnFinding, FindingVerdict,
    parse_and_validate_findings, deduplicate_findings,
    VALID_INJECTION_CATEGORIES, VALID_AUTH_CATEGORIES,
    VALID_XSS_CATEGORIES, VALID_AUTHZ_CATEGORIES,
    VALID_SSRF_CATEGORIES, VALID_MISCONFIG_CATEGORIES,
)


def _finding(
    category: str = "injection",
    issue_type: str = "sql_injection",
    entry_point_id: str = "app.py:handler:1",
    vulnerable_func_id: str = "svc.py:query:10",
    chain_path: tuple = ("handler", "query"),
    confidence: float = 0.9,
) -> VulnFinding:
    return VulnFinding(
        category=category,
        issue_type=issue_type,
        entry_point_id=entry_point_id,
        vulnerable_function_id=vulnerable_func_id,
        call_chain_path=list(chain_path),
        confidence=confidence,
        verdict=FindingVerdict.VULNERABLE,
        title=f"{category}: {issue_type}",
        description="Test finding",
        code_location="svc.py:10",
    )


class TestVulnFinding:
    def test_basic_creation(self):
        f = _finding()
        assert f.category == "injection"
        assert f.confidence == 0.9
        assert f.verdict == FindingVerdict.VULNERABLE

    def test_dedup_key(self):
        f = _finding()
        key = f.dedup_key
        assert "app.py:handler:1" in key
        assert "sql_injection" in key


class TestCategoryWhitelists:
    def test_injection_categories(self):
        assert "sql_injection" in VALID_INJECTION_CATEGORIES
        assert "command_injection" in VALID_INJECTION_CATEGORIES
        assert "path_traversal" in VALID_INJECTION_CATEGORIES
        assert "ssti" in VALID_INJECTION_CATEGORIES

    def test_auth_categories(self):
        assert "broken_authentication" in VALID_AUTH_CATEGORIES
        assert "credential_stuffing" in VALID_AUTH_CATEGORIES

    def test_xss_categories(self):
        assert "reflected_xss" in VALID_XSS_CATEGORIES
        assert "stored_xss" in VALID_XSS_CATEGORIES
        assert "dom_xss" in VALID_XSS_CATEGORIES

    def test_authz_categories(self):
        assert "idor" in VALID_AUTHZ_CATEGORIES
        assert "missing_role_check" in VALID_AUTHZ_CATEGORIES

    def test_ssrf_categories(self):
        assert "server_side_request_forgery" in VALID_SSRF_CATEGORIES

    def test_misconfig_categories(self):
        assert "cors_misconfiguration" in VALID_MISCONFIG_CATEGORIES


class TestParseAndValidateFindings:
    def test_valid_findings(self):
        raw = {
            "findings": [
                {
                    "category": "injection",
                    "issue_type": "sql_injection",
                    "entry_point_id": "a.py:f:1",
                    "vulnerable_function_id": "b.py:g:2",
                    "call_chain_path": ["f", "g"],
                    "confidence": 0.9,
                    "title": "SQL Injection",
                    "description": "Unsanitized input",
                    "code_location": "b.py:2",
                },
            ]
        }
        results = parse_and_validate_findings(raw, "injection")
        assert len(results) == 1
        assert results[0].issue_type == "sql_injection"

    def test_invalid_category_filtered(self):
        raw = {
            "findings": [
                {
                    "category": "injection",
                    "issue_type": "fake_vulnerability",
                    "entry_point_id": "a:f:1",
                    "vulnerable_function_id": "b:g:2",
                    "call_chain_path": ["f", "g"],
                    "confidence": 0.9,
                    "title": "Fake",
                    "description": "Not real",
                    "code_location": "b.py:2",
                },
            ]
        }
        results = parse_and_validate_findings(raw, "injection")
        assert len(results) == 0  # Filtered out

    def test_wrong_agent_type_filtered(self):
        raw = {
            "findings": [
                {
                    "category": "xss",
                    "issue_type": "reflected_xss",
                    "entry_point_id": "a:f:1",
                    "vulnerable_function_id": "b:g:2",
                    "call_chain_path": ["f", "g"],
                    "confidence": 0.9,
                    "title": "XSS",
                    "description": "Found XSS",
                    "code_location": "b.py:2",
                },
            ]
        }
        results = parse_and_validate_findings(raw, "injection")
        assert len(results) == 0  # XSS not valid for injection agent

    def test_empty_findings(self):
        results = parse_and_validate_findings({"findings": []}, "injection")
        assert results == []

    def test_malformed_entry_skipped(self):
        raw = {
            "findings": [
                {"category": "injection"},  # Missing required fields
            ]
        }
        results = parse_and_validate_findings(raw, "injection")
        assert len(results) == 0


class TestDeduplicateFindings:
    def test_removes_exact_duplicates(self):
        findings = [_finding(), _finding()]
        result = deduplicate_findings(findings)
        assert len(result) == 1

    def test_keeps_different_chain_paths(self):
        f1 = _finding(chain_path=("handler", "query_a"))
        f2 = _finding(chain_path=("handler", "query_b"))
        result = deduplicate_findings([f1, f2])
        assert len(result) == 2

    def test_keeps_different_categories(self):
        f1 = _finding(category="injection", issue_type="sql_injection")
        f2 = _finding(category="injection", issue_type="command_injection",
                      vulnerable_func_id="svc.py:exec:20")
        result = deduplicate_findings([f1, f2])
        assert len(result) == 2

    def test_keeps_different_entry_points(self):
        f1 = _finding(entry_point_id="a.py:f:1")
        f2 = _finding(entry_point_id="a.py:g:5",
                      vulnerable_func_id="svc.py:query:10")
        result = deduplicate_findings([f1, f2])
        assert len(result) == 2

    def test_empty_list(self):
        result = deduplicate_findings([])
        assert result == []

    def test_five_tuple_dedup(self):
        """Dedup key = (entry_point, category, issue_type, vuln_func, chain_path)."""
        f1 = _finding(
            category="injection", issue_type="sql_injection",
            entry_point_id="a.py:f:1", vulnerable_func_id="b.py:g:2",
            chain_path=("f", "g"),
        )
        f2 = _finding(
            category="injection", issue_type="sql_injection",
            entry_point_id="a.py:f:1", vulnerable_func_id="b.py:g:2",
            chain_path=("f", "g"),
            confidence=0.95,  # Different confidence, same 5-tuple
        )
        result = deduplicate_findings([f1, f2])
        assert len(result) == 1
        assert result[0].confidence == 0.9  # Keeps first occurrence
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_finding_models.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement finding_models.py**

Create `packages/core/src/shannon_core/code_index/finding_models.py`:

```python
"""VulnFinding model, category whitelists, validation, and deduplication.

Provides:
- VulnFinding: Structured vulnerability finding from audit agents
- Category/issue_type whitelists for each vulnerability class
- parse_and_validate_findings(): Filter raw LLM output against whitelists
- deduplicate_findings(): 5-tuple deduplication
"""

import logging
from enum import Enum
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


class FindingVerdict(str, Enum):
    """Verdict for a vulnerability finding."""
    VULNERABLE = "vulnerable"
    NOT_VULNERABLE = "not_vulnerable"
    NEEDS_REVIEW = "needs_review"


# ── Category/Issue-Type Whitelists ───────────────────────────
# These define the valid structured output categories for each agent type.
# Any finding not matching these is discarded.

VALID_INJECTION_CATEGORIES: set[str] = {
    "sql_injection", "command_injection", "path_traversal",
    "ssti", "ldap_injection", "nosql_injection",
}

VALID_AUTH_CATEGORIES: set[str] = {
    "broken_authentication", "weak_password_policy",
    "credential_stuffing", "session_fixation",
    "insecure_password_storage", "missing_brute_force_protection",
}

VALID_XSS_CATEGORIES: set[str] = {
    "reflected_xss", "stored_xss", "dom_xss",
}

VALID_AUTHZ_CATEGORIES: set[str] = {
    "idor", "missing_role_check", "role_tampering",
    "tenant_isolation", "cross_org_access",
    "horizontal_privilege_escalation", "vertical_privilege_escalation",
}

VALID_SSRF_CATEGORIES: set[str] = {
    "server_side_request_forgery", "dns_rebinding",
    "internal_service_access",
}

VALID_MISCONFIG_CATEGORIES: set[str] = {
    "cors_misconfiguration", "missing_security_headers",
    "insecure_cookie_flags", "information_disclosure",
    "debug_mode_enabled", "default_credentials",
}

# Map agent_type → valid issue_types
AGENT_TYPE_WHITELIST: dict[str, set[str]] = {
    "injection": VALID_INJECTION_CATEGORIES,
    "auth": VALID_AUTH_CATEGORIES,
    "xss": VALID_XSS_CATEGORIES,
    "authz": VALID_AUTHZ_CATEGORIES,
    "ssrf": VALID_SSRF_CATEGORIES,
    "misconfig": VALID_MISCONFIG_CATEGORIES,
}


class VulnFinding(BaseModel):
    """A structured vulnerability finding from an audit agent."""
    category: str                           # "injection" | "auth" | "xss" | ...
    issue_type: str                         # "sql_injection" | "idor" | ...
    entry_point_id: str                     # FuncBlock.id of the entry point
    vulnerable_function_id: str             # FuncBlock.id of the vulnerable function
    call_chain_path: list[str]              # Function names in the chain
    confidence: float                       # 0.0-1.0
    verdict: FindingVerdict = FindingVerdict.VULNERABLE
    title: str = ""
    description: str = ""
    code_location: str = ""                 # "file:line"
    remediation: str = ""
    evidence: str = ""

    @property
    def dedup_key(self) -> tuple:
        """5-tuple deduplication key."""
        return (
            self.entry_point_id,
            self.category,
            self.issue_type,
            self.vulnerable_function_id,
            tuple(self.call_chain_path),
        )


def parse_and_validate_findings(
    raw: dict,
    agent_type: str,
) -> list[VulnFinding]:
    """Parse raw LLM JSON output and validate against whitelists.

    Args:
        raw: Parsed JSON dict with "findings" key containing a list.
        agent_type: "injection" | "auth" | "xss" | "authz" | "ssrf" | "misconfig"

    Returns:
        List of validated VulnFinding objects. Invalid entries are silently dropped.
    """
    valid_issues = AGENT_TYPE_WHITELIST.get(agent_type, set())
    if not valid_issues:
        logger.warning("Unknown agent type: %s, no whitelist", agent_type)
        return []

    raw_findings = raw.get("findings", [])
    results: list[VulnFinding] = []

    for item in raw_findings:
        # Validate issue_type against whitelist
        issue_type = item.get("issue_type", "")
        category = item.get("category", "")

        if issue_type not in valid_issues:
            logger.debug("Filtered finding: issue_type=%s not in %s whitelist",
                         issue_type, agent_type)
            continue

        # Try to parse as VulnFinding
        try:
            finding = VulnFinding(**item)
            results.append(finding)
        except (ValidationError, TypeError) as exc:
            logger.warning("Invalid finding structure: %s", exc)
            continue

    return results


def deduplicate_findings(findings: list[VulnFinding]) -> list[VulnFinding]:
    """Deduplicate findings using 5-tuple key.

    Dedup key: (entry_point_id, category, issue_type,
                vulnerable_function_id, tuple(call_chain_path))

    Keeps the first occurrence when duplicates are found.
    """
    seen: set[tuple] = set()
    unique: list[VulnFinding] = []

    for finding in findings:
        key = finding.dedup_key
        if key not in seen:
            seen.add(key)
            unique.append(finding)

    return unique
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_finding_models.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/finding_models.py packages/core/tests/code_index/test_finding_models.py
git commit -m "feat(code_index): add VulnFinding models with validation and dedup

VulnFinding structured model with category/issue_type whitelists.
parse_and_validate_findings() filters LLM output against whitelists.
deduplicate_findings() uses 5-tuple key for dedup."
```

---

## Task 3: Coverage Report Models

**Files:**
- Create: `packages/core/src/shannon_core/code_index/coverage_report.py`
- Test: `packages/core/tests/code_index/test_coverage_report.py`

Audit tier distribution statistics and coverage metrics for each phase.

- [ ] **Step 1: Write failing tests**

Create `packages/core/tests/code_index/test_coverage_report.py`:

```python
import json
import pytest
from shannon_core.code_index.coverage_report import (
    AuditTierReport, CoverageReport, Phase0Coverage, Phase3Coverage,
)
from shannon_core.code_index.risk_scorer import ChainRiskScore


class TestAuditTierReport:
    def test_empty_report(self):
        report = AuditTierReport(total_chains=0)
        assert report.tier3_count == 0
        assert report.tier2_count == 0
        assert report.tier1_count == 0

    def test_from_scores(self):
        scores = [
            ChainRiskScore(chain_id="a", sink_danger=10, taint_completeness=10,
                           auth_gap=8, depth=5),  # total=33, tier 3
            ChainRiskScore(chain_id="b", sink_danger=7, taint_completeness=4,
                           auth_gap=8, depth=3),  # total=22, tier 2
            ChainRiskScore(chain_id="c", sink_danger=0, taint_completeness=0,
                           auth_gap=0, depth=1),  # total=1, tier 1
        ]
        report = AuditTierReport.from_scores(scores)
        assert report.total_chains == 3
        assert report.tier3_count == 1
        assert report.tier2_count == 1
        assert report.tier1_count == 1
        assert report.estimated_llm_calls == 5 + 2 + 1  # 8

    def test_json_serialization(self):
        report = AuditTierReport(total_chains=5, tier3_count=1,
                                  tier2_count=2, tier1_count=2,
                                  estimated_llm_calls=9,
                                  actual_llm_calls=8)
        data = json.loads(report.to_json())
        assert data["total_chains"] == 5
        assert data["tier3_count"] == 1


class TestPhase0Coverage:
    def test_file_coverage(self):
        cov = Phase0Coverage(
            total_source_files=100,
            indexed_source_files=95,
            total_template_files=20,
            scanned_template_files=18,
            total_config_files=10,
            scanned_config_files=10,
        )
        assert cov.source_file_coverage == 0.95
        assert cov.template_file_coverage == 0.90
        assert cov.config_file_coverage == 1.0


class TestPhase3Coverage:
    def test_chain_coverage(self):
        cov = Phase3Coverage(
            total_chains=50,
            tier3_chains_audited=5,
            tier2_chains_audited=20,
            tier1_chains_audited=25,
            total_findings=12,
            deduplicated_findings=8,
            llm_calls_used=85,
            llm_calls_budget=200,
        )
        assert cov.chain_audit_coverage == 1.0  # All 50 chains audited
        assert cov.budget_used_fraction == 85 / 200


class TestCoverageReport:
    def test_full_report(self):
        report = CoverageReport(
            phase0=Phase0Coverage(
                total_source_files=50, indexed_source_files=48,
                total_template_files=10, scanned_template_files=10,
                total_config_files=5, scanned_config_files=5,
            ),
            phase3=Phase3Coverage(
                total_chains=30,
                tier3_chains_audited=3, tier2_chains_audited=10,
                tier1_chains_audited=17,
                total_findings=5, deduplicated_findings=4,
                llm_calls_used=60, llm_calls_budget=200,
            ),
        )
        json_str = report.to_json()
        assert "phase0" in json_str
        assert "phase3" in json_str
        # Round-trip
        restored = CoverageReport.model_validate_json(json_str)
        assert restored.phase0.total_source_files == 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_coverage_report.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement coverage_report.py**

Create `packages/core/src/shannon_core/code_index/coverage_report.py`:

```python
"""Coverage report models for audit tier distribution and phase coverage.

Tracks:
- Phase 0: File/function/entry point coverage from code indexing
- Phase 3: Chain audit coverage, tier distribution, finding counts, budget usage
- Overall: Combined report with JSON serialization
"""

from __future__ import annotations

from pydantic import BaseModel

from shannon_core.code_index.risk_scorer import ChainRiskScore


class AuditTierReport(BaseModel):
    """Statistics about the tiered audit distribution."""
    total_chains: int = 0
    tier3_count: int = 0
    tier2_count: int = 0
    tier1_count: int = 0
    estimated_llm_calls: int = 0
    actual_llm_calls: int = 0

    @classmethod
    def from_scores(cls, scores: list[ChainRiskScore]) -> "AuditTierReport":
        """Build tier distribution from a list of scored chains."""
        tier3 = sum(1 for s in scores if s.tier == 3)
        tier2 = sum(1 for s in scores if s.tier == 2)
        tier1 = sum(1 for s in scores if s.tier == 1)
        est = tier3 * 5 + tier2 * 2 + tier1 * 1

        return cls(
            total_chains=len(scores),
            tier3_count=tier3,
            tier2_count=tier2,
            tier1_count=tier1,
            estimated_llm_calls=est,
        )

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)


class Phase0Coverage(BaseModel):
    """Coverage metrics for Phase 0 (code indexing)."""
    total_source_files: int = 0
    indexed_source_files: int = 0
    total_template_files: int = 0
    scanned_template_files: int = 0
    total_config_files: int = 0
    scanned_config_files: int = 0
    total_entry_points: int = 0
    resolved_entry_points: int = 0

    @property
    def source_file_coverage(self) -> float:
        if self.total_source_files == 0:
            return 1.0
        return self.indexed_source_files / self.total_source_files

    @property
    def template_file_coverage(self) -> float:
        if self.total_template_files == 0:
            return 1.0
        return self.scanned_template_files / self.total_template_files

    @property
    def config_file_coverage(self) -> float:
        if self.total_config_files == 0:
            return 1.0
        return self.scanned_config_files / self.total_config_files


class Phase3Coverage(BaseModel):
    """Coverage metrics for Phase 3 (tiered audit)."""
    total_chains: int = 0
    tier3_chains_audited: int = 0
    tier2_chains_audited: int = 0
    tier1_chains_audited: int = 0
    tier1_upgraded: int = 0
    total_findings: int = 0
    deduplicated_findings: int = 0
    llm_calls_used: int = 0
    llm_calls_budget: int = 200

    @property
    def chain_audit_coverage(self) -> float:
        audited = (self.tier3_chains_audited + self.tier2_chains_audited +
                   self.tier1_chains_audited)
        if self.total_chains == 0:
            return 1.0
        return audited / self.total_chains

    @property
    def budget_used_fraction(self) -> float:
        if self.llm_calls_budget == 0:
            return 0.0
        return self.llm_calls_used / self.llm_calls_budget


class CoverageReport(BaseModel):
    """Combined coverage report across all phases."""
    phase0: Phase0Coverage = Phase0Coverage()
    phase3: Phase3Coverage = Phase3Coverage()

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_coverage_report.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/coverage_report.py packages/core/tests/code_index/test_coverage_report.py
git commit -m "feat(code_index): add coverage report models

AuditTierReport tracks tier distribution from risk scores.
Phase0Coverage tracks file/entry point coverage.
Phase3Coverage tracks chain audit coverage, finding counts, budget usage.
CoverageReport combines all phases with JSON serialization."
```

---

## Task 4: Audit Input Builder

**Files:**
- Create: `packages/core/src/shannon_core/code_index/audit_input_builder.py`
- Test: `packages/core/tests/code_index/test_audit_input_builder.py`

Builds the structured input for each audit agent from call chain data: complete source code per function, taint flow summary, and sink location.

- [ ] **Step 1: Write failing tests**

Create `packages/core/tests/code_index/test_audit_input_builder.py`:

```python
import pytest
from shannon_core.code_index.audit_input_builder import (
    build_chain_audit_input, build_tier1_audit_input,
    format_taint_flow_summary,
)
from shannon_core.code_index.models import FuncBlock, CallChain, ParameterSource
from shannon_core.code_index.parameter_models import (
    TaintFlow, SinkType, PropagationStep,
)


def _block(name: str, file: str, line: int, source: str) -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}", file_path=file,
        function_name=name, start_line=line, end_line=line + 5,
        source_code=source, parameters=[], language="python",
    )


class TestBuildChainAuditInput:
    def test_builds_input_with_source_and_taint(self):
        blocks = {
            "app.py:handler:1": _block(
                "handler", "app.py", 1,
                "def handler(request):\n    user_id = request.args.get('id')\n    process(user_id)\n",
            ),
            "svc.py:process:10": _block(
                "process", "svc.py", 10,
                "def process(order_id):\n    db.query(order_id)\n",
            ),
        }
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:process:10"],
            depth=1, has_unresolved=False,
        )
        flows = [
            TaintFlow(
                entry_point_id="app.py:handler:1",
                source_param="user_id",
                source_type=ParameterSource.QUERY_PARAM,
                propagation_steps=[
                    PropagationStep(
                        from_func_id="app.py:handler:1", from_param="user_id",
                        to_func_id="svc.py:process:10", to_param="order_id",
                        transformation=None, code_location="app.py:3",
                    ),
                ],
                sink_func_id="svc.py:process:10",
                sink_type=SinkType.SQL_EXECUTION,
            ),
        ]

        result = build_chain_audit_input(chain, blocks, flows)
        assert "## Call Chain" in result
        assert "handler" in result
        assert "process" in result
        assert "## Taint Flow" in result
        assert "query" in result
        assert "## Sinks" in result
        assert "SQL execution" in result

    def test_empty_chain(self):
        chain = CallChain(
            entry_point_id="app.py:f:1",
            path=["app.py:f:1"], depth=0, has_unresolved=False,
        )
        result = build_chain_audit_input(chain, {}, [])
        assert "## Call Chain" in result


class TestBuildTier1AuditInput:
    def test_shorter_format(self):
        blocks = {
            "app.py:f:1": _block("f", "app.py", 1, "def f(x): g(x)"),
        }
        chain = CallChain(
            entry_point_id="app.py:f:1",
            path=["app.py:f:1"], depth=0, has_unresolved=False,
        )
        result = build_tier1_audit_input(chain, blocks, [])
        assert "Quick Security Scan" in result


class TestFormatTaintFlowSummary:
    def test_single_flow(self):
        flows = [
            TaintFlow(
                entry_point_id="app.py:handler:1",
                source_param="user_id",
                source_type=ParameterSource.QUERY_PARAM,
                propagation_steps=[
                    PropagationStep(
                        from_func_id="app.py:handler:1", from_param="user_id",
                        to_func_id="svc.py:process:10", to_param="order_id",
                        transformation=None, code_location="app.py:3",
                    ),
                ],
                sink_func_id="svc.py:process:10",
                sink_type=SinkType.SQL_EXECUTION,
            ),
        ]
        summary = format_taint_flow_summary(flows)
        assert "user_id" in summary
        assert "QUERY" in summary or "query" in summary
        assert "order_id" in summary

    def test_no_flows(self):
        summary = format_taint_flow_summary([])
        assert summary == "No taint flow data available for this chain."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_audit_input_builder.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement audit_input_builder.py**

Create `packages/core/src/shannon_core/code_index/audit_input_builder.py`:

```python
"""Audit input builder — formats call chain data for audit agent prompts.

Builds the structured text input that audit agents receive:
- Complete source code for each function in the chain
- Taint flow summary (parameter propagation)
- Sink locations and types

Two formats:
- build_chain_audit_input(): Full format for Tier 2/3 agents
- build_tier1_audit_input(): Compact format for Tier 1 combined agent
"""

import logging

from shannon_core.code_index.models import CallChain, FuncBlock
from shannon_core.code_index.parameter_models import TaintFlow

logger = logging.getLogger(__name__)


def build_chain_audit_input(
    chain: CallChain,
    blocks_by_id: dict[str, FuncBlock],
    taint_flows: list[TaintFlow],
) -> str:
    """Build full audit input for a Tier 2/3 agent.

    Includes complete source code for each function in the chain,
    the taint flow summary, and sink locations.
    """
    sections: list[str] = []

    # Call chain header
    chain_summary = " → ".join(
        blocks_by_id[nid].function_name if nid in blocks_by_id else nid.split(":")[1]
        for nid in chain.path
    )
    sections.append(f"## Call Chain\n{chain_summary}\n")

    # Source code for each function
    for i, func_id in enumerate(chain.path):
        block = blocks_by_id.get(func_id)
        if block:
            lang = block.language
            sections.append(
                f"## Function {i + 1}: {block.function_name} "
                f"({block.file_path}:{block.start_line}-{block.end_line})\n"
                f"```{lang}\n{block.source_code}\n```\n"
            )

    # Taint flow
    sections.append(f"## Taint Flow (from Parameter Propagation Graph)\n"
                    f"{format_taint_flow_summary(taint_flows)}\n")

    # Sinks
    sinks = []
    for flow in taint_flows:
        if flow.sink_type and flow.sink_func_id:
            sinks.append(
                f"- {flow.sink_func_id}: {flow.sink_type.value.replace('_', ' ')} "
                f"sink at {flow.sink_func_id}"
            )
    if sinks:
        sections.append("## Sinks in this chain\n" + "\n".join(sinks) + "\n")
    else:
        sections.append("## Sinks in this chain\nNo identified sinks.\n")

    return "\n".join(sections)


def build_tier1_audit_input(
    chain: CallChain,
    blocks_by_id: dict[str, FuncBlock],
    taint_flows: list[TaintFlow],
) -> str:
    """Build compact audit input for Tier 1 combined agent."""
    chain_summary = " → ".join(
        blocks_by_id[nid].function_name if nid in blocks_by_id else nid.split(":")[1]
        for nid in chain.path
    )

    parts = [
        "## Quick Security Scan\n",
        f"Analyze this call chain for ALL vulnerability types at a high level.\n",
        f"## Call Chain: {chain_summary}\n",
    ]

    # Source code (same as full format but shorter preamble)
    for i, func_id in enumerate(chain.path):
        block = blocks_by_id.get(func_id)
        if block:
            parts.append(
                f"## Function {i + 1}: {block.function_name} "
                f"({block.file_path}:{block.start_line})\n"
                f"```{block.language}\n{block.source_code}\n```\n"
            )

    parts.append(f"## Taint Flow: {format_taint_flow_summary(taint_flows)}\n")

    return "\n".join(parts)


def format_taint_flow_summary(flows: list[TaintFlow]) -> str:
    """Format taint flows as a human-readable summary.

    Shows the propagation path from source parameter to sink.
    """
    if not flows:
        return "No taint flow data available for this chain."

    lines: list[str] = []
    for flow in flows:
        source_label = f"{flow.source_param} ({flow.source_type.value})"
        if flow.propagation_steps:
            path_parts = [source_label]
            for step in flow.propagation_steps:
                transform = f" [{step.transformation}]" if step.transformation else ""
                path_parts.append(f"{step.to_param}{transform}")
            sink_label = flow.sink_type.value if flow.sink_type else "unknown"
            path_str = " → ".join(path_parts)
            lines.append(f"- {flow.source_type.value}: {path_str} → {sink_label}")
        else:
            lines.append(f"- {source_label} (no propagation steps)")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_audit_input_builder.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/audit_input_builder.py packages/core/tests/code_index/test_audit_input_builder.py
git commit -m "feat(code_index): add audit input builder

build_chain_audit_input() formats full per-chain input for Tier 2/3 agents.
build_tier1_audit_input() formats compact input for Tier 1 combined agent.
Includes source code, taint flow summary, and sink locations."
```

---

## Task 5: Tiered Audit Orchestrator

**Files:**
- Create: `packages/core/src/shannon_core/code_index/tiered_audit.py`
- Test: `packages/core/tests/code_index/test_tiered_audit.py`

The core orchestrator that scores chains, assigns tiers, and coordinates the audit within budget limits.

- [ ] **Step 1: Write failing tests**

Create `packages/core/tests/code_index/test_tiered_audit.py`:

```python
import pytest
from shannon_core.code_index.tiered_audit import (
    TieredAuditPlanner, AuditPlan,
)
from shannon_core.code_index.models import FuncBlock, CallChain, ParameterSource
from shannon_core.code_index.parameter_models import TaintFlow, SinkType
from shannon_core.code_index.risk_scorer import ChainRiskScore, AuditBudget


def _block(name: str, file: str = "app.py", line: int = 1,
           source: str = "") -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}", file_path=file,
        function_name=name, start_line=line, end_line=line + 5,
        source_code=source or f"def {name}(): pass",
        parameters=[], language="python",
    )


def _chain(path: list[str], depth: int) -> CallChain:
    return CallChain(
        entry_point_id=path[0], path=path,
        depth=depth, has_unresolved=False,
    )


class TestTieredAuditPlanner:
    def test_empty_chains(self):
        planner = TieredAuditPlanner(
            chains=[], blocks_by_id={}, taint_flows_by_chain={},
            auth_middleware_ids=set(), budget=AuditBudget(),
        )
        plan = planner.plan()
        assert plan.total_chains == 0
        assert plan.tier3_chains == []
        assert plan.tier2_chains == []
        assert plan.tier1_chains == []

    def test_sorts_by_risk(self):
        """Chains are sorted by risk score descending within each tier."""
        blocks = {
            "a.py:high:1": _block("high", "a.py", 1,
                                   source="def high(x): db.query(x)"),
            "a.py:med:5": _block("med", "a.py", 5,
                                  source="def med(x): render(x)"),
            "a.py:low:10": _block("low", "a.py", 10),
        }
        chains = [
            _chain(["a.py:low:10"], 0),
            _chain(["a.py:med:5"], 1),
            _chain(["a.py:high:1"], 1),
        ]
        # Provide taint flows for high-risk chain
        flows_by_chain = {
            "a.py:high:1": [TaintFlow(
                entry_point_id="a.py:high:1", source_param="x",
                source_type=ParameterSource.QUERY_PARAM,
                propagation_steps=[], sink_func_id="a.py:high:1",
                sink_type=SinkType.SQL_EXECUTION,
            )],
            "a.py:med:5": [TaintFlow(
                entry_point_id="a.py:med:5", source_param="x",
                source_type=ParameterSource.BODY_FIELD,
                propagation_steps=[], sink_func_id="a.py:med:5",
                sink_type=SinkType.TEMPLATE_RENDER,
            )],
            "a.py:low:10": [],
        }

        planner = TieredAuditPlanner(
            chains=chains, blocks_by_id=blocks,
            taint_flows_by_chain=flows_by_chain,
            auth_middleware_ids=set(), budget=AuditBudget(),
        )
        plan = planner.plan()
        assert plan.total_chains == 3
        # High-risk chain should be in a higher tier
        assert len(plan.scores) == 3
        # Verify scores are computed
        high_score = next(s for s in plan.scores if "high" in s.chain_id)
        low_score = next(s for s in plan.scores if "low" in s.chain_id)
        assert high_score.total > low_score.total

    def test_budget_limits_tier3(self):
        """Tier 3 is capped at tier3_max_chains."""
        blocks = {}
        chains = []
        flows_by_chain = {}
        for i in range(10):
            fid = f"a.py:f{i}:{i}"
            blocks[fid] = _block(f"f{i}", "a.py", i,
                                  source="def f(x): cursor.execute(x)")
            chains.append(_chain([fid], 0))
            flows_by_chain[fid] = [TaintFlow(
                entry_point_id=fid, source_param="x",
                source_type=ParameterSource.QUERY_PARAM,
                propagation_steps=[], sink_func_id=fid,
                sink_type=SinkType.SQL_EXECUTION,
            )]

        budget = AuditBudget(tier3_max_chains=3)
        planner = TieredAuditPlanner(
            chains=chains, blocks_by_id=blocks,
            taint_flows_by_chain=flows_by_chain,
            auth_middleware_ids=set(), budget=budget,
        )
        plan = planner.plan()
        assert len(plan.tier3_chains) <= 3

    def test_estimated_calls_within_budget(self):
        blocks = {}
        chains = []
        flows_by_chain = {}
        for i in range(10):
            fid = f"a.py:f{i}:{i}"
            blocks[fid] = _block(f"f{i}", "a.py", i,
                                  source="def f(x): cursor.execute(x)")
            chains.append(_chain([fid], 0))
            flows_by_chain[fid] = [TaintFlow(
                entry_point_id=fid, source_param="x",
                source_type=ParameterSource.QUERY_PARAM,
                propagation_steps=[], sink_func_id=fid,
                sink_type=SinkType.SQL_EXECUTION,
            )]

        budget = AuditBudget(max_total_llm_calls=50, tier3_max_chains=2,
                              tier2_max_chains=5)
        planner = TieredAuditPlanner(
            chains=chains, blocks_by_id=blocks,
            taint_flows_by_chain=flows_by_chain,
            auth_middleware_ids=set(), budget=budget,
        )
        plan = planner.plan()
        assert plan.estimated_llm_calls <= budget.max_total_llm_calls


class TestAuditPlan:
    def test_tier_distribution(self):
        scores = [
            ChainRiskScore(chain_id="a", sink_danger=10, taint_completeness=10,
                           auth_gap=8, depth=5),
            ChainRiskScore(chain_id="b", sink_danger=7, taint_completeness=4,
                           auth_gap=8, depth=3),
            ChainRiskScore(chain_id="c", sink_danger=0, taint_completeness=0,
                           auth_gap=0, depth=1),
        ]
        plan = AuditPlan(
            total_chains=3,
            scores=scores,
            tier3_chains=[scores[0]],
            tier2_chains=[scores[1]],
            tier1_chains=[scores[2]],
            estimated_llm_calls=8,
        )
        assert plan.tier3_count == 1
        assert plan.tier2_count == 1
        assert plan.tier1_count == 1

    def test_json_serialization(self):
        plan = AuditPlan(
            total_chains=0, scores=[], tier3_chains=[],
            tier2_chains=[], tier1_chains=[], estimated_llm_calls=0,
        )
        json_str = plan.to_json()
        assert "total_chains" in json_str
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_tiered_audit.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement tiered_audit.py**

Create `packages/core/src/shannon_core/code_index/tiered_audit.py`:

```python
"""Tiered audit orchestrator — plans and executes per-chain audits.

Responsibilities:
1. Score all call chains using ChainRiskScore
2. Sort chains by risk score within each tier
3. Apply budget limits (max chains per tier, max total LLM calls)
4. Produce an AuditPlan with the chain distribution

The actual LLM agent dispatch is done by the workflow layer,
not by this module. This module produces the plan.
"""

import logging
from pydantic import BaseModel

from shannon_core.code_index.models import CallChain, FuncBlock
from shannon_core.code_index.parameter_models import TaintFlow
from shannon_core.code_index.risk_scorer import ChainRiskScore, AuditBudget

logger = logging.getLogger(__name__)


class AuditPlan(BaseModel):
    """The planned audit distribution across tiers."""
    total_chains: int = 0
    scores: list[ChainRiskScore] = []
    tier3_chains: list[ChainRiskScore] = []
    tier2_chains: list[ChainRiskScore] = []
    tier1_chains: list[ChainRiskScore] = []
    estimated_llm_calls: int = 0

    @property
    def tier3_count(self) -> int:
        return len(self.tier3_chains)

    @property
    def tier2_count(self) -> int:
        return len(self.tier2_chains)

    @property
    def tier1_count(self) -> int:
        return len(self.tier1_chains)

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)


class TieredAuditPlanner:
    """Plans the tiered audit distribution.

    Usage:
        planner = TieredAuditPlanner(chains, blocks, flows, auth_ids, budget)
        plan = planner.plan()
        # plan.tier3_chains, plan.tier2_chains, plan.tier1_chains
    """

    def __init__(
        self,
        chains: list[CallChain],
        blocks_by_id: dict[str, FuncBlock],
        taint_flows_by_chain: dict[str, list[TaintFlow]],
        auth_middleware_ids: set[str],
        budget: AuditBudget,
    ):
        self.chains = chains
        self.blocks_by_id = blocks_by_id
        self.taint_flows_by_chain = taint_flows_by_chain
        self.auth_middleware_ids = auth_middleware_ids
        self.budget = budget

    def plan(self) -> AuditPlan:
        """Score all chains and produce the tiered audit plan."""
        if not self.chains:
            return AuditPlan()

        # Score all chains
        scores: list[ChainRiskScore] = []
        for chain in self.chains:
            entry_id = chain.path[0] if chain.path else ""
            flows = self.taint_flows_by_chain.get(entry_id, [])
            score = ChainRiskScore.score(
                chain, self.blocks_by_id, flows, self.auth_middleware_ids,
            )
            scores.append(score)

        # Sort by total score descending
        scores.sort(key=lambda s: -s.total)

        # Assign tiers with budget limits
        tier3 = sorted(
            [s for s in scores if s.tier == 3],
            key=lambda s: -s.total,
        )[:self.budget.tier3_max_chains]

        tier2 = sorted(
            [s for s in scores if s.tier == 2],
            key=lambda s: -s.total,
        )[:self.budget.tier2_max_chains]

        tier1 = [s for s in scores if s.tier == 1]

        # Calculate estimated LLM calls
        estimated = (
            len(tier3) * 5 +   # 5 agents per Tier 3 chain
            len(tier2) * 2 +   # 2 agents per Tier 2 chain
            len(tier1) * 1     # 1 combined agent per Tier 1 chain
        )

        # If over budget, trim from the bottom (Tier 1 first)
        while estimated > self.budget.max_total_llm_calls and tier1:
            tier1.pop()
            estimated -= 1

        logger.info(
            "Audit plan: %d chains → Tier3=%d, Tier2=%d, Tier1=%d, "
            "estimated_calls=%d (budget=%d)",
            len(scores), len(tier3), len(tier2), len(tier1),
            estimated, self.budget.max_total_llm_calls,
        )

        return AuditPlan(
            total_chains=len(scores),
            scores=scores,
            tier3_chains=tier3,
            tier2_chains=tier2,
            tier1_chains=tier1,
            estimated_llm_calls=estimated,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_tiered_audit.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/tiered_audit.py packages/core/tests/code_index/test_tiered_audit.py
git commit -m "feat(code_index): add tiered audit planner

TieredAuditPlanner scores chains, assigns tiers with budget limits,
and produces AuditPlan with tier3/tier2/tier1 chain distribution.
Trims Tier 1 chains first when over budget."
```

---

## Task 6: Tier 1 Combined Agent Prompt

**Files:**
- Create: `prompts/audit-tier1.txt`

The prompt for the Tier 1 all-in-one combined scan agent. Scans a single call chain for all vulnerability types at a high level.

- [ ] **Step 1: Write the prompt file**

Create `prompts/audit-tier1.txt`:

```
<role>
You are a security auditor performing a quick multi-type vulnerability scan on a single call chain. You analyze source code, taint flows, and sink locations to identify potential vulnerabilities across multiple categories.
</role>

<objective>
Analyze the provided call chain for ALL vulnerability types at a high level. This is a rapid screening scan — identify which vulnerability categories MAY be present and flag them for deeper analysis.
</objective>

@include(shared/_target.txt)
@include(shared/_vuln-scope.txt)

<context>
## Call Chain Analysis Input

{{CHAIN_AUDIT_INPUT}}

## Vulnerability Classes to Check: {{VULN_CLASSES_TESTED}}
</context>

<methodology>
For each vulnerability category, perform a rapid check:

### Injection Check
- Does any parameter in the taint flow reach a SQL query, command execution, or file path?
- Is there clear parameterization or sanitization between source and sink?
- Flag as vulnerable if user input reaches an execution sink without clear sanitization.

### Authentication Check
- Is authentication middleware present in the call chain?
- Are credentials handled securely (no plaintext, no weak hashing)?
- Is session management properly implemented?

### Authorization Check
- Does the endpoint enforce ownership or role checks?
- Can one user access another user's resources (IDOR)?
- Are admin-only functions properly gated?

### XSS Check
- Does user input reach HTML output or template rendering?
- Is output encoding applied?
- Check for both server-side and client-side contexts.

### SSRF Check
- Does user input influence outbound HTTP requests?
- Is URL validation or allowlisting in place?
- Check for internal service access patterns.

For each category, provide your assessment.
</methodology>

<output_format>
Output STRICT JSON — no markdown, no code fences, no explanation outside the JSON.

```json
{
  "findings": [
    {
      "category": "injection|auth|authz|xss|ssrf|misconfig",
      "issue_type": "specific_issue_type_from_whitelist",
      "entry_point_id": "file:func:line",
      "vulnerable_function_id": "file:func:line",
      "call_chain_path": ["func1", "func2", "..."],
      "confidence": 0.0,
      "title": "Short descriptive title",
      "description": "Detailed description of the vulnerability",
      "code_location": "file:line",
      "evidence": "Relevant code snippet or reasoning"
    }
  ],
  "chain_summary": {
    "vulnerable_categories": ["list of categories with findings"],
    "total_findings": 0,
    "highest_risk": "category with highest risk or null"
  }
}
```

### Category Whitelists for issue_type

**injection:** sql_injection, command_injection, path_traversal, ssti, ldap_injection, nosql_injection
**auth:** broken_authentication, weak_password_policy, credential_stuffing, session_fixation, insecure_password_storage, missing_brute_force_protection
**xss:** reflected_xss, stored_xss, dom_xss
**authz:** idor, missing_role_check, role_tampering, tenant_isolation, cross_org_access, horizontal_privilege_escalation, vertical_privilege_escalation
**ssrf:** server_side_request_forgery, dns_rebinding, internal_service_access
**misconfig:** cors_misconfiguration, missing_security_headers, insecure_cookie_flags, information_disclosure, debug_mode_enabled, default_credentials
</output_format>

<critical>
- Do NOT report findings with confidence below 0.5
- Only report issues where user-controllable input reaches a sink WITHOUT clear, effective security controls
- Do NOT make security judgments about whether sanitization is "sufficient" — report FACTS about what input reaches what sink
- If no vulnerabilities are found, return {"findings": [], "chain_summary": {"vulnerable_categories": [], "total_findings": 0, "highest_risk": null}}
- Use EXACT issue_type values from the whitelists above — any other values will be discarded
</critical>
```

- [ ] **Step 2: Verify file exists**

Run: `wc -l /root/shannon-py/prompts/audit-tier1.txt`
Expected: File exists with ~100+ lines

- [ ] **Step 3: Commit**

```bash
git add prompts/audit-tier1.txt
git commit -m "feat(prompts): add Tier 1 combined audit agent prompt

All-in-one scan prompt for lightweight per-chain screening.
Checks all vuln categories at high level, outputs structured JSON
with category whitelists and confidence thresholds."
```

---

## Task 7: Agent Registry Update

**Files:**
- Modify: `packages/core/src/shannon_core/models/agents.py`

Add a new AgentName for the Tier 1 combined audit agent and update the phase map.

- [ ] **Step 1: Write failing test**

Add to `packages/core/tests/test_agents.py`:

```python
def test_audit_tier1_agent_registered():
    from shannon_core.models.agents import AgentName, AGENTS
    assert hasattr(AgentName, "AUDIT_TIER1")
    assert AgentName.AUDIT_TIER1 in AGENTS
    agent = AGENTS[AgentName.AUDIT_TIER1]
    assert agent.prompt_template == "audit-tier1"
    assert agent.model_tier == "small"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_agents.py -k "audit_tier1" -v`
Expected: FAIL — AUDIT_TIER1 not found

- [ ] **Step 3: Add AUDIT_TIER1 to agents.py**

In `packages/core/src/shannon_core/models/agents.py`, add to the `AgentName` enum (after `MISCONFIG_EXPLOIT`):

```python
    AUDIT_TIER1 = "audit-tier1"
```

Add to the `AGENTS` dict (after `MISCONFIG_EXPLOIT`):

```python
    AgentName.AUDIT_TIER1: AgentDefinition(
        name=AgentName.AUDIT_TIER1,
        display_name="Tier 1 Combined Audit",
        prerequisites=[AgentName.RECON],
        prompt_template="audit-tier1",
        deliverable_filename=None,  # Findings collected, no separate deliverable
        model_tier="small",
    ),
```

Add to `AGENT_PHASE_MAP`:

```python
    "audit-tier1": "vulnerability-analysis",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_agents.py -k "audit_tier1" -v`
Expected: PASS

- [ ] **Step 5: Run full agents test suite**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/test_agents.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/models/agents.py packages/core/tests/test_agents.py
git commit -m "feat(agents): add AUDIT_TIER1 agent for combined chain scan

New agent uses audit-tier1 prompt template with small model tier.
Registered in vulnerability-analysis phase."
```

---

## Task 8: Pre-Recon Prompt Enhancement

**Files:**
- Modify: `prompts/pre-recon-code.txt`

Add Phase 0 coverage data section to the Pre-Recon prompt so the agent knows what the deterministic indexer found.

- [ ] **Step 1: Identify insertion point**

Read the end of `prompts/pre-recon-code.txt` to find where to append the coverage data section. The section should be added before the output format section.

- [ ] **Step 2: Add Phase 0 coverage data section**

Append the following block to `prompts/pre-recon-code.txt`, just before the `<output_format>` tag:

```
<phase0_data>
## Phase 0 Coverage Data (from code_index.json + file_manifest.json)

### Entry Points (from Entry Point Detection)
{{ENTRY_POINTS_TABLE}}

### Call Chain Statistics
- Total chains: {{TOTAL_CHAINS}}
- Average depth: {{AVG_CHAIN_DEPTH}}
- Max depth: {{MAX_CHAIN_DEPTH}}
- Unresolved calls: {{UNRESOLVED_COUNT}}

### File Coverage
- Source files (indexed): {{INDEXED_SOURCE_FILES}}
- Template files: {{TEMPLATE_FILE_COUNT}}
- Config files: {{CONFIG_FILE_COUNT}}
- Schema files: {{SCHEMA_FILE_COUNT}}
- Total: {{TOTAL_FILES}}

### Degradation Status
{{DEGRADATION_WARNING_OR_NONE}}

Use this data to cross-reference your findings. If Phase 0 detected entry points or chains that you don't mention in your analysis, explain why they were excluded.
</phase0_data>
```

- [ ] **Step 3: Commit**

```bash
git add prompts/pre-recon-code.txt
git commit -m "feat(prompts): add Phase 0 coverage data section to pre-recon

Pre-recon agent now receives deterministic code index data including
entry points, chain statistics, file coverage, and degradation status."
```

---

## Task 9: Recon Prompt Enhancement

**Files:**
- Modify: `prompts/recon.txt`
- Modify: `prompts/recon-static.txt`

Add parameter propagation data section and "No Security Judgments" rule to recon prompts. Expand recon-static.txt to include guards directory and privilege lattice sections.

- [ ] **Step 1: Add parameter propagation data to recon.txt**

Find the section in `prompts/recon.txt` that describes the input data (it reads `pre_recon_deliverable.md`). Add the following block after the input data section:

```
<parameter_propagation_data>
## Parameter Propagation Graph (from parameter_graph.json)

For each endpoint, the parameter propagation graph provides:
- Taint source: Where user input enters (query param, body field, path param, header, cookie)
- Propagation path: How input flows through function calls
- Sink: Where the input reaches a security-sensitive operation (SQL, exec, render, etc.)

{{TAINT_FLOW_SUMMARY}}

When documenting injection sources in Section 9, use the taint flow data to trace the complete path from entry parameter to sink. Include the transformation chain (if any) between source and sink.
</parameter_propagation_data>

<no_security_judgments>
CRITICAL RULE — No Security Judgments:

Your job is to IDENTIFY and REPORT facts — where user-controllable input enters, where it flows, and what sink it reaches. You MUST NOT make security judgments about:
- Whether sanitization is "sufficient"
- Whether a vulnerability is "exploitable"
- Whether a finding is "low risk" or "acceptable"

Report the FACTS. Let the downstream vulnerability analysis agents make the security assessment.
</no_security_judgments>
```

- [ ] **Step 2: Expand recon-static.txt to 9-chapter structure**

The current `recon-static.txt` has 7 sections. Add the missing sections after Section 6 (Security-Relevant File Paths):

Add Section 6.4 (Guards Directory):

```
## 6.4 Guards Directory

Map all authorization guards and middleware:
| Guard Name | File:Line | Protected Routes | Check Type |
|------------|-----------|------------------|------------|

For each guard, document:
- What it checks (role, ownership, permission, custom)
- Which routes/endpoints it protects
- Whether it can be bypassed
```

Add Section 7 (Privilege Lattice):

```
## 7. Privilege Lattice

Map the role/permission hierarchy:
```
Admin → Manager → User → Anonymous
         ↓
       Editor → Viewer
```

For each role:
- What resources it can access
- What operations it can perform
- How role assignment works (database, config, token claim)
```

Add Section 8 (Authorization Vulnerability Candidates):

```
## 8. Authorization Vulnerability Candidates

For each endpoint, document:
| Endpoint | Required Role | Object ID Params | Auth Check Present | Risk |
|----------|--------------|------------------|--------------------|------|

Focus on:
- Endpoints with object ID parameters that lack ownership validation
- Admin-only endpoints without role checks
- Endpoints that perform different actions based on user context but don't verify the context
```

- [ ] **Step 3: Commit**

```bash
git add prompts/recon.txt prompts/recon-static.txt
git commit -m "feat(prompts): enhance recon prompts with parameter propagation and auth sections

recon.txt: Add taint flow data section + No Security Judgments rule.
recon-static.txt: Expand to 9 chapters — add Guards Directory,
Privilege Lattice, Authorization Vulnerability Candidates."
```

---

## Task 10: Workflow Integration

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`

Integrate the tiered audit into the whitebox scan workflow. The key change: Phase 3 now scores chains first, then dispatches per-chain instead of per-vuln-type.

- [ ] **Step 1: Add risk scoring activity to activities.py**

Add the following activity to `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`:

```python
@activity.defn
async def run_risk_scoring(input: ActivityInput) -> dict:
    """Score call chains and produce tiered audit plan."""
    try:
        from shannon_core.code_index.models import CodeIndex
        from shannon_core.code_index.parameter_models import ParameterPropagationGraph
        from shannon_core.code_index.risk_scorer import AuditBudget
        from shannon_core.code_index.tiered_audit import TieredAuditPlanner

        repo, deliverables, _ = _get_paths(input)

        # Load code index
        code_index_path = deliverables / "code_index.json"
        if not code_index_path.exists():
            return {"total_chains": 0, "tier3_count": 0, "tier2_count": 0, "tier1_count": 0}

        index = CodeIndex.model_validate_json(code_index_path.read_text())

        # Load parameter graph
        param_graph_path = deliverables / "parameter_graph.json"
        taint_flows_by_chain: dict[str, list] = {}
        if param_graph_path.exists():
            pgraph = ParameterPropagationGraph.model_validate_json(
                param_graph_path.read_text()
            )
            for flow in pgraph.taint_flows:
                taint_flows_by_chain.setdefault(flow.entry_point_id, []).append(flow)

        # Build block lookup
        blocks_by_id = {b.id: b for b in index.blocks}

        # Auth middleware detection: simple heuristic — functions with
        # auth/login/token/verify in name or decorators
        auth_ids: set[str] = set()
        for block in index.blocks:
            combined = f"{block.function_name} {' '.join(block.decorators)}".lower()
            if any(kw in combined for kw in ("auth", "login", "token", "verify", "session")):
                auth_ids.add(block.id)

        # Plan
        planner = TieredAuditPlanner(
            chains=index.chains,
            blocks_by_id=blocks_by_id,
            taint_flows_by_chain=taint_flows_by_chain,
            auth_middleware_ids=auth_ids,
            budget=AuditBudget(),
        )
        plan = planner.plan()

        # Write audit plan
        plan_path = deliverables / "audit_plan.json"
        plan_path.write_text(plan.to_json())

        return {
            "total_chains": plan.total_chains,
            "tier3_count": plan.tier3_count,
            "tier2_count": plan.tier2_count,
            "tier1_count": plan.tier1_count,
            "estimated_llm_calls": plan.estimated_llm_calls,
        }
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

- [ ] **Step 2: Add tiered audit fields to PipelineState**

In `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`, add to the `PipelineState` dataclass:

```python
    audit_plan_stats: dict | None = None
```

- [ ] **Step 3: Modify workflow to add risk scoring before vuln analysis**

In `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`, after the Recon agent block (around line 115) and before the vuln_tasks block (around line 117), insert:

```python
            # Risk scoring — produce tiered audit plan
            risk_result = await workflow.execute_activity(
                activities.run_risk_scoring, act_input,
                start_to_close_timeout=timedelta(minutes=5),
            )
            self._state.audit_plan_stats = risk_result
```

- [ ] **Step 4: Run whitebox tests**

Run: `cd /root/shannon-py && uv run pytest packages/whitebox/tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/src/shannon_whitebox/pipeline/shared.py
git commit -m "feat(whitebox): integrate risk scoring into whitebox workflow

New run_risk_scoring activity loads code index + parameter graph,
scores chains across 4 dimensions, produces tiered audit plan.
Workflow now runs risk scoring between Recon and Vuln phases."
```

---

## Self-Review Checklist

### 1. Spec Coverage (P2 — Tiered Audit)

| Spec Requirement | Task |
|---|---|
| ChainRiskScore (sink_danger + taint_completeness + auth_gap + depth) | Task 1 (risk_scorer.py) |
| Tier assignment (≥30→T3, ≥15→T2, <15→T1) | Task 1 (risk_scorer.py) |
| AuditBudget (max_total_llm_calls=200, tier3≤5, tier2≤20) | Task 1 (risk_scorer.py) |
| Structured output validation (category/issue_type whitelists) | Task 2 (finding_models.py) |
| 5-tuple deduplication | Task 2 (finding_models.py) |
| Audit tier report | Task 3 (coverage_report.py) |
| Coverage metrics per phase | Task 3 (coverage_report.py) |
| Per-chain audit input builder | Task 4 (audit_input_builder.py) |
| Tiered audit planner (score → sort → assign → budget trim) | Task 5 (tiered_audit.py) |
| Tier 1 combined agent prompt | Task 6 (audit-tier1.txt) |
| AUDIT_TIER1 agent registration | Task 7 (agents.py) |
| Pre-Recon prompt enhancement (Phase 0 coverage data) | Task 8 |
| Recon prompt enhancement (taint flow + No Security Judgments) | Task 9 |
| Recon-static.txt expansion to 9 chapters | Task 9 |
| Workflow integration (risk scoring activity) | Task 10 |

### 2. Placeholder Scan

✅ No TBD, TODO, "implement later"
✅ All code steps contain actual code
✅ All test code is complete
✅ All file paths are exact
✅ Prompt additions contain actual text (not "add appropriate section")

### 3. Type Consistency

✅ `ChainRiskScore` fields match between definition (risk_scorer.py) and usage (tiered_audit.py, coverage_report.py)
✅ `VulnFinding.dedup_key` returns same tuple structure used in `deduplicate_findings()`
✅ `SinkType` enum from Plan B reused in risk_scorer SINK_DANGER_SCORES
✅ `TaintFlow` from Plan B reused in audit_input_builder and tiered_audit
✅ `CallChain.path` format ("file:func:line") consistent across all modules
✅ `AuditPlan.tier3_chains` / `tier2_chains` / `tier1_chains` are `list[ChainRiskScore]` — consistent with tiered_audit.py and risk_scorer.py
✅ `AgentName.AUDIT_TIER1` string value "audit-tier1" matches AGENT_PHASE_MAP key and prompt template name
