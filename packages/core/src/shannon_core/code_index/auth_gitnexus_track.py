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

from shannon_core.code_index.models import CodeIndex, FuncBlock

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
    """跑所有已实装检查器。"""
    ep_to_route = {ep.func_block_id: f"{ep.http_method or 'ANY'} {ep.route}" for ep in index.entry_points if ep.route}
    candidates: list[AuthCandidate] = []
    for handler in handlers:
        endpoint = ep_to_route.get(handler.id)
        for checker in (_check_session_regenerate_missing,
                        _check_logout_destroy_missing,
                        _check_password_hash_missing,
                        _check_jwt_verify_missing):  # T3 追加 3 检查器
            c = checker(handler, index, endpoint)
            if c is not None:
                candidates.append(c)
    return candidates


# —— 检查器原语规则表（spike §3.1 / §2.5）—— per-language
# MISSING_POSITIVE 型：应调用的安全原语；handler 内 + 可达路径均无 → 候选
_SESSION_REGENERATE_PRIMITIVES = {
    "typescript": re.compile(r"\b(session\.regenerate)\b", re.IGNORECASE),
    "javascript": re.compile(r"\b(session\.regenerate)\b", re.IGNORECASE),
    # Go session 库多样（gorilla/chi/gin-session），首批后置（spec R4）
}


def _handler_has_primitive(handler: FuncBlock, pattern: re.Pattern | None) -> str | None:
    """handler 源码命中 pattern → 返回命中片段；否则 None。"""
    if pattern is None:
        return None
    m = pattern.search(handler.source_code)
    return m.group(0) if m else None


def _path_has_primitive(handler_id: str, index: CodeIndex, pattern: re.Pattern | None) -> bool:
    """可达路径（CallChain）上是否有 pattern 命中（session.regenerate 在 helper 里）。

    GitNexus 不可用（chains 空）时退化为 False（只看 handler 内）。
    """
    if pattern is None:
        return False
    blocks_by_id = {b.id: b for b in index.blocks}
    for chain in index.chains:
        if chain.entry_point_id != handler_id:
            continue
        for nid in chain.path:
            blk = blocks_by_id.get(nid)
            if blk and pattern.search(blk.source_code):
                return True
    return False


def _is_login_success_handler(handler: FuncBlock, endpoint: str | None) -> bool:
    """该 handler 是否"登录成功写 session"型（login/oauth callback/token 签发等）。

    启发式：endpoint 含 login/callback/token/signin/auth/oauth，或源码写 ctx.session/req.session。
    """
    if endpoint:
        if re.search(r"(login|callback|token|signin|auth|oauth)", endpoint, re.IGNORECASE):
            return True
    return bool(re.search(r"\b(req|ctx)\.session\.\w+\s*=", handler.source_code, re.IGNORECASE))


def _check_session_regenerate_missing(
    handler: FuncBlock, index: CodeIndex, endpoint: str | None,
) -> AuthCandidate | None:
    """检查器 1：登录成功 handler 内（+可达路径）无 session.regenerate → session 固定候选。

    首批 Node.js only（Go session 库多样后置，spec R4）。
    """
    if handler.language not in ("typescript", "javascript"):
        return None
    if not _is_login_success_handler(handler, endpoint):
        return None
    pattern = _SESSION_REGENERATE_PRIMITIVES[handler.language]
    if _handler_has_primitive(handler, pattern):
        return None  # 有 regenerate，不报
    if _path_has_primitive(handler.id, index, pattern):
        return None  # 可达路径有，不报
    return AuthCandidate(
        id=f"{handler.id}:session_regenerate_missing:{handler.start_line}",
        handler_id=handler.id,
        endpoint=endpoint,
        check_type=AuthCheckType.SESSION_REGENERATE_MISSING,
        verdict_signal=VerdictSignal.MISSING_POSITIVE,
        evidence_callee="session.regenerate",
        expected="登录成功后调用 session.regenerate 轮换会话 ID（防 session 固定）",
        file_path=handler.file_path,
        line=handler.start_line,
        code_snippet=handler.source_code[:200],
        confidence="high",
    )


# —— T3 检查器原语规则表（spike §3.1 / §2.5）—— per-language
_LOGOUT_DESTROY_PRIMITIVES = {
    "typescript": re.compile(r"\b(session\.destroy|session\.remove|req\.logout)\b", re.IGNORECASE),
    "javascript": re.compile(r"\b(session\.destroy|session\.remove|req\.logout)\b", re.IGNORECASE),
}
_PASSWORD_HASH_PRIMITIVES = {
    "typescript": re.compile(r"\b(bcrypt|argon2|pbkdf2|scrypt)\b", re.IGNORECASE),
    "javascript": re.compile(r"\b(bcrypt|argon2|pbkdf2|scrypt)\b", re.IGNORECASE),
    "go": re.compile(r"\b(bcrypt|argon2id|crypto\.scrypt)\b", re.IGNORECASE),
    "python": re.compile(r"\b(bcrypt|argon2|pbkdf2|scrypt|werkzeug|passlib)\b", re.IGNORECASE),
}
_JWT_VERIFY_PRIMITIVES = {
    "typescript": re.compile(r"\b(jwt\.verify|jsonwebtoken\.verify)\b", re.IGNORECASE),
    "javascript": re.compile(r"\b(jwt\.verify|jsonwebtoken\.verify)\b", re.IGNORECASE),
    "go": re.compile(r"\b(jwt\.Parse|jwt\.Verify|ParseWithClaims)\b", re.IGNORECASE),
    "python": re.compile(r"\b(jwt\.decode|jwt\.verify)\b", re.IGNORECASE),
}
_PASSWORD_WRITE_RE = re.compile(r"(password|passwd|pwd)", re.IGNORECASE)
_IDTOKEN_USE_RE = re.compile(r"\b(id_token|idToken|openid|oidc)\b", re.IGNORECASE)


