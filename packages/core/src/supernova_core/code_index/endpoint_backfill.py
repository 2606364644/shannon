"""endpoint 确定性回填（spec 2026-09-03 §3 F6-B）。

verdict 后处理：GN 卡 endpoint 缺失（builder join miss 或 verdict 产物退化）时，
从 verdict agent 自己写的 title/source_detail/evidence_chain 提取 "METHOD /path"
提名 → 全量路由白名单验证（index.entry_points 全量构建，绕开 entry_point_map
同 func_block_id 多路由被 dict 折叠的 bug）→ 唯一命中采信回填。

白名单外丢弃 / 多候选歧义不采信（宁缺勿错拼）——防给错误穿确定性外衣。
"""
from __future__ import annotations

import logging

from supernova_core.code_index.gn_collapse import (
    _METHOD_PATH,
    _TRAILING_PUNCT_RE,
    _normalize_placeholders,
)

logger = logging.getLogger(__name__)


def _candidate_labels(text: str) -> set[str]:
    """文本中全部 "METHOD /path" 提名（F1 剥尾标点 + F4 占位符归一后的形态）。"""
    labels = set()
    for m in _METHOD_PATH.finditer(text or ""):
        route = m.group(2).split("?", 1)[0].rstrip("/") or "/"
        route = _normalize_placeholders(_TRAILING_PUNCT_RE.sub("", route)).rstrip("/") or "/"
        labels.add(f"{m.group(1).upper()} {route}")
    return labels


def backfill_endpoints(findings: list, all_routes: set[str]) -> list:
    """endpoint 缺失卡的白名单验证回填；已有值不动，多歧义/白名单外不采信。"""
    # 白名单同样归一化建索引（:userId ↔ {userId} 同义路由互认），
    # 命中后回填白名单原形态（保持 index.entry_points 的实际路由写法）。
    norm_routes = {_normalize_placeholders(r): r for r in all_routes}
    for i, f in enumerate(findings):
        if getattr(f, "endpoint", None):
            continue
        texts = [t for t in (getattr(f, "title", None), getattr(f, "source_detail", None),
                             getattr(f, "evidence_chain", None), getattr(f, "path", None))
                 if isinstance(t, str)]
        found: set[str] = set()
        for t in texts:
            found |= _candidate_labels(t)
        hits = {norm_routes[label] for label in found if label in norm_routes}
        if len(hits) == 1:
            data = f.model_dump()
            data["endpoint"] = next(iter(hits))
            findings[i] = type(f).model_validate(data)
            logger.info("endpoint backfill: %s → %s (llm-nominated, whitelist-verified)",
                        f.ID, data["endpoint"])
        elif len(hits) > 1:
            logger.warning("endpoint backfill: %s 多候选歧义 %s 不采信（宁缺勿错拼）",
                           f.ID, sorted(hits))
    return findings
