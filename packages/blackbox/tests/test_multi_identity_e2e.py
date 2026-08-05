"""子项目2 T11: 多身份越权对比扫描 端到端集成测试。

验证多身份链 WITHOUT real GLM —— 在 integration seam 上锁定契约:
- T7 build_identity_context 渲染 **真实** `prompts/shared/_identities.txt`
  (非 T8 单元测试里的合成 mini partial,而是 agent 实际看到的协议文本)
- T8 ExploitExecutor 读 identity-manifest.json + 注入 IDENTITY_CONTEXT 进
  prompt_variables,单身份(无 manifest)→ ""
- T6 verdict 层:mock agent 模拟"协议驱动"的产出 —— 有 baseline 的方向
  返回 EXPLOITED、无 baseline 的方向返回 POTENTIAL;两条 verdict 经
  validate_exploit_verdicts 均通过(L1 schema discriminated union 接纳
  两个 tier,L2 id ∈ queue,L3 去重)

WHY 这层独立于 T8 的 test_executors.py:
T8 用合成 partial 验"注入发生";T11 用真实 partial + 真实 verdict collector
验"协议内容 + verdict 路径",防 _identities.txt / verdict schema 单边演进
时破坏多身份契约。若比较协议规则被改(如 NO BASELINE 规则删除)或 POTENTIAL
档从 discriminated union 移除,本测试失败 —— 这是 T11 的护栏。

工作目录:本文件 `packages/blackbox/tests/test_multi_identity_e2e.py`,
`parents[3]` 即 repo 根(对齐 test_endpoint_verify.py:336 的路径口径)。
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from supernova_core.collectors.exploit import validate_exploit_verdicts
from supernova_core.models.agents import AgentName
from supernova_core.models.metrics import AgentMetrics
from supernova_core.prompts.manager import PromptManager
from supernova_core.services.engines.agent_browser_engine import AgentBrowserEngine
from supernova_core.services.validate_authentication import (
    IdentityManifest,
    IdentityRecord,
)
from supernova_blackbox.agents.exploit_executor import ExploitExecutor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_PROMPTS_DIR = REPO_ROOT / "prompts"


@pytest.fixture
def workspace(tmp_path):
    """Build a workspace dir + deliverables subdir mirroring blackbox runtime layout."""
    ws = tmp_path / "workspaces" / "bb-multi-identity"
    deliverables = ws / "deliverables"
    deliverables.mkdir(parents=True)
    return ws, deliverables


def _write_manifest(ws: Path, identities: list[dict]) -> None:
    (ws / "identity-manifest.json").write_text(
        json.dumps({"identities": identities}, ensure_ascii=False), encoding="utf-8"
    )


# A 3-identity worked example mirroring T10's primary=first-low / accounts=rest
# (admin + 2 low users; one low user kept unavailable to exercise the
# "NO BASELINE → potential" rule path in the verdict-layer assertion below).
MULTI_IDENTITY_MANIFEST = [
    {"account_id": "primary", "role": "user", "tier": "low",
     "auth_state_file": "auth-state.json", "available": True},
    {"account_id": "victim-b", "role": "user", "tier": "low",
     "auth_state_file": "auth-state-victim-b.json", "available": True},
    {"account_id": "admin-1", "role": "admin", "tier": "high",
     "auth_state_file": "auth-state-admin-1.json", "available": True},
]


# ---------------------------------------------------------------------------
# Integration seam 1: IDENTITY_CONTEXT injection uses the REAL _identities.txt
# partial (not a synthetic stand-in) —— guards the comparison protocol text.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_identity_injects_real_comparison_protocol(workspace):
    """≥2 available identities ⇒ IDENTITY_CONTEXT rendered from REAL _identities.txt.

    Asserts the integration b/w T7 (build_identity_context) and T8 (executor
    injection): the actual repo `_identities.txt` partial is loaded, the three
    identities are listed with their per-account session ids + auth_load_command
    output, AND the comparison_protocol rules (VERTICAL / HORIZONTAL / NO
    BASELINE → potential) appear verbatim. If any of those rules get removed
    from the partial, this test fails —— that is the T11 contract guard.
    """
    ws, deliverables = workspace
    _write_manifest(ws, MULTI_IDENTITY_MANIFEST)

    mock_executor = AsyncMock()
    mock_executor.execute.return_value = AgentMetrics(duration_ms=10)
    # Real PromptManager pointing at the repo's actual prompts dir → loads
    # the real _identities.txt (not the synthetic mini partial from T8 test).
    mock_executor.prompt_manager = PromptManager(str(REAL_PROMPTS_DIR))

    ex = ExploitExecutor(mock_executor)
    await ex.execute(
        agent_name=AgentName.AUTHZ_EXPLOIT, vuln_type="authz",
        workspace_path=ws, deliverables_path=deliverables,
        web_url="https://target.local",
    )

    pv = mock_executor.execute.call_args.kwargs.get("prompt_variables", {})
    ctx = pv.get("IDENTITY_CONTEXT", "")
    assert ctx, "multi-identity manifest must inject non-empty IDENTITY_CONTEXT"

    # T7 contract: all 3 identities listed with per-account session ids.
    # Primary keeps the base session id; others get suffixed slots.
    assert "identity=primary" in ctx
    assert "identity=victim-b" in ctx
    assert "identity=admin-1" in ctx
    # agent-browser engine's auth_load_command emits `state load <path>`
    assert "state load auth-state.json" in ctx
    assert "state load auth-state-victim-b.json" in ctx
    assert "state load auth-state-admin-1.json" in ctx

    # T7 contract: pairwise comparison matrix rendered.
    assert "attacker=" in ctx and "baseline=" in ctx

    # T11 contract: the REAL comparison protocol rules are present verbatim
    # (this is what tells the agent when to emit EXPLOITED vs POTENTIAL).
    # These strings live in prompts/shared/_identities.txt and must survive
    # any future partial edit.
    assert "VERTICAL" in ctx, "comparison_protocol VERTICAL rule missing"
    assert "HORIZONTAL" in ctx, "comparison_protocol HORIZONTAL rule missing"
    assert "potential" in ctx, (
        "comparison_protocol 'NO BASELINE → potential' rule missing — "
        "without it the agent has no signal to downgrade"
    )

    # AUTH_STATE_FILE guard preserved (still injected by AgentExecutor base,
    # NOT by ExploitExecutor explicitly).
    assert "AUTH_STATE_FILE" not in pv


@pytest.mark.asyncio
async def test_single_identity_injects_empty_identity_context(workspace):
    """No manifest file (single-identity scan) ⇒ IDENTITY_CONTEXT == "".

    Authz-exploit behavior unchanged for single-identity scans: the shared
    session prompt is used, no comparison protocol injected. This is the
    zero-regression guard for the multi-identity feature.
    """
    ws, deliverables = workspace  # no _write_manifest call

    mock_executor = AsyncMock()
    mock_executor.execute.return_value = AgentMetrics(duration_ms=10)
    mock_executor.prompt_manager = PromptManager(str(REAL_PROMPTS_DIR))

    ex = ExploitExecutor(mock_executor)
    await ex.execute(
        agent_name=AgentName.AUTHZ_EXPLOIT, vuln_type="authz",
        workspace_path=ws, deliverables_path=deliverables,
        web_url="https://target.local",
    )

    pv = mock_executor.execute.call_args.kwargs.get("prompt_variables", {})
    assert pv.get("IDENTITY_CONTEXT", "") == "", (
        "single-identity (no manifest) must inject empty IDENTITY_CONTEXT"
    )
    assert "AUTH_STATE_FILE" not in pv


@pytest.mark.asyncio
async def test_single_available_identity_in_manifest_also_empty(workspace):
    """Manifest exists but only 1 available ⇒ still "" (T7 <2 available guard)."""
    ws, deliverables = workspace
    _write_manifest(ws, [
        {"account_id": "primary", "role": "user", "tier": "low",
         "auth_state_file": "auth-state.json", "available": True},
        {"account_id": "victim-b", "role": "user", "tier": "low",
         "auth_state_file": "auth-state-victim-b.json", "available": False},
    ])

    mock_executor = AsyncMock()
    mock_executor.execute.return_value = AgentMetrics(duration_ms=10)
    mock_executor.prompt_manager = PromptManager(str(REAL_PROMPTS_DIR))

    ex = ExploitExecutor(mock_executor)
    await ex.execute(
        agent_name=AgentName.AUTHZ_EXPLOIT, vuln_type="authz",
        workspace_path=ws, deliverables_path=deliverables,
        web_url="https://target.local",
    )

    pv = mock_executor.execute.call_args.kwargs.get("prompt_variables", {})
    assert pv.get("IDENTITY_CONTEXT", "") == "", (
        "manifest with <2 available identities must inject empty IDENTITY_CONTEXT"
    )


# ---------------------------------------------------------------------------
# Integration seam 2: verdict path — agent emits EXPLOITED for baseline-backed
# directions and POTENTIAL for no-baseline directions (the behavior the
# comparison protocol in IDENTITY_CONTEXT instructs). validate_exploit_verdicts
# accepts BOTH verdict tiers (T6 discriminated union + T7 protocol + T8 chain).
# ---------------------------------------------------------------------------


def test_verdict_path_accepts_exploited_with_baseline_and_potential_without():
    """Mock agent behavior simulating protocol-driven output: one direction with
    a baseline identity ⇒ EXPLOITED; another direction without a baseline ⇒
    POTENTIAL. validate_exploit_verdicts accepts both.

    This is the closest we can get to the verdict path WITHOUT real GLM:
    instead of running the agent, we feed the collector the structured_output
    a compliant agent WOULD produce given IDENTITY_CONTEXT. If T6's POTENTIAL
    tier or the schema discriminated union regresses, this test fails.
    """
    # Build the IDENTITY_CONTEXT that would be injected for the worked example
    # (3 identities: primary/victim-b/admin-1) — same code path as the executor.
    manifest = IdentityManifest(identities=[
        IdentityRecord(*item) if isinstance(item, tuple) else IdentityRecord(**item)
        for item in MULTI_IDENTITY_MANIFEST
    ])
    pm = PromptManager(str(REAL_PROMPTS_DIR))
    ctx = pm.build_identity_context(manifest, AgentBrowserEngine())
    assert "VERTICAL" in ctx and "potential" in ctx, (
        "precondition: comparison protocol with both rules must be rendered"
    )

    # Mock agent structured_output: simulates what a compliant GLM would emit
    # given the IDENTITY_CONTEXT above.
    #   - AZ-1 (HORIZONTAL primary↔victim-b): baseline victim-b available ⇒ EXPLOITED.
    #   - AZ-2 (a direction whose baseline identity was unavailable): POTENTIAL.
    agent_verdicts = [
        {
            "vulnerability_id": "AZ-1",
            "status": "exploited",
            "severity": "high",
            "impact": "Horizontal privilege escalation: primary session reads victim-b's private memo.",
            "exploitation_steps": [
                "Load victim-b baseline session, capture its private memo id + body.",
                "Switch to primary attacker session, request the same memo id.",
                "Response body matches victim-b baseline ⇒ unauthorized cross-user read.",
            ],
            "proof_of_impact": "victim-b baseline body == primary attacker body for memo id=42",
        },
        {
            "vulnerability_id": "AZ-2",
            "status": "potential",
            "severity": "medium",
            "confidence": "medium",
            "downgrade_reason": (
                "No baseline identity available for this direction "
                "(victim-c login failed → not in manifest). Per comparison_protocol, "
                "successful access without a baseline MUST be reported as potential."
            ),
            "evidence_of_vulnerability": (
                "primary attacker session reached admin-only endpoint /admin/users "
                "but no admin baseline was available to confirm cross-user equivalence."
            ),
        },
    ]

    result = validate_exploit_verdicts(agent_verdicts, valid_ids={"AZ-1", "AZ-2"})

    assert len(result.accepted) == 2, (
        f"both verdicts must be accepted; rejected={result.rejected}"
    )
    assert not result.rejected, f"unexpected rejections: {result.rejected}"

    # Verify each accepted verdict kept its status (discriminated union preserved tier).
    statuses = {v.vulnerability_id: v.status for v in result.accepted}
    assert statuses["AZ-1"] == "exploited"
    assert statuses["AZ-2"] == "potential"


def test_verdict_path_rejects_invalid_potential_missing_downgrade_reason():
    """PotentialVerdict REQUIRES downgrade_reason (T6 schema). A verdict that
    claims status=potential but omits the reason must be rejected — this is the
    L1 schema guard that prevents the agent from silently downgrading without
    justification. Locks the discriminated union's per-tier required fields.
    """
    bad_verdict = {
        "vulnerability_id": "AZ-9",
        "status": "potential",
        "severity": "medium",
        "confidence": "medium",
        # downgrade_reason MISSING
        "evidence_of_vulnerability": "some evidence",
    }
    result = validate_exploit_verdicts([bad_verdict], valid_ids={"AZ-9"})
    assert not result.accepted, "missing downgrade_reason must be rejected"
    assert len(result.rejected) == 1
    assert "L1 schema" in result.rejected[0][1]
