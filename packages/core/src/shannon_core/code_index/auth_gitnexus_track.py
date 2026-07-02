"""auth GitNexus track: deterministic auth-logic candidate generation.

Three-signal auth handler identification + 6 checkers → AuthCandidate list.
spike report (2026-07-02-auth-deterministic-candidate-model-spike-report.md)
confirmed: missing/misuse-type auth defects are deterministically detectable
with high precision (binary signals); business-logic types (fail-open / race)
remain to the LLM track.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from shannon_core.code_index.models import CodeIndex, EntryPoint, FuncBlock
from shannon_core.code_index.parsers import get_parser

logger = logging.getLogger(__name__)


class AuthCheckType(str, Enum):
    SESSION_REGENERATE_MISSING = "session_regenerate_missing"
    LOGOUT_DESTROY_MISSING = "logout_destroy_missing"
    PASSWORD_HASH_MISSING = "password_hash_missing"
    JWT_VERIFY_MISSING = "jwt_verify_missing"
    WEAK_RANDOM_TOKEN = "weak_random_token"
    OAUTH_STATE_MISSING = "oauth_state_missing"
    USER_ENUMERATION_DIFF_ERROR = "user_enumeration_diff_error"  # 后置


class VerdictSignal(str, Enum):
    MISSING_POSITIVE = "missing_positive"
    NEGATIVE_SINK_HIT = "negative_sink_hit"
    SEMANTIC_SUSPECT = "semantic_suspect"


@dataclass(frozen=True)
class AuthCandidate:
    id: str
    handler_id: str
    endpoint: str | None
    check_type: AuthCheckType
    verdict_signal: VerdictSignal
    evidence_callee: str | None
    expected: str
    file_path: str
    line: int
    code_snippet: str
    confidence: str  # "high" | "medium" | "low"
    needs_deep_agent: bool = True


class AuthTrackBuildResult(NamedTuple):
    markdown: str
    candidates: list[AuthCandidate]
    handler_count: int
    entry_point_total: int


# —— 三信号端点识别（spike §5）——

# 信号 1：路由正则（扩 _AUTH_ROUTE_RE，加 logout/callback/authorize + 框架语法）
_AUTH_ROUTE_PATTERN = re.compile(
    r"(?:/|^)(login|logout|signin|signup|register|reset|recover|forgot|"
    r"token|oauth|callback|authorize|auth|session)\b",
    re.IGNORECASE,
)

# 信号 2：函数名语义
_AUTH_FUNCNAME_PATTERN = re.compile(
    r"\b(login|logout|signin|signup|authenticate|authorize|"
    r"passwd|password|token|oauth|sso|callback|session|register)\w*",
    re.IGNORECASE,
)

# 信号 3：反向定位——handler 内调用 auth 原语（session 操作 / 密码 hash / OAuth / token 签发验签）
_AUTH_PRIMITIVE_PATTERNS = [
    re.compile(r"\b(session\.(regenerate|destroy|save|login|logout))\b", re.IGNORECASE),
    re.compile(r"\b(bcrypt|argon2|pbkdf2|scrypt)\b", re.IGNORECASE),
    re.compile(r"\b(jwt\.(sign|verify|decode)|jsonwebtoken)\b", re.IGNORECASE),
    re.compile(r"\b(req\.session|ctx\.session)\b", re.IGNORECASE),
    re.compile(r"\b(passport|oauth|oidc)\b", re.IGNORECASE),
]


def _handler_calls_auth_primitive(handler: FuncBlock) -> str | None:
    """信号 3：handler 源码（或 AST iter_calls）命中 auth 原语 → 返回命中的原语片段。"""
    for pat in _AUTH_PRIMITIVE_PATTERNS:
        m = pat.search(handler.source_code)
        if m:
            return m.group(0)
    return None


def _identify_auth_handlers(index: CodeIndex) -> list[FuncBlock]:
    """三信号融合识别 auth handler（spike §5）。

    信号 1：EntryPoint.route 匹配 auth 路由关键词。
    信号 2：函数名匹配 auth 语义。
    信号 3（最鲁棒）：handler 源码命中 auth 原语调用。
    任一信号命中即收（取并集，去重 by FuncBlock.id）。
    """
    blocks_by_id: dict[str, FuncBlock] = {b.id: b for b in index.blocks}
    ep_to_route: dict[str, str] = {}
    for ep in index.entry_points:
        if ep.route:
            ep_to_route[ep.func_block_id] = f"{ep.http_method or 'ANY'} {ep.route}"

    seen: set[str] = set()
    handlers: list[FuncBlock] = []
    for blk in index.blocks:
        bid = blk.id
        if bid in seen:
            continue
        hit = False
        # 信号 1
        route = ep_to_route.get(bid)
        if route and _AUTH_ROUTE_PATTERN.search(route.split(" ", 1)[-1]):
            hit = True
        # 信号 2
        if not hit and _AUTH_FUNCNAME_PATTERN.search(blk.function_name):
            hit = True
        # 信号 3
        if not hit and _handler_calls_auth_primitive(blk):
            hit = True
        if hit:
            seen.add(bid)
            handlers.append(blk)
    return handlers


def build_auth_gitnexus_track(deliverables_dir: str) -> AuthTrackBuildResult:
    """读 code_index.json → 三信号识别 auth handler → 跑检查器 → 产候选 + markdown。

    检查器在 T2-T4 加入；本 task (T1) 只建骨架（端点识别 + 空候选 + 诊断）。
    """
    import json
    out = Path(deliverables_dir)
    code_index_path = out / "code_index.json"
    if not code_index_path.exists():
        logger.warning("code_index.json not found in %s; auth track skips", deliverables_dir)
        return AuthTrackBuildResult(markdown="", candidates=[], handler_count=0, entry_point_total=0)
    index = CodeIndex.model_validate_json(code_index_path.read_text())

    handlers = _identify_auth_handlers(index)
    # 检查器（T2-T4 填充 _run_checkers）
    candidates: list[AuthCandidate] = _run_checkers(index, handlers)

    markdown = _render_auth_candidates(candidates)  # T5 完善表格；T1 占位
    return AuthTrackBuildResult(
        markdown=markdown,
        candidates=candidates,
        handler_count=len(handlers),
        entry_point_total=len(index.entry_points),
    )


def _run_checkers(index: CodeIndex, handlers: list[FuncBlock]) -> list[AuthCandidate]:
    """跑 6 检查器。T1 返回空（占位）；T2-T4 实装。"""
    return []


def _render_auth_candidates(candidates: list[AuthCandidate]) -> str:
    """T5 完善表格；T1 占位（judge activity T5 才接入）。"""
    if not candidates:
        return "（auth GitNexus 轨：0 候选。端点识别或检查器未命中。）"
    lines = ["## Auth GitNexus Track — auth 逻辑类候选（确定性，待 LLM 判定）", ""]
    for c in candidates:
        lines.append(f"- `{c.check_type.value}` @ `{c.endpoint or c.handler_id}` "
                     f"({c.file_path}:{c.line}) — {c.expected}")
    return "\n".join(lines)
