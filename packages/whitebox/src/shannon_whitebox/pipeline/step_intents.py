"""Step intent registry — single source of truth for whitebox phase steps.

Each declared step carries a human-readable intent so the live display can tell
the user *what* a step is doing (not just its slug). Consumed by:
  * workflows.py — log_phase_start_activity (names + intents for the dashboard)
  * activities.py — track_step(intent=...) on each deterministic step (wired in Task 8)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StepSpec:
    name: str
    intent: str


PHASE_STEPS: dict[str, tuple[StepSpec, ...]] = {
    "setup": (
        StepSpec("preflight",          "预检（环境 / 依赖就绪性）"),
        StepSpec("credential-check",   "校验 API 凭证"),
        StepSpec("auth-validation",    "验证目标鉴权链路"),
    ),
    "pre-recon": (
        StepSpec("code-index",          "构建调用图与代码索引"),
        StepSpec("pre-recon",           "扫描应用架构、入口点与 sink"),
        StepSpec("merge-sinks",         "合并确定性 sink 与 LLM 发现"),
        StepSpec("entry-point-fusion",  "融合确定性入口点与 LLM 发现"),
        StepSpec("adjudication",        "按置信度裁决入口点"),
        StepSpec("framework-analysis",  "检测 REST 框架并推断端点"),
        StepSpec("frontend-mapping",    "映射前端路由到 API、识别 XSS 链"),
        StepSpec("route-chain-building", "构建攻击路由链"),
    ),
    "recon": (
        StepSpec("recon", "侦察目标运行时与外部信息"),
    ),
    "risk-scoring": (
        StepSpec("risk-scoring",   "打分与风险排序"),
        StepSpec("dataflow-hints", "生成数据流提示"),
    ),
    "vulnerability-analysis": (
        StepSpec("merge-dual-track", "双轨合并 LLM/GitNexus 漏洞队列"),
        StepSpec("auth-config-scan", "确定性认证配置扫描(cookie/HSTS/CORS/JWT/限流)"),
        StepSpec("auth-gitnexus-judge", "auth GitNexus 轨候选多轮深度判定"),
    ),
    "attack-chain": (
        StepSpec("attack-chain-assembly", "组装攻击链"),
    ),
    "reporting": (
        StepSpec("render-findings",   "渲染漏洞条目(若存在队列)"),
        StepSpec("assemble-report",   "拼接各分项报告"),
        StepSpec("run-report-agent",  "撰写执行摘要并清理报告"),
    ),
}


def step_names(phase: str) -> tuple[str, ...]:
    """Step slugs for a phase (consumed by log_phase_start_activity)."""
    return tuple(s.name for s in PHASE_STEPS[phase])


def step_intents(phase: str) -> tuple[str, ...]:
    """Human intents parallel to step_names (consumed by the dashboard pin)."""
    return tuple(s.intent for s in PHASE_STEPS[phase])


_INTENT_BY_NAME: dict[str, str] = {
    s.name: s.intent for specs in PHASE_STEPS.values() for s in specs
}


def intent_for(name: str) -> str | None:
    """Resolve a step slug to its intent, or None if unknown."""
    return _INTENT_BY_NAME.get(name)
