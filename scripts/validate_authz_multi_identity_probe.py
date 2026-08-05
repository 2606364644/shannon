#!/usr/bin/env python3
"""Probe: GLM 多身份越权对比 能力探针(子项目2 T11 / R1 风险实测).

问题(plan 作者标注的 T11 最大不确定性):
  GLM 在 authz-exploit prompt + 多身份 IDENTITY_CONTEXT 下,能否
  (a) 正确读懂比较协议(VERTICAL / HORIZONTAL / NO BASELINE → potential 规则);
  (b) 按协议给出正确的 verdict status —— 有 baseline 佐证 → exploited,
      无 baseline → potential(永不 exploited);
  (c) (端到端时)多 turn 切 session 做 baseline↔attacker 对比。

设计(诚实局限,对齐 validate_login_success_probe.py 的隔离测试哲学):
  真实 authz-exploit 是多轮浏览器 agent,要有活的 web 靶子 + agent-browser
  才能完整跑端到端(那是 NodeGoat 冒烟的事,不是本探针)。本探针把"判定层"
  独立出来 —— 用 PromptManager.build_identity_context 生成真实 IDENTITY_CONTEXT
  (与 ExploitExecutor T8 走同一条渲染路径),手工喂"客观观察"模拟多 session
  切换后的浏览器状态,让 GLM 按 add_exploit 的 schema 输出 verdict。

  因此本探针测的是:"给定相同的客观观察 + 真实 IDENTITY_CONTEXT,GLM 能否
  按协议给出正确的 status?" 若不能,完整端到端也救不回来 —— 这是 R1 的决定性
  checkpoint。能正确判定 ⇒ 端到端若失败,问题在工具/会话管理,而非协议理解。

场景:
  WITH_BASELINE  —— 横向越权,victim-b baseline 可达且 primary attacker 看到等价
                    数据。期望 status=exploited(HORIZONTAL data matches ⇒ EXPLOITED)。
  NO_BASELINE    —— 纵向越权但 admin baseline 不可用(登录失败)。期望 status=potential
                    (NO BASELINE ⇒ MUST be potential, NEVER exploited)。

PASS:GLM 在 WITH_BASELINE 给 exploited、NO_BASELINE 给 potential(允许 LOGINPROBE_N
      次重复中的统计偏差,默认 N=4)。
FAIL:GLM 在 NO_BASELINE 给 exploited(违反协议最关键的安全规则) 或 WITH_BASELINE
      始终给 blocked/false_positive(读不懂协议) → 触发 plan T11"风险留白"的降级
      评估(简化为 Task 6 方案 B 确定性重放 或 限制 N=2)。

用法:
  python scripts/validate_authz_multi_identity_probe.py [glm-anthropic] [glm-openai] ...
  AUTHZPROBE_N=8 python scripts/validate_authz_multi_identity_probe.py
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES = {
    "glm-anthropic": ROOT / ".env.profiles" / "glm-anthropic.env",
    "glm-openai": ROOT / ".env.profiles" / "glm-openai.env",
}
PROMPTS_DIR = ROOT / "prompts"


def load_profile(name: str) -> None:
    path = PROFILES[name]
    if not path.exists():
        raise FileNotFoundError(f"profile not found: {path}")
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()  # 覆盖:切引擎时凭证对得上


# ── 真实 IDENTITY_CONTEXT(走 T7 build_identity_context 同款渲染路径)──────────
# 与 ExploitExecutor.execute(agent_name=AUTHZ_EXPLOIT, ...) 的产物等价;改 manifest
# 即覆盖子项目2 的 worked example(admin + 2 low users)。
def build_identity_context_for(scenario: str) -> tuple[str, list[dict]]:
    """Return (identity_context_text, available_account_ids) for a scenario.

    Uses the real PromptManager + AgentBrowserEngine — exercises the same
    rendering path the exploit_executor takes (T7 + T8 + agent-browser).
    """
    from supernova_core.prompts.manager import PromptManager
    from supernova_core.services.engines.agent_browser_engine import AgentBrowserEngine
    from supernova_core.services.validate_authentication import (
        IdentityManifest,
        IdentityRecord,
    )

    if scenario == "WITH_BASELINE":
        # Both low-tier identities available ⇒ horizontal comparison has a baseline.
        identities = [
            IdentityRecord("primary", "user", "low",
                           "auth-state.json", True),
            IdentityRecord("victim-b", "user", "low",
                           "auth-state-victim-b.json", True),
        ]
    elif scenario == "NO_BASELINE":
        # ≥2 available identities pass T7's guard, but a specific comparison
        # direction lacks any baseline identity. Concretely: primary (low) +
        # admin-1 (high) are both available, but for resource owner "victim-c"
        # there is NO identity in the manifest at all ⇒ the victim-c direction
        # has no baseline. The manifest format mirrors what T4's preflight loop
        # would emit when one of the configured accounts fails to provide a
        # resource-specific baseline.
        identities = [
            IdentityRecord("primary", "user", "low",
                           "auth-state.json", True),
            IdentityRecord("admin-1", "admin", "high",
                           "auth-state-admin-1.json", True),
        ]
    else:
        raise ValueError(scenario)

    manifest = IdentityManifest(identities=identities)
    pm = PromptManager(str(PROMPTS_DIR))
    ctx = pm.build_identity_context(manifest, AgentBrowserEngine())
    available = [r.account_id for r in identities if r.available]
    return ctx, available


# ── add_exploit verdict schema(复用真实 schema,镜像 collector.py:_SINGLE_VERDICT_SCHEMA)
def verdict_schema() -> dict:
    from supernova_core.collectors.exploit import _SINGLE_VERDICT_SCHEMA
    return _SINGLE_VERDICT_SCHEMA


SCENARIOS = {
    "WITH_BASELINE": {
        "expected_status": "exploited",
        "vulnerability_id": "AZ-H-1",
        "observation": (
            "You have already established the baseline and attacker observations by "
            "switching sessions (state load commands from IDENTITY_CONTEXT):\n\n"
            "BASELINE (victim-b session, loaded via `state load auth-state-victim-b.json`):\n"
            "  - GET /memos/42 -> 200 OK\n"
            "  - Response body: {\"id\": 42, \"owner\": \"victim-b\", "
            "\"body\": \"private memo of victim-b\"}\n\n"
            "ATTACKER (primary session, loaded via `state load auth-state.json`):\n"
            "  - GET /memos/42 -> 200 OK\n"
            "  - Response body: {\"id\": 42, \"owner\": \"victim-b\", "
            "\"body\": \"private memo of victim-b\"}  "
            "(SAME owner field and body as baseline — cross-user read confirmed)\n\n"
            "Per the comparison_protocol, HORIZONTAL direction with matching data "
            "=> exploited. Record your verdict for vulnerability AZ-H-1."
        ),
    },
    "NO_BASELINE": {
        "expected_status": "potential",
        "vulnerability_id": "AZ-V-2",
        "observation": (
            "You are investigating cross-user access to a private resource owned by "
            "\"victim-c\". The IDENTITY_CONTEXT above shows the identities you have "
            "available; there is NO victim-c identity in the manifest (victim-c was "
            "never provisioned), so for the victim-c direction there is no baseline "
            "session to compare against.\n\n"
            "ATTACKER (primary session, loaded via `state load auth-state.json`):\n"
            "  - GET /memos/99 (owner=victim-c) -> 200 OK\n"
            "  - Response body: {\"id\": 99, \"owner\": \"victim-c\", "
            "\"body\": \"victim-c private memo\"}\n"
            "  (primary can read another user's private memo — successful access)\n\n"
            "There is NO victim-c baseline session available — you cannot establish "
            "what victim-c themselves would see. Per the comparison_protocol: "
            "\"NO BASELINE available for a direction MUST be reported as status=potential, "
            "NEVER exploited.\" Record your verdict for vulnerability AZ-V-2."
        ),
    },
}

PROMPT_PREAMBLE = (
    "You are an Authorization Exploitation Specialist operating against "
    "http://target.local with MULTIPLE authenticated sessions. The host has "
    "loaded the following identity set + comparison protocol; you MUST follow "
    "it when deciding verdict status.\n\n"
    "{identity_context}\n\n"
    "Below are the observations from your session-switching actions. Decide the "
    "verdict status STRICTLY per the comparison_protocol rules above.\n\n"
    "{observation}\n\n"
    "Produce your verdict as a JSON object matching the schema. The host will "
    "validate your status against the comparison_protocol — choosing exploited "
    "for the no-baseline direction is a protocol violation."
)


async def run_one(engine: str, scenario: str, idx: int) -> dict:
    from supernova_core.agents.runner import run_claude_prompt
    from supernova_core.agents.tool_audit_logger import NullToolAuditLogger

    sc = SCENARIOS[scenario]
    ctx, _available = build_identity_context_for(scenario)
    prompt = PROMPT_PREAMBLE.format(
        identity_context=ctx, observation=sc["observation"]
    )
    target = Path(tempfile.mkdtemp(prefix=f"authzprobe_{engine}_{scenario}_{idx}_"))
    t0 = time.time()
    try:
        result = await asyncio.wait_for(
            run_claude_prompt(
                prompt=prompt,
                repo_path=str(target),
                model_tier="medium",
                output_format=verdict_schema(),
                max_turns=5,
                tool_audit_logger=NullToolAuditLogger(),
            ),
            timeout=180,
        )
    except asyncio.TimeoutError:
        return {"engine": engine, "scenario": scenario, "idx": idx, "timeout": True}
    dt = time.time() - t0

    so = result.structured_output
    text = result.text or ""
    field_status = None
    field_vid = None
    if isinstance(so, dict):
        field_status = so.get("status")
        field_vid = so.get("vulnerability_id")
    return {
        "engine": engine, "scenario": scenario, "idx": idx,
        "duration": round(dt, 1), "run_success": result.success,
        "field_status": field_status,
        "field_vid": field_vid,
        "so_none": so is None,
        "expected": sc["expected_status"],
        "match": field_status == sc["expected_status"],
        "snippet": text[:200].replace("\n", " "),
        "error": result.error,
    }


def summarize(rows: list[dict]) -> int:
    """Print summary; return 0 if all scenarios match expected, 1 otherwise."""
    valid = [r for r in rows if not r.get("timeout")]
    print("\n" + "=" * 80)
    print(f"汇总  (有效样本 {len(valid)}/{len(rows)})")
    print("=" * 80)
    all_match = True
    for engine in PROFILES:
        for scenario, sc in SCENARIOS.items():
            sub = [r for r in valid if r["engine"] == engine and r["scenario"] == scenario]
            n = len(sub)
            if n == 0:
                continue
            matches = sum(1 for r in sub if r["match"])
            tag = "PASS" if matches == n else "FAIL"
            print(
                f"[{engine}/{scenario}] N={n}  expected={sc['expected_status']}  {tag}  "
                f"({matches}/{n} match)"
            )
            counts: dict[str, int] = {}
            for r in sub:
                k = r["field_status"] or "(none)"
                counts[k] = counts.get(k, 0) + 1
            print(f"    status distribution: {counts}")
            if matches != n:
                all_match = False
                # Highlight protocol violations (the most dangerous case).
                if scenario == "NO_BASELINE":
                    violated = [r for r in sub if r["field_status"] == "exploited"]
                    if violated:
                        print(
                            f"    PROTOCOL VIOLATION: {len(violated)} sample(s) emitted "
                            f"'exploited' for NO_BASELINE direction (must be 'potential')"
                        )
    return 0 if all_match else 1


async def main() -> int:
    engines = sys.argv[1:] or ["glm-anthropic", "glm-openai"]
    n_per = int(os.getenv("AUTHZPROBE_N", "4"))
    rows: list[dict] = []
    for engine in engines:
        load_profile(engine)
        for scenario in SCENARIOS:
            for idx in range(n_per):
                r = await run_one(engine, scenario, idx)
                rows.append(r)
                if r.get("timeout"):
                    print(f"[{engine}/{scenario} #{idx}] TIMEOUT (>180s)")
                else:
                    mark = "OK" if r["match"] else "MISMATCH"
                    print(
                        f"[{engine}/{scenario} #{idx}] status={r['field_status']} "
                        f"expected={r['expected']} -> {mark}  "
                        f"({r['duration']}s){' ERR=' + r['error'] if r['error'] else ''}"
                    )
                    print(f"    …{r['snippet']}")
    return summarize(rows)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
