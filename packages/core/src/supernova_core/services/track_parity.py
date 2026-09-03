"""双轨呈现一致性编排（spec 2026-08-26 §6）。

GitNexus 轨侧的轻量 LLM 配对层（同 chain_verdict 轻量判定 / llm-discovered
sink 模式——确定性兜底 + LLM 增强；不触碰「确定性产物不喂 LLM 轨 vuln
agent prompt」铁律，输入是 GN 自己的产物）：

**配对归并**（§6.1 + §5.1 ①归并终审）：确定性 key 配不上的同洞卡（sink
粒度/称谓不同、跨接口存储型链各见半条），每 class 一次 LLM 批量比对，仅
high 置信对应用——两种形态：``mode=merge`` 并成 both（复用 merger both 分支
字段融合）或 ``mode=attach`` 挂靠（LLM 卡为主体，GN 卡 ID 写入主体卡
``merged_from``、不再独立出现；不改主体判定字段）。

配对后仍 gitnexus-only 的卡不做 merge 内逐卡补全（原 §6.2 轻量档，随
SUPERNOVA_GN_ENRICH_MODE 档位开关 2026-08-31 整键移除而删）：叙事/评级
全字段由 merge 后独立深度富化 step（whitebox run_gn_finding_enrichment，
多轮 agent 读码）承担。

LLM 不可用（raise / 超时 / 输出不可解析）优雅退化：维持确定性 merge 结果，
不阻塞报告（渲染层确定性文案兜底路径已存在）。
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from supernova_core.code_index.dual_track_merger import (
    apply_pairing_merge,
    build_pairing_prompt,
    parse_pairing_response,
)
from supernova_core.models.queue_schemas import Vulnerability

logger = logging.getLogger(__name__)

LlmClient = Callable[..., Awaitable[str]]


async def enhance_track_parity(
    merged: list[Vulnerability],
    llm_client: LlmClient,
) -> list[Vulnerability]:
    """merge activity 内的编排入口（确定性 merge 之后、落盘之前）。

    配对（每 class 一次；结果两种形态——merge 成 both 或 attach 挂靠
    ``merged_from``，见 dual_track_merger.apply_pairing_merge）。LLM 失败/
    不可解析都优雅退化（log + 保持现状 = 确定性 key 配对结果），绝不抛出——
    报告管线不因增强层阻塞（spec §6/§5.6 退化口径）。单侧空时零调用（成本守门）。"""
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
                high = [p for p in pairs if p.confidence == "high"]
                if not high:
                    logger.warning(
                        "track-parity: %d 对 LLM 判定均 <high> 无配对应用 "
                        "(llm_only=%d gn_only=%d)", len(pairs), len(llm_only), len(gn_only))
                merged = apply_pairing_merge(merged, pairs)
            else:
                logger.warning(
                    "track-parity: LLM 返回 0 对（解析失败或全被过滤）"
                    "(llm_only=%d gn_only=%d)", len(llm_only), len(gn_only))
        except Exception as exc:  # noqa: BLE001 — 增强层不阻塞
            logger.warning("track-parity pairing skipped (LLM unavailable): %s", exc)
    return merged
