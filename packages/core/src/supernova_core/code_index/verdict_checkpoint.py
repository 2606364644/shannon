"""chain_verdict 逐链 checkpoint（2026-08-28 事故修）。

run_gitnexus_chain_verdict activity 超时重试 / 组合扫描 resume 曾从零重跑
全部链（2026-08-27 NodeGoat 事故：27+31 条链每条累计被判定 ~5 遍）。本模块
让每条链的判定结果即时落盘，重跑按链指纹跳过已判链——重试/resume 成本从
「全量 × N」降为「只跑残余链」。

护栏（2026-08-28 用户要求：不得陷入消耗模型资源的怪圈）：
- 缓存命中零 LLM 调用（严格只减不增消耗）；
- 损坏文件 / 畸形条目 / 落盘失败一律降级为 miss（重判该链），绝不抛异常
  阻断判定流程、绝不循环。

指纹 = (vuln_class, flow_id, sink_call_site_id, source_param) 的 sha1——同一
pgraph 产物跨次稳定；不用候选序号（顺序变化不错配）。unadjudicated（预算
护栏的保守占位）不落盘：它不是真判定，重跑预算放开后应真判。
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from supernova_core.utils.atomic_write import atomic_write_json

if TYPE_CHECKING:  # 仅类型标注；运行时 lazy import 防循环依赖
    from supernova_core.code_index.chain_verdict import (
        CandidateChain,
        ChainVerdict,
    )

logger = logging.getLogger(__name__)


def chain_fingerprint(chain: "CandidateChain") -> str:
    """链身份指纹：同一确定性 pgraph 产物跨次运行稳定。

    取判定语义的稳定四元组（vuln_class/flow_id/sink/source），不含
    propagation_steps 等长列表（同链内容恒定，无需整链 hash）。
    """
    raw = "|".join((
        getattr(chain, "vuln_class", "") or "",
        getattr(chain, "flow_id", "") or "",
        getattr(chain, "sink_call_site_id", "") or "",
        getattr(chain, "source_param", "") or "",
    ))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class VerdictCheckpoint:
    """逐链判定 checkpoint：内存 dict + 每次_put 全量原子写盘。

    一个 gather 一个实例（活动侧按 vc 一文件一实例），无跨 gather 共享 →
    无并发写竞争；gather 内 asyncio 单线程，put 顺序天然串行。
    """

    def __init__(self, path: Path, verdicts: dict[str, dict] | None = None):
        self._path = Path(path)
        self._verdicts: dict[str, dict] = verdicts or {}

    @classmethod
    def load(cls, path: Path) -> "VerdictCheckpoint":
        """读侧入口：文件缺失/损坏/非预期结构 → 空 store（当 miss 重判）。"""
        p = Path(path)
        if not p.exists():
            return cls(p)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("verdict-checkpoint: 损坏按空处理 (%s): %r", p.name, exc)
            return cls(p)
        if not isinstance(data, dict):
            logger.warning("verdict-checkpoint: 非预期结构按空处理 (%s)", p.name)
            return cls(p)
        clean = {k: v for k, v in data.items()
                 if isinstance(k, str) and isinstance(v, dict)}
        return cls(p, clean)

    def get(self, chain: "CandidateChain") -> "ChainVerdict | None":
        """缓存命中 → ChainVerdict；miss/条目畸形 → None（绝不抛）。"""
        from supernova_core.code_index.chain_verdict import ChainVerdict

        raw = self._verdicts.get(chain_fingerprint(chain))
        if raw is None:
            return None
        try:
            return ChainVerdict(**raw)
        except TypeError:
            # 字段缺失/多余/类型错（旧版本残留等）→ 当 miss 重判
            return None

    def put(self, chain: "CandidateChain", verdict: "ChainVerdict") -> None:
        """记录判定并落盘；落盘失败仅 warning（流程不受阻，退化为无 checkpoint）。"""
        from dataclasses import asdict

        self._verdicts[chain_fingerprint(chain)] = asdict(verdict)
        try:
            atomic_write_json(self._path, self._verdicts)
        except OSError as exc:
            logger.warning(
                "verdict-checkpoint: 落盘失败（本次运行内存内仍有效）: %r", exc)