def _check_logout_destroy_missing(
    handler: FuncBlock, index: CodeIndex, endpoint: str | None,
) -> AuthCandidate | None:
    """检查器 2：有 login 但无 logout/destroy（全库）→ 候选。Node.js only 首批。

    登录成功型 handler 触发；全库（所有 blocks）无 session.destroy / req.logout → 候选。
    """
    if handler.language not in ("typescript", "javascript"):
        return None
    if not _is_login_success_handler(handler, endpoint):
        return None
    pattern = _LOGOUT_DESTROY_PRIMITIVES[handler.language]
    # 全库无 logout/destroy 原语 → 候选
    has_anywhere = any(pattern.search(b.source_code) for b in index.blocks)
    if has_anywhere:
        return None
    return AuthCandidate(
        id=f"{handler.id}:logout_destroy_missing:{handler.start_line}",
        handler_id=handler.id,
        endpoint=endpoint,
        check_type=AuthCheckType.LOGOUT_DESTROY_MISSING,
        verdict_signal=VerdictSignal.MISSING_POSITIVE,
        evidence_callee="session.destroy / req.logout",
        expected="提供 logout 端点并在服务端销毁 session（撤销机制）",
        file_path=handler.file_path,
        line=handler.start_line,
        code_snippet=handler.source_code[:200],
        confidence="high",
    )


def _check_password_hash_missing(
    handler: FuncBlock, index: CodeIndex, endpoint: str | None,
) -> AuthCandidate | None:
    """检查器 3：signup/reset handler 写密码但无 hash 原语 → 明文存储候选。

    仅在 signup/register/reset/password 类端点；handler 源码命中 password 写入且
    handler 内 + 可达路径均无 hash 原语 → 候选。
    """
    lang = handler.language
    pattern = _PASSWORD_HASH_PRIMITIVES.get(lang)
    if pattern is None:
        return None
    if not (endpoint and re.search(r"(signup|register|reset|password)", endpoint, re.IGNORECASE)):
        return None
    if not _PASSWORD_WRITE_RE.search(handler.source_code):
        return None  # 不碰密码，不报
    if _handler_has_primitive(handler, pattern):
        return None
    if _path_has_primitive(handler.id, index, pattern):
        return None
    return AuthCandidate(
        id=f"{handler.id}:password_hash_missing:{handler.start_line}",
        handler_id=handler.id,
        endpoint=endpoint,
        check_type=AuthCheckType.PASSWORD_HASH_MISSING,
        verdict_signal=VerdictSignal.MISSING_POSITIVE,
        evidence_callee="bcrypt/argon2/scrypt",
        expected="密码写入存储前经密码学 hash（bcrypt/argon2/scrypt）",
        file_path=handler.file_path,
        line=handler.start_line,
        code_snippet=handler.source_code[:200],
        confidence="high",
    )


def _check_jwt_verify_missing(
    handler: FuncBlock, index: CodeIndex, endpoint: str | None,
) -> AuthCandidate | None:
    """检查器 4：OIDC handler 用 id_token 但无 jwt.verify → 未验签候选。

    仅在 oauth/oidc/callback/token 类端点；handler 源码命中 id_token/openid 用法且
    handler 内 + 可达路径均无 jwt.verify/ParseWithClaims → 候选。
    """
    lang = handler.language
    pattern = _JWT_VERIFY_PRIMITIVES.get(lang)
    if pattern is None:
        return None
    if not (endpoint and re.search(r"(oauth|oidc|callback|token)", endpoint, re.IGNORECASE)):
        return None
    if not _IDTOKEN_USE_RE.search(handler.source_code):
        return None  # 不碰 id_token，不报
    if _handler_has_primitive(handler, pattern):
        return None
    if _path_has_primitive(handler.id, index, pattern):
        return None
    return AuthCandidate(
        id=f"{handler.id}:jwt_verify_missing:{handler.start_line}",
        handler_id=handler.id,
        endpoint=endpoint,
        check_type=AuthCheckType.JWT_VERIFY_MISSING,
        verdict_signal=VerdictSignal.MISSING_POSITIVE,
        evidence_callee="jwt.verify/ParseWithClaims",
        expected="OIDC id_token 需本地验签 + 校验 iss/aud/exp/sub claims",
        file_path=handler.file_path,
        line=handler.start_line,
        code_snippet=handler.source_code[:200],
        confidence="high",
    )


def _render_auth_candidates(candidates: list[AuthCandidate]) -> str:
    """T5 完善表格；T1 占位（judge activity T5 才接入）。"""
    if not candidates:
        return "（auth GitNexus 轨：0 候选。端点识别或检查器未命中。）"
    lines = ["## Auth GitNexus Track — auth 逻辑类候选（确定性，待 LLM 判定）", ""]
    for c in candidates:
        lines.append(f"- `{c.check_type.value}` @ `{c.endpoint or c.handler_id}` "
                     f"({c.file_path}:{c.line}) — {c.expected}")
    return "\n".join(lines)
