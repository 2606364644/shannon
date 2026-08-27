"""阶段 B 裁决编排（spec 2026-08-27 §7）——发现驱动，跑在阶段 A 产物之上。

批粒度容错：单批 Agent 失败/无效 payload/漏判 finding → error 占位卡补位
（direction="error"、conclusion="needs-review"），不静默丢失、不拖垮其它批。
矛盾卡（direction vs conclusion）经 sanitize_adjudication_cards 拦截。
"""
from __future__ import annotations

import json
import logging

from supernova_core.correlation.adjudication import AdjudicationBatch
from supernova_core.correlation.artifacts_guide import build_full_artifacts_guide
from supernova_core.correlation.merge_validation import sanitize_adjudication_cards
from supernova_core.models.agents import AgentName

logger = logging.getLogger(__name__)

_CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string"},
                    "finding_ref": {"type": "object"},
                    "conclusion": {"type": "string"},
                    "cross_service_context": {"type": "string"},
                    "analysis_process": {"type": "array"},
                    "verification_evidence": {"type": "array"},
                    "reasoning": {"type": "string"},
                    "confidence": {"type": "string"},
                },
                "required": ["direction", "finding_ref", "conclusion"],
            },
        }
    },
    "required": ["cards"],
}


def _error_card(batch: AdjudicationBatch, finding: dict, reason: str) -> dict:
    return {
        "direction": "error",
        "finding_ref": {"service": batch.service,
                        "vuln_id": finding.get("ID", ""),
                        "origin": batch.origin},
        "conclusion": "needs-review",
        "cross_service_context": "",
        "analysis_process": [],
        "verification_evidence": [],
        "reasoning": f"adjudication batch failed: {reason}",
        "confidence": "low",
    }


def _error_cards(batch: AdjudicationBatch, reason: str) -> list[dict]:
    return [_error_card(batch, f, reason) for f in batch.findings]


async def run_adjudication_phase(
    *,
    batches: list[AdjudicationBatch],
    artifacts_by_service: dict,
    correlation_context: dict,
    executor,
    sem,
    repo_path: str,
    deliverables_path: str,
    pipeline_testing: bool = False,
    provider_config: dict | None = None,
) -> list[dict]:
    """跑全部裁决批，返回 sanitized 裁决卡列表（含 error 占位卡）。"""
    cards: list[dict] = []

    async def run_batch(batch: AdjudicationBatch) -> list[dict]:
        async with sem:
            metrics = await executor.execute(
                agent_name=AgentName.CROSS_REPO_ADJUDICATION,
                repo_path=repo_path,
                deliverables_path=deliverables_path,
                pipeline_testing=pipeline_testing,
                prompt_variables={
                    "artifacts_guide": build_full_artifacts_guide(
                        artifacts_by_service),
                    "correlation_context": json.dumps(
                        correlation_context, ensure_ascii=False),
                    "batch_json": json.dumps(
                        batch.findings, ensure_ascii=False),
                },
                structured_output_schema=_CARD_SCHEMA,
                provider_config=provider_config,
            )
            payload = getattr(metrics, "structured_output", None)
            if not (isinstance(payload, dict) and isinstance(payload.get("cards"), list)):
                return _error_cards(batch, "invalid structured output")
            return payload["cards"]

    for batch in batches:
        try:
            batch_cards = await run_batch(batch)
        except Exception as e:  # noqa: BLE001 —— 单批失败 → 占位卡,不拖垮其它批
            logger.warning("adjudication batch %s/%s/%s failed: %s",
                           batch.service, batch.vuln_class, batch.origin, e)
            batch_cards = _error_cards(batch, str(e))
        # 漏判补位：批内每个 finding 必须有卡（ID 对齐 finding_ref.vuln_id）
        covered = {c.get("finding_ref", {}).get("vuln_id") for c in batch_cards
                   if isinstance(c, dict)}
        for f in batch.findings:
            if f.get("ID") not in covered:
                batch_cards.append(
                    _error_card(batch, f, "not covered by agent output"))
        cards.extend(c for c in batch_cards if isinstance(c, dict))

    return sanitize_adjudication_cards(cards)
