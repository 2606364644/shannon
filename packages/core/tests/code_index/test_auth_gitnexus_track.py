from shannon_core.code_index.auth_gitnexus_track import (
    AuthCandidate, AuthCheckType, VerdictSignal,
    build_auth_gitnexus_track, _identify_auth_handlers, _run_checkers,
)
from shannon_core.code_index.models import CodeIndex, EntryPoint, FuncBlock


def _blk(bid, name="h", source="", decorators=None, language="typescript"):
    return FuncBlock(
        id=bid, file_path=bid.split(":")[0], function_name=name,
        start_line=1, end_line=10, source_code=source,
        parameters=[], class_name=None, decorators=decorators or [],
        language=language,
    )


def _index(blocks, entry_points=None):
    """构造最小 CodeIndex（字段以 models.py 实际为准：无 func_blocks，用 blocks）。"""
    return CodeIndex(
        repository="r", language="typescript",
        total_blocks=len(blocks), total_entry_points=len(entry_points or []),
        total_chains=0,
        blocks=blocks, edges=[], entry_points=entry_points or [], chains=[],
    )


def test_identify_auth_handlers_route_signal():
    """信号 1：路由正则识别 auth 端点（login/logout/oauth/callback 等）。"""
    blk = _blk("app.ts:login", name="login",
               source="router.post('/login', (req,res) => {})",
               decorators=[])
    ep = EntryPoint(func_block_id=blk.id, entry_type="http_route",
                    route="/login", http_method="POST",
                    confidence=0.9, evidence="route", needs_llm_review=False)
    index = _index([blk], [ep])
    handlers = _identify_auth_handlers(index)
    assert blk.id in [h.id for h in handlers], "路由 /login 应识别为 auth handler"


def test_identify_auth_handlers_function_name_signal():
    """信号 2：函数名语义（login/logout/auth/passkey/token）。"""
    blk = _blk("a.ts:loginUser", name="loginUser", source="function loginUser(){}")
    index = _index([blk])
    handlers = _identify_auth_handlers(index)
    assert blk.id in [h.id for h in handlers], "函数名 loginUser 含 login 应识别"


def test_identify_auth_handlers_reverse_signal():
    """信号 3：反向定位——handler 内调 auth 原语（ctx.session.* / bcrypt / jwt.verify）。"""
    blk = _blk("a.ts:cb", name="callback",
               source="function cb(ctx){ ctx.session.regenerate(); }")
    index = _index([blk])
    handlers = _identify_auth_handlers(index)
    assert blk.id in [h.id for h in handlers], "调 ctx.session.* 反向定位为 auth handler"


def test_auth_candidate_model_fields():
    """AuthCandidate schema（spike §4.1）。"""
    c = AuthCandidate(
        id="a.ts:h:session_regenerate_missing:5", handler_id="a.ts:h",
        endpoint="POST /login", check_type=AuthCheckType.SESSION_REGENERATE_MISSING,
        verdict_signal=VerdictSignal.MISSING_POSITIVE,
        evidence_callee="ctx.session.regenerate", expected="登录成功后调用 regenerate",
        file_path="a.ts", line=5, code_snippet="ctx.session.user = ...",
        confidence="high", needs_deep_agent=True,
    )
    assert c.check_type == AuthCheckType.SESSION_REGENERATE_MISSING
    assert c.needs_deep_agent is True


def test_session_regenerate_missing_nodejs():
    """Node.js login handler 内无 ctx.session.regenerate → MISSING_POSITIVE 候选。"""
    # OAuth callback handler 写 session 但无 regenerate（moa-auth #1 场景）
    handler = _blk("app.ts:callback", name="callback",
                   source="function callback(ctx){ ctx.session.user = userInfo; ctx.redirect(ref); }")
    ep = EntryPoint(func_block_id=handler.id, entry_type="http_route",
                    route="/callback", http_method="GET", confidence=0.9,
                    evidence="route", needs_llm_review=False)
    index = _index([handler], [ep])
    cands = _run_checkers(index, _identify_auth_handlers(index))
    regen = [c for c in cands if c.check_type == AuthCheckType.SESSION_REGENERATE_MISSING]
    assert len(regen) == 1, f"应产 1 个 session_regenerate_missing 候选，实际 {len(regen)}"
    assert regen[0].verdict_signal == VerdictSignal.MISSING_POSITIVE
    assert regen[0].confidence == "high"


def test_session_regenerate_present_no_finding():
    """handler 内有 ctx.session.regenerate → 不产候选（避免误报）。"""
    handler = _blk("app.ts:login", name="login",
                   source="function login(ctx){ ctx.session.regenerate(); ctx.session.user = u; }")
    ep = EntryPoint(func_block_id=handler.id, entry_type="http_route", route="/login",
                    http_method="POST", confidence=0.9, evidence="r", needs_llm_review=False)
    index = _index([handler], [ep])
    cands = _run_checkers(index, _identify_auth_handlers(index))
    assert not any(c.check_type == AuthCheckType.SESSION_REGENERATE_MISSING for c in cands), \
        "有 regenerate 调用不应产缺失候选"
