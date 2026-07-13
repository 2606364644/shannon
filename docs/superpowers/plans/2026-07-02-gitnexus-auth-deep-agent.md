> ⚠️ auth 部分已回退 2026-07-14（对齐原始 shannon：`auth_config_scanner` 踩 §1 铁律「确定性产物不喂 LLM 轨 prompt」+ CORS 越界被裁的 misconfig；authz GitNexus 轨保留。详见 plan zazzy-roaming-shamir / memory auth-gitnexus-track-reverted）

# auth GitNexus 轨深度 agent（spec-2b） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 在 GitNexus 轨确定性生成 auth 逻辑类候选（缺失/误用型），经多轮深度 agent 判定，产 `auth_gitnexus_queue.json`（与 config_scan 同 queue 追加），让关 LLM 轨时 GitNexus 轨独立兜底 auth 缺失/误用型缺陷（业务逻辑型仍靠 LLM 轨）。

**Architecture:** 独立 `auth_gitnexus_track.py`（类比 `authz_gitnexus_track.py`，不扩 config_scanner / 不塞 sink_rules——语义反转）。三信号识别 auth handler（路由正则 + 函数名语义 + 反向定位 handler 内 auth 原语）→ 6 检查器（MISSING_POSITIVE / NEGATIVE_SINK_HIT / 缺失型）→ `AuthCandidate`。新建 `run_auth_gitnexus_judge` activity（候选>0 多轮 `run_gitnexus_verdict_agent` / ==0 自主探索，对标 `run_authz_gitnexus_judge`），追加写 `auth_gitnexus_queue.json`（config_scan 已先写）。首批 Node.js 全 6 检查器 + Go 4 检查器（session 类后置，库多样）。

**Tech Stack:** Python / tree-sitter AST（`get_parser`）/ temporalio / pytest / 双引擎

**Spec:** `docs/superpowers/specs/2026-07-02-gitnexus-auth-deep-agent-design.md`（定稿，基于 spike 报告 `2026-07-02-auth-deterministic-candidate-model-spike-report.md`）

## Global Constraints

- **不改 `auth_config_scan`**（config 类已覆盖）；**不改 LLM 轨 `vuln-auth.txt`**（保留为可选增强，业务逻辑型仍靠它）；**不改双轨 merger**（`auth_gitnexus_queue.json` schema 不变，追加非覆盖）。
- **首批范围**：6 检查器（SESSION_REGENERATE_MISSING / LOGOUT_DESTROY_MISSING / PASSWORD_HASH_MISSING / JWT_VERIFY_MISSING / WEAK_RANDOM_TOKEN / OAUTH_STATE_MISSING）；`USER_ENUMERATION_DIFF_ERROR` 后置（low confidence）。Node.js 全 6 + Go 4（session 类 regenerate/logout 后置——库多样无统一 API）。
- **双轨铁律**：本 plan 是确定性层（AST 候选生成 + 读 code_index）+ 吃自己产的候选做深度判定（GitNexus 轨本职），不喂确定性产物给 LLM 轨 prompt。
- **测试**：`uv run pytest <path> -v`，**只跑改动相关文件**（全套有预存 hang）。
- **commit**：conventional commits；`git add` 只 named 文件。
- **候选全标 `needs_deep_agent=True`**：auth 逻辑类确定性弱于 authz，交深度 agent 深判（conservative）。
- **queue 追加**：`run_auth_gitnexus_judge` 读 config_scan 产的 `auth_gitnexus_queue.json` + 追加逻辑类（非覆盖）。

---

## File Structure

| 文件 | 责任 | 本 plan 改动 |
|---|---|---|
| `packages/core/src/shannon_core/code_index/auth_gitnexus_track.py` | auth 候选生成 track（新） | T1 新建骨架+端点识别；T2-T4 检查器；T5 render |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | activity | T5 `run_auth_gitnexus_judge` 判定段；T6 探索段 |
| `prompts/auth_gitnexus_judge.txt` | 判定 prompt（新） | T5 新建 |
| `prompts/auth_gitnexus_explore.txt` | 探索 prompt（新） | T6 新建 |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | workflow 编排 | T6 插入 `run_auth_gitnexus_judge`（auth_config_scan 后） |
| `packages/core/tests/code_index/test_auth_gitnexus_track.py` | track 测试（新） | T1-T5 测试 |
| `packages/whitebox/tests/pipeline/test_auth_gitnexus_judge.py` | judge activity 测试（新） | T5-T6 测试 |
| `packages/whitebox/tests/pipeline/test_workflows_safety.py` | 编排回归锚点 | T6 扩 |

---

### Task 1: AuthCandidate 模型 + auth_gitnexus_track 骨架 + 三信号端点识别

**Files:**
- Create: `packages/core/src/shannon_core/code_index/auth_gitnexus_track.py`
- Test: `packages/core/tests/code_index/test_auth_gitnexus_track.py`（新建）

**Interfaces:**
- Consumes：`CodeIndex`（`models.py`，entry_points/blocks/source_points/chains）、`FuncBlock`、`get_parser(language)`（`parsers/__init__.py:10`）、`EntryPoint`
- Produces：`AuthCandidate`（dataclass）、`AuthCheckType`/`VerdictSignal`（Enum）、`AuthTrackBuildResult`（NamedTuple）、`build_auth_gitnexus_track(deliverables_dir) -> AuthTrackBuildResult`、`_identify_auth_handlers(index) -> list[FuncBlock]`（三信号）

- [ ] **Step 1: Write failing tests**

```python
# packages/core/tests/code_index/test_auth_gitnexus_track.py
from shannon_core.code_index.auth_gitnexus_track import (
    AuthCandidate, AuthCheckType, VerdictSignal,
    build_auth_gitnexus_track, _identify_auth_handlers,
)
from shannon_core.code_index.models import CodeIndex, EntryPoint, FuncBlock


def _blk(bid, name="h", source="", decorators=None, language="typescript"):
    return FuncBlock(
        id=bid, file_path=bid.split(":")[0], function_name=name,
        start_line=1, end_line=10, source_code=source,
        parameters=[], class_name=None, decorators=decorators or [],
        language=language,
    )


def test_identify_auth_handlers_route_signal():
    """信号 1：路由正则识别 auth 端点（login/logout/oauth/callback 等）。"""
    blk = _blk("app.ts:login", name="login",
               source="router.post('/login', (req,res) => {})",
               decorators=[])
    # 构造最小 CodeIndex（entry_points 含一条 login 路由）
    ep = EntryPoint(func_block_id=blk.id, entry_type="http_route",
                    route="/login", http_method="POST",
                    confidence=0.9, evidence="route", needs_llm_review=False)
    index = CodeIndex(repository="r", language="typescript", func_blocks=[blk],
                      entry_points=[ep], source_points=[], sink_call_sites=[],
                      blocks=[blk], edges=[], chains=[],
                      total_blocks=1, total_entry_points=1)
    handlers = _identify_auth_handlers(index)
    assert blk.id in [h.id for h in handlers], "路由 /login 应识别为 auth handler"


def test_identify_auth_handlers_function_name_signal():
    """信号 2：函数名语义（login/logout/auth/passkey/token）。"""
    blk = _blk("a.ts:loginUser", name="loginUser", source="function loginUser(){}")
    index = CodeIndex(repository="r", language="typescript", func_blocks=[blk],
                      entry_points=[], source_points=[], sink_call_sites=[],
                      blocks=[blk], edges=[], chains=[], total_blocks=1, total_entry_points=0)
    handlers = _identify_auth_handlers(index)
    assert blk.id in [h.id for h in handlers], "函数名 loginUser 含 login 应识别"


def test_identify_auth_handlers_reverse_signal():
    """信号 3：反向定位——handler 内调 auth 原语（ctx.session.* / bcrypt / jwt.verify）。"""
    blk = _blk("a.ts:cb", name="callback",
               source="function cb(ctx){ ctx.session.regenerate(); }")
    index = CodeIndex(repository="r", language="typescript", func_blocks=[blk],
                      entry_points=[], source_points=[], sink_call_sites=[],
                      blocks=[blk], edges=[], chains=[], total_blocks=1, total_entry_points=0)
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
```

> `CodeIndex`/`FuncBlock`/`EntryPoint` 必填字段以 `models.py` 实际为准（fixture 按实际补全，**不删断言**）。

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/code_index/test_auth_gitnexus_track.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.code_index.auth_gitnexus_track`。

- [ ] **Step 3: Write the implementation**

`packages/core/src/shannon_core/code_index/auth_gitnexus_track.py`（新建）：

```python
"""auth GitNexus track: deterministic auth-logic candidate generation.

Three-signal auth handler identification + 6 checkers → AuthCandidate list.
spike report (2026-07-02-auth-deterministic-candidate-model-spike-report.md)
confirmed: missing/misuse-type auth defects are deterministically detectable
with high precision (binary signals); business-logic types (fail-open / race)
remain to the LLM track.
"""

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/code_index/test_auth_gitnexus_track.py -v`
Expected: PASS（4 测试）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/auth_gitnexus_track.py packages/core/tests/code_index/test_auth_gitnexus_track.py
git commit -m "feat(auth): auth_gitnexus_track 骨架 + 三信号端点识别 + AuthCandidate 模型（spec-2b T1）"
```

---

### Task 2: SESSION_REGENERATE_MISSING 检查器 + 端到端原型验证（spike §9）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/auth_gitnexus_track.py`（`_run_checkers` + 新增 `_check_session_regenerate_missing`）
- Test: `packages/core/tests/code_index/test_auth_gitnexus_track.py`（扩）

**Interfaces:**
- Consumes：T1 的 `AuthCandidate`/`AuthCheckType`/`_identify_auth_handlers`；`get_parser(language)`；`CallChain`（可达性，GitNexus 不可用时退 handler 内 only）
- Produces：`_check_session_regenerate_missing(handler, index, parser) -> AuthCandidate | None`；`_run_checkers` 接入首个检查器

**spike §9 原型验证**：moa-auth（`/root/code/frontend/moa-auth`）`ctx.session.regenerate` 全代码库 0 调用 → 应产 1 候选（对照基准 moa-auth `auth_exploitation_queue.json` AUTH-VULN-01）。

- [ ] **Step 1: Write failing tests**

```python
# test_auth_gitnexus_track.py 追加
def test_session_regenerate_missing_nodejs():
    """Node.js login handler 内无 ctx.session.regenerate → MISSING_POSITIVE 候选。"""
    # OAuth callback handler 写 session 但无 regenerate（moa-auth #1 场景）
    handler = _blk("app.ts:callback", name="callback",
                   source="function callback(ctx){ ctx.session.user = userInfo; ctx.redirect(ref); }")
    ep = EntryPoint(func_block_id=handler.id, entry_type="http_route",
                    route="/callback", http_method="GET", confidence=0.9,
                    evidence="route", needs_llm_review=False)
    index = CodeIndex(repository="r", language="typescript", func_blocks=[handler],
                      entry_points=[ep], source_points=[], sink_call_sites=[],
                      blocks=[handler], edges=[], chains=[],
                      total_blocks=1, total_entry_points=1)
    from shannon_core.code_index.auth_gitnexus_track import _run_checkers, _identify_auth_handlers
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
    index = CodeIndex(repository="r", language="typescript", func_blocks=[handler],
                      entry_points=[ep], source_points=[], sink_call_sites=[],
                      blocks=[handler], edges=[], chains=[], total_blocks=1, total_entry_points=1)
    from shannon_core.code_index.auth_gitnexus_track import _run_checkers, _identify_auth_handlers
    cands = _run_checkers(index, _identify_auth_handlers(index))
    assert not any(c.check_type == AuthCheckType.SESSION_REGENERATE_MISSING for c in cands), \
        "有 regenerate 调用不应产缺失候选"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/code_index/test_auth_gitnexus_track.py::test_session_regenerate_missing_nodejs -v`
Expected: FAIL — T1 `_run_checkers` 返回空。

- [ ] **Step 3: Write the implementation**

`auth_gitnexus_track.py` 加检查器 + 接入 `_run_checkers`：

```python
# 检查器原语规则表（spike §3.1 / §2.5）—— per-language
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
    """该 handler 是否"登录成功写 session"型（login/oauth callback/token 签发）。

    启发式：endpoint 含 login/callback/token/signin/auth，或源码写 ctx.session/req.session。
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


def _run_checkers(index: CodeIndex, handlers: list[FuncBlock]) -> list[AuthCandidate]:
    """跑所有已实装检查器。"""
    ep_to_route = {ep.func_block_id: f"{ep.http_method or 'ANY'} {ep.route}" for ep in index.entry_points if ep.route}
    candidates: list[AuthCandidate] = []
    for handler in handlers:
        endpoint = ep_to_route.get(handler.id)
        for checker in (_check_session_regenerate_missing,):  # T3/T4 追加
            c = checker(handler, index, endpoint)
            if c is not None:
                candidates.append(c)
    return candidates
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/code_index/test_auth_gitnexus_track.py -v`
Expected: PASS（T1 4 + T2 2 = 6）。

- [ ] **Step 5: 端到端原型验证（spike §9，对 moa-auth）**

Run（手动/探针）: 写一个临时探针或在测试里读 moa-auth 的 code_index（若已产）。若 moa-auth 无 code_index.json，本 step 标"待真机白盒跑产 code_index 后验证"（不阻塞 commit）：
```bash
# 若 moa-auth 有 code_index.json（路径 /root/code/frontend/moa-auth/.shannon/deliverables/whitebox/code_index.json）：
uv run python -c "
from shannon_core.code_index.auth_gitnexus_track import build_auth_gitnexus_track
r = build_auth_gitnexus_track('/root/code/frontend/moa-auth/.shannon/deliverables/whitebox')
print('handlers:', r.handler_count, 'candidates:', len(r.candidates))
print([c.check_type.value for c in r.candidates])
"
# 期望：含 session_regenerate_missing（对照 moa-auth #1）
```
> 若 moa-auth 无现成 code_index.json（未跑过白盒），本 step 记为"待真机冒烟"，T2 commit 不阻塞——单测已证检查器逻辑正确。

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/auth_gitnexus_track.py packages/core/tests/code_index/test_auth_gitnexus_track.py
git commit -m "feat(auth): SESSION_REGENERATE_MISSING 检查器 + Node.js session 固定检测（spec-2b T2）"
```

---

### Task 3: LOGOUT_DESTROY_MISSING + PASSWORD_HASH_MISSING + JWT_VERIFY_MISSING 检查器

**Files:**
- Modify: `auth_gitnexus_track.py`（加 3 检查器 + 接入 `_run_checkers`）
- Test: `test_auth_gitnexus_track.py`（扩）

**Interfaces:**
- Consumes：T2 检查器模式（`_handler_has_primitive` / `_path_has_primitive`）
- Produces：`_check_logout_destroy_missing` / `_check_password_hash_missing` / `_check_jwt_verify_missing`

- [ ] **Step 1: Write failing tests**

```python
# 追加：每检查器一 missing + 一 present 用例（同 T2 模式）
def test_logout_destroy_missing_nodejs():
    """login 端点存在但无 logout 端点 / 无 session.destroy → 候选（moa-auth #9）。"""
    # 构造 index：有 /login 无 /logout，且全库无 session.destroy
    handler = _blk("app.ts:login", name="login", source="function login(ctx){ctx.session.user=u}")
    ep = EntryPoint(func_block_id=handler.id, entry_type="http_route", route="/login",
                    http_method="POST", confidence=0.9, evidence="r", needs_llm_review=False)
    index = CodeIndex(repository="r", language="typescript", func_blocks=[handler],
                      entry_points=[ep], source_points=[], sink_call_sites=[],
                      blocks=[handler], edges=[], chains=[], total_blocks=1, total_entry_points=1)
    from shannon_core.code_index.auth_gitnexus_track import _run_checkers, _identify_auth_handlers
    cands = _run_checkers(index, _identify_auth_handlers(index))
    assert any(c.check_type == AuthCheckType.LOGOUT_DESTROY_MISSING for c in cands)


def test_password_hash_missing_nodejs():
    """signup handler 写密码但无 bcrypt/argon2 → 明文存储候选。"""
    handler = _blk("app.ts:signup", name="signup",
                   source="function signup(ctx){ const pwd = ctx.request.body.password; db.save({pwd}); }")
    ep = EntryPoint(func_block_id=handler.id, entry_type="http_route", route="/signup",
                    http_method="POST", confidence=0.9, evidence="r", needs_llm_review=False)
    index = CodeIndex(repository="r", language="typescript", func_blocks=[handler],
                      entry_points=[ep], source_points=[], sink_call_sites=[],
                      blocks=[handler], edges=[], chains=[], total_blocks=1, total_entry_points=1)
    from shannon_core.code_index.auth_gitnexus_track import _run_checkers, _identify_auth_handlers
    cands = _run_checkers(index, _identify_auth_handlers(index))
    assert any(c.check_type == AuthCheckType.PASSWORD_HASH_MISSING for c in cands)


def test_jwt_verify_missing_nodejs():
    """OIDC callback handler 用 id_token 但无 jwt.verify → 未验签候选（futu #13 类）。"""
    handler = _blk("app.ts:oidc", name="oidcCallback",
                   source="function oidcCallback(ctx){ const claims = decode(id_token); ctx.session.sub = claims.sub; }")
    ep = EntryPoint(func_block_id=handler.id, entry_type="http_route", route="/oauth/callback",
                    http_method="GET", confidence=0.9, evidence="r", needs_llm_review=False)
    index = CodeIndex(repository="r", language="typescript", func_blocks=[handler],
                      entry_points=[ep], source_points=[], sink_call_sites=[],
                      blocks=[handler], edges=[], chains=[], total_blocks=1, total_entry_points=1)
    from shannon_core.code_index.auth_gitnexus_track import _run_checkers, _identify_auth_handlers
    cands = _run_checkers(index, _identify_auth_handlers(index))
    assert any(c.check_type == AuthCheckType.JWT_VERIFY_MISSING for c in cands)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/code_index/test_auth_gitnexus_track.py -v -k "logout_destroy or password_hash or jwt_verify"`
Expected: FAIL — 3 检查器未实装。

- [ ] **Step 3: Write the implementation**

`auth_gitnexus_track.py` 加 3 检查器 + 原语规则 + 接入 `_run_checkers`：

```python
# —— 原语规则表（per-language）——
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


def _check_logout_destroy_missing(handler, index, endpoint):
    """检查器 2：有 login 但无 logout/destroy（全库） → 候选。Node.js only 首批。"""
    if handler.language not in ("typescript", "javascript"):
        return None
    # 只在 login 类 handler 触发（一次登录态写入手法）
    if not _is_login_success_handler(handler, endpoint):
        return None
    pattern = _LOGOUT_DESTROY_PRIMITIVES[handler.language]
    # 全库无 logout/destroy → 候选
    has_anywhere = any(pattern.search(b.source_code) for b in index.blocks)
    if has_anywhere:
        return None
    return AuthCandidate(
        id=f"{handler.id}:logout_destroy_missing:{handler.start_line}",
        handler_id=handler.id, endpoint=endpoint,
        check_type=AuthCheckType.LOGOUT_DESTROY_MISSING,
        verdict_signal=VerdictSignal.MISSING_POSITIVE,
        evidence_callee="session.destroy / req.logout",
        expected="提供 logout 端点并在服务端销毁 session（撤销机制）",
        file_path=handler.file_path, line=handler.start_line,
        code_snippet=handler.source_code[:200], confidence="high",
    )


def _check_password_hash_missing(handler, index, endpoint):
    """检查器 3：signup/reset handler 写密码但无 hash 原语 → 明文存储候选。"""
    lang = handler.language
    pattern = _PASSWORD_HASH_PRIMITIVES.get(lang)
    if pattern is None:
        return None
    # 只在 signup/reset/register 类 handler
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
        handler_id=handler.id, endpoint=endpoint,
        check_type=AuthCheckType.PASSWORD_HASH_MISSING,
        verdict_signal=VerdictSignal.MISSING_POSITIVE,
        evidence_callee="bcrypt/argon2/scrypt",
        expected="密码写入存储前经密码学 hash（bcrypt/argon2/scrypt）",
        file_path=handler.file_path, line=handler.start_line,
        code_snippet=handler.source_code[:200], confidence="high",
    )


def _check_jwt_verify_missing(handler, index, endpoint):
    """检查器 4：OIDC handler 用 id_token 但无 jwt.verify → 未验签候选。"""
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
        handler_id=handler.id, endpoint=endpoint,
        check_type=AuthCheckType.JWT_VERIFY_MISSING,
        verdict_signal=VerdictSignal.MISSING_POSITIVE,
        evidence_callee="jwt.verify/ParseWithClaims",
        expected="OIDC id_token 需本地验签 + 校验 iss/aud/exp/sub claims",
        file_path=handler.file_path, line=handler.start_line,
        code_snippet=handler.source_code[:200], confidence="high",
    )


# _run_checkers 的 for 循环 checker 列表改为：
#   for checker in (_check_session_regenerate_missing, _check_logout_destroy_missing,
#                   _check_password_hash_missing, _check_jwt_verify_missing):
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/code_index/test_auth_gitnexus_track.py -v`
Expected: PASS（T1-T2 6 + T3 3 = 9）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/auth_gitnexus_track.py packages/core/tests/code_index/test_auth_gitnexus_track.py
git commit -m "feat(auth): logout/password_hash/jwt_verify 检查器（spec-2b T3）"
```

---

### Task 4: WEAK_RANDOM_TOKEN + OAUTH_STATE_MISSING 检查器

**Files:**
- Modify: `auth_gitnexus_track.py`（加 2 检查器 + 接入 `_run_checkers`）
- Test: `test_auth_gitnexus_track.py`（扩）

**Interfaces:**
- Consumes：NEGATIVE_SINK_HIT 型（误用弱原语）+ 缺失型（grep 零出现）
- Produces：`_check_weak_random_token`（negative sink）/ `_check_oauth_state_missing`（缺失）

- [ ] **Step 1: Write failing tests**

```python
def test_weak_random_token_nodejs():
    """token/reset handler 用 Math.random 生成 token → NEGATIVE_SINK_HIT 候选。"""
    handler = _blk("app.ts:reset", name="reset",
                   source="function reset(ctx){ const token = Math.random().toString(36); db.save({token}); }")
    ep = EntryPoint(func_block_id=handler.id, entry_type="http_route", route="/reset",
                    http_method="POST", confidence=0.9, evidence="r", needs_llm_review=False)
    index = CodeIndex(repository="r", language="typescript", func_blocks=[handler],
                      entry_points=[ep], source_points=[], sink_call_sites=[],
                      blocks=[handler], edges=[], chains=[], total_blocks=1, total_entry_points=1)
    from shannon_core.code_index.auth_gitnexus_track import _run_checkers, _identify_auth_handlers
    cands = _run_checkers(index, _identify_auth_handlers(index))
    weak = [c for c in cands if c.check_type == AuthCheckType.WEAK_RANDOM_TOKEN]
    assert len(weak) == 1 and weak[0].verdict_signal == VerdictSignal.NEGATIVE_SINK_HIT


def test_oauth_state_missing_nodejs():
    """OAuth callback handler 内无 state 校验 → 缺失候选（futu #12 类）。"""
    handler = _blk("app.ts:cb", name="oauthCallback",
                   source="function oauthCallback(ctx){ const code = ctx.query.code; const token = exchange(code); ctx.session.user = token; }")
    ep = EntryPoint(func_block_id=handler.id, entry_type="http_route", route="/oauth/callback",
                    http_method="GET", confidence=0.9, evidence="r", needs_llm_review=False)
    index = CodeIndex(repository="r", language="typescript", func_blocks=[handler],
                      entry_points=[ep], source_points=[], sink_call_sites=[],
                      blocks=[handler], edges=[], chains=[], total_blocks=1, total_entry_points=1)
    from shannon_core.code_index.auth_gitnexus_track import _run_checkers, _identify_auth_handlers
    cands = _run_checkers(index, _identify_auth_handlers(index))
    assert any(c.check_type == AuthCheckType.OAUTH_STATE_MISSING for c in cands)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/code_index/test_auth_gitnexus_track.py -v -k "weak_random or oauth_state"`
Expected: FAIL。

- [ ] **Step 3: Write the implementation**

```python
_WEAK_RANDOM_PRIMITIVES = {
    "typescript": re.compile(r"\b(Math\.random|crypto\.pseudoRandomBytes)\b", re.IGNORECASE),
    "javascript": re.compile(r"\b(Math\.random|crypto\.pseudoRandomBytes)\b", re.IGNORECASE),
    "go": re.compile(r"math/rand\b"),  # 非_crypto/rand（futu #6）
    "python": re.compile(r"\brandom\.(random|randint|choice)\b"),
}
_TOKEN_GEN_CONTEXT_RE = re.compile(r"(token|secret|nonce|reset|otp|code)", re.IGNORECASE)
_OAUTH_HANDLER_RE = re.compile(r"(oauth|oidc|callback|authorize)", re.IGNORECASE)
_OAUTH_SECURITY_RE = re.compile(r"\b(state|nonce|code_challenge|code_verifier|pkce)\b", re.IGNORECASE)


def _check_weak_random_token(handler, index, endpoint):
    """检查器 5：token/reset/otp handler 用 Math.random/math.rand → NEGATIVE_SINK_HIT。"""
    lang = handler.language
    pattern = _WEAK_RANDOM_PRIMITIVES.get(lang)
    if pattern is None:
        return None
    # 只在 token 生成语境（reset/otp/token/nonce handler）
    if not (endpoint and re.search(_TOKEN_GEN_CONTEXT_RE, endpoint)):
        return None
    m = pattern.search(handler.source_code)
    if not m:
        return None
    return AuthCandidate(
        id=f"{handler.id}:weak_random_token:{handler.start_line}",
        handler_id=handler.id, endpoint=endpoint,
        check_type=AuthCheckType.WEAK_RANDOM_TOKEN,
        verdict_signal=VerdictSignal.NEGATIVE_SINK_HIT,
        evidence_callee=m.group(0),
        expected="token/secret/reset token 用密码学安全随机（crypto.randomBytes / crypto/rand / randomUUID）",
        file_path=handler.file_path, line=handler.start_line,
        code_snippet=handler.source_code[:200], confidence="high",
    )


def _check_oauth_state_missing(handler, index, endpoint):
    """检查器 6：OAuth callback handler 内无 state/nonce/PKCE → 缺失候选。"""
    if not (endpoint and _OAUTH_HANDLER_RE.search(endpoint)):
        return None
    # handler 内 + 可达路径有无 state/nonce/code_challenge
    if _OAUTH_SECURITY_RE.search(handler.source_code):
        return None
    blocks_by_id = {b.id: b for b in index.blocks}
    for chain in index.chains:
        if chain.entry_point_id != handler.id:
            continue
        if any(_OAUTH_SECURITY_RE.search(blocks_by_id[n].source_code)
               for n in chain.path if n in blocks_by_id):
            return None
    return AuthCandidate(
        id=f"{handler.id}:oauth_state_missing:{handler.start_line}",
        handler_id=handler.id, endpoint=endpoint,
        check_type=AuthCheckType.OAUTH_STATE_MISSING,
        verdict_signal=VerdictSignal.MISSING_POSITIVE,
        evidence_callee="state/nonce/code_challenge",
        expected="OAuth callback 校验 state（CSRF）+ nonce（replay）+ PKCE（code 拦截）",
        file_path=handler.file_path, line=handler.start_line,
        code_snippet=handler.source_code[:200], confidence="high",
    )


# _run_checkers checker 列表追加：_check_weak_random_token, _check_oauth_state_missing
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/code_index/test_auth_gitnexus_track.py -v`
Expected: PASS（9 + T4 2 = 11）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/auth_gitnexus_track.py packages/core/tests/code_index/test_auth_gitnexus_track.py
git commit -m "feat(auth): weak_random_token + oauth_state_missing 检查器（spec-2b T4）"
```

---

### Task 5: render + `run_auth_gitnexus_judge` activity 判定段 + judge prompt + 追加 queue

**Files:**
- Modify: `auth_gitnexus_track.py`（完善 `_render_auth_candidates` 表格）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（加 `run_auth_gitnexus_judge` activity，判定段对标 `run_authz_gitnexus_judge:337-374`）
- Create: `prompts/auth_gitnexus_judge.txt`
- Test: `packages/whitebox/tests/pipeline/test_auth_gitnexus_judge.py`（新建）

**Interfaces:**
- Consumes：T1-T4 的 `build_auth_gitnexus_track`；`run_gitnexus_verdict_agent`（spec-0，:926-971）；`VulnerabilityQueue.parse_lenient`；`atomic_write_json`；`run_auth_config_scan` 先产的 `auth_gitnexus_queue.json`
- Produces：`run_auth_gitnexus_judge(input) -> dict`（candidate_count>0 多轮判定；**读现有 queue + 追加逻辑类**，非覆盖）

- [ ] **Step 1: Write failing test**

```python
# packages/whitebox/tests/pipeline/test_auth_gitnexus_judge.py
import json
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_auth_judge_multiturn_when_candidates_and_appends_queue(tmp_path, monkeypatch):
    """candidate_count>0 → run_gitnexus_verdict_agent 多轮；queue 追加（非覆盖）config_scan 的条目。"""
    import shannon_whitebox.pipeline.activities as act

    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    # config_scan 先产的 queue（2 条 config 类）
    (deliverables / "auth_gitnexus_queue.json").write_text(json.dumps({"vulnerabilities": [
        {"ID": "AUTH-GN-COOKIE-1", "vulnerability_type": "Session_Management_Flaw",
         "externally_exploitable": True, "confidence": "medium", "source_track": "gitnexus"},
        {"ID": "AUTH-GN-HSTS-1", "vulnerability_type": "Transport_Exposure",
         "externally_exploitable": True, "confidence": "medium", "source_track": "gitnexus"},
    ]}))

    # build_auth_gitnexus_track 产 1 个 session_regenerate_missing 候选
    from shannon_core.code_index.auth_gitnexus_track import AuthTrackBuildResult, AuthCandidate, AuthCheckType, VerdictSignal
    fake_cand = AuthCandidate(id="a:h:session_regenerate_missing:1", handler_id="a:h",
        endpoint="POST /login", check_type=AuthCheckType.SESSION_REGENERATE_MISSING,
        verdict_signal=VerdictSignal.MISSING_POSITIVE, evidence_callee="session.regenerate",
        expected="regen", file_path="a.ts", line=1, code_snippet="...", confidence="high")
    fake_result = AuthTrackBuildResult(markdown="## 候选", candidates=[fake_cand],
                                       handler_count=1, entry_point_total=1)
    monkeypatch.setattr("shannon_core.code_index.auth_gitnexus_track.build_auth_gitnexus_track",
                        lambda d: fake_result)

    verdict_called = {"n": 0}
    async def fake_verdict(*, prompt, repo_path, structured_output_schema=None, audit_session=None):
        verdict_called["n"] += 1
        r = MagicMock()
        r.structured_output = {"vulnerabilities": [{"ID": "AUTH-GN-LOGIC-1",
            "vulnerability_type": "Session_Management_Flaw", "externally_exploitable": True,
            "confidence": "high"}]}
        r.text = "{}"
        return r
    monkeypatch.setattr(act, "run_gitnexus_verdict_agent", fake_verdict)
    monkeypatch.setattr(act, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    inp = MagicMock(); inp.workspace_name = "ws"; inp.api_key = None

    await act.run_auth_gitnexus_judge(inp)
    assert verdict_called["n"] == 1, "应用多轮 verdict_agent"

    # queue 应有 3 条：2 config（保留）+ 1 逻辑（追加）
    q = json.loads((deliverables / "auth_gitnexus_queue.json").read_text())
    assert len(q["vulnerabilities"]) == 3, f"queue 应追加到 3 条，实际 {len(q['vulnerabilities'])}"
```

> ActivityInput 必填字段、`_get_paths` mock 以 repo 实际为准（**不删断言**：verdict_called==1 + queue 3 条）。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_auth_gitnexus_judge.py::test_auth_judge_multiturn_when_candidates_and_appends_queue -v`
Expected: FAIL — `run_auth_gitnexus_judge` 不存在。

- [ ] **Step 3: Write the implementation**

(a) `auth_gitnexus_track.py` 完善 `_render_auth_candidates`（表格，对标 `render_authz_gitnexus_candidates`）：

```python
def _render_auth_candidates(candidates: list[AuthCandidate]) -> str:
    if not candidates:
        return "（auth GitNexus 轨：0 候选。端点识别或检查器未命中，交探索 agent。）"
    lines = [
        "## Auth GitNexus Track — auth 逻辑类候选（确定性，待 LLM 判定）",
        "",
        "| Endpoint | Check Type | Signal | Evidence | Expected | Handler 片段 |",
        "|---|---|---|---|---|---|",
    ]
    for c in candidates:
        lines.append(
            f"| `{c.endpoint or c.handler_id}` | `{c.check_type.value}` | "
            f"{c.verdict_signal.value} | `{c.evidence_callee or '—'}` | "
            f"{c.expected} | `{c.code_snippet[:120]}` |"
        )
    lines += ["", "### 判定指令：对每条候选产一条 AuthVulnerability（保守，不确定判 vulnerable）。"]
    return "\n".join(lines)
```

(b) `prompts/auth_gitnexus_judge.txt`（新建，对标 `authz_gitnexus_judge.txt`）：

```
<role>
You are an Authentication Verdict Judge. You are given auth-logic candidates
produced by deterministic GitNexus analysis (missing/misuse-type: session
fixation, plaintext password, weak random, JWT not verified, OAuth state
missing). Confirm or reject each as an auth vulnerability, based ONLY on the
evidence.
</role>

<objective>
For EACH candidate, emit one AuthVulnerability verdict. Conservative: when
unclear whether the missing control is exploitable, judge vulnerable.
</objective>

<input>
{{AUTH_GITNEXUS_CANDIDATES}}
</input>

<output_format>
JSON only:
{
  "vulnerabilities": [
    {
      "ID": "AUTH-GN-LOGIC-NN",
      "vulnerability_type": "Session_Management_Flaw | Login_Flow_Logic | Token_Management_Issue | OAuth_Flow_Issue",
      "externally_exploitable": true,
      "source_endpoint": "POST /login",
      "vulnerable_code_location": "file:line",
      "missing_defense": "what control is absent/misused",
      "exploitation_hypothesis": "how an attacker exploits it",
      "suggested_exploit_technique": "session_fixation | plaintext_password | weak_token | oauth_csrf | ...",
      "confidence": "high | medium | low",
      "notes": "candidate source: session_regenerate_missing | ... | rejected: <reason>"
    }
  ]
}

Rules:
- Emit ONE entry per candidate. Rejected: externally_exploitable=false + reason in notes
  (e.g. "session.regenerate called via helper X" / "equivalent rotation primitive found").
- Tag notes with the candidate's check_type.
- Zero candidates → {"vulnerabilities": []}.
</output_format>
```

(c) `activities.py` 加 `run_auth_gitnexus_judge`（判定段，对标 `run_authz_gitnexus_judge:337-374`）：

```python
@activity.defn
async def run_auth_gitnexus_judge(input: ActivityInput) -> dict:
    """auth GitNexus 轨：候选多轮深度判定 + 追加 auth_gitnexus_queue.json。"""
    from shannon_whitebox.audit.session_registry import get_audit_session
    from shannon_core.code_index.auth_gitnexus_track import build_auth_gitnexus_track
    from shannon_core.models.queue_schemas import VulnerabilityQueue
    from shannon_core.utils.atomic_write import atomic_write_json
    from pathlib import Path
    import json
    try:
        _session = get_audit_session()
        repo, deliverables, _ = _get_paths(input)
        async with _session.track_step("authz", "auth-gitnexus-judge",
                                       intent=intent_for("auth-gitnexus-judge")):
            md, candidates, handler_count, entry_point_total = build_auth_gitnexus_track(str(deliverables))
            candidate_count = len(candidates)
            vulnerabilities: list[dict] = []

            if candidate_count > 0:
                prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
                prompt_manager = PromptManager(prompts_dir)
                prompt = prompt_manager.load_sync(
                    "auth_gitnexus_judge",
                    variables={"AUTH_GITNEXUS_CANDIDATES": md},
                )
                result = await run_gitnexus_verdict_agent(
                    prompt=prompt, repo_path=str(repo), audit_session=_session,
                    structured_output_schema={"type": "object",
                        "properties": {"vulnerabilities": {"type": "array"}}},
                )
                raw = result.structured_output
                if raw is None and result.text:
                    raw = result.text
                parsed = VulnerabilityQueue.parse_lenient(
                    raw if isinstance(raw, str) else json.dumps(raw) if raw is not None else "{}")
                for v in parsed.queue.vulnerabilities:
                    data = v.model_dump()
                    data["source_track"] = "gitnexus"
                    if not data.get("evidence_chain"):
                        data["evidence_chain"] = "gitnexus track candidate (auth logic)"
                    vulnerabilities.append(data)
            # candidate_count == 0 分支在 T6（探索）

        # 追加 auth_gitnexus_queue.json（config_scan 先产；读现有 + 合并，非覆盖）
        queue_path = deliverables / "auth_gitnexus_queue.json"
        existing: list[dict] = []
        if queue_path.exists():
            try:
                existing = json.loads(queue_path.read_text()).get("vulnerabilities", [])
            except Exception:
                existing = []
        atomic_write_json(queue_path, {"vulnerabilities": existing + vulnerabilities})

        return {"candidate_count": candidate_count, "verdict_count": len(vulnerabilities),
                "handler_count": handler_count}
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

> `intent_for("auth-gitnexus-judge")` 若 intent 未注册，参考 `run_authz_gitnexus_judge` 用的 intent key（执行时核对，必要时加白名单或复用 "gitnexus-verdict"）。`PromptManager` import 与 `run_authz_gitnexus_judge` 同路径。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_auth_gitnexus_judge.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/auth_gitnexus_track.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py prompts/auth_gitnexus_judge.txt packages/whitebox/tests/pipeline/test_auth_gitnexus_judge.py
git commit -m "feat(auth): run_auth_gitnexus_judge 多轮判定 + judge prompt + queue 追加（spec-2b T5）"
```

---

### Task 6: 候选空探索 + explore prompt + workflows 编排 + 注册 activity

**Files:**
- Modify: `activities.py`（`run_auth_gitnexus_judge` 加 candidate_count==0 探索段，对标 `run_authz_gitnexus_judge:375-419`）
- Create: `prompts/auth_gitnexus_explore.txt`
- Modify: `workflows.py`（:303 `run_auth_config_scan` 之后插入 `run_auth_gitnexus_judge`）
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py`（注册 `run_auth_gitnexus_judge` activity，对标 `run_authz_gitnexus_judge` 注册）
- Test: `test_auth_gitnexus_judge.py`（扩探索测试）+ `test_workflows_safety.py`（回归锚点）

**Interfaces:**
- Consumes：`run_gitnexus_verdict_agent`；新 prompt `auth_gitnexus_explore`；workflows `run_auth_config_scan:303`（先跑产 config queue）
- Produces：candidate_count==0 探索分支 + workflows 编排（config_scan → auth_judge）

- [ ] **Step 1: Write failing tests**

```python
# test_auth_gitnexus_judge.py 追加
@pytest.mark.asyncio
async def test_auth_judge_explores_when_zero_candidates(tmp_path, monkeypatch):
    """candidate_count==0 → verdict_agent 探索（非静默空 queue）。"""
    import shannon_whitebox.pipeline.activities as act
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    from shannon_core.code_index.auth_gitnexus_track import AuthTrackBuildResult
    fake_result = AuthTrackBuildResult(markdown="", candidates=[], handler_count=0, entry_point_total=0)
    monkeypatch.setattr("shannon_core.code_index.auth_gitnexus_track.build_auth_gitnexus_track",
                        lambda d: fake_result)
    explored = {"n": 0}
    async def fake_verdict(*, prompt, repo_path, structured_output_schema=None, audit_session=None):
        explored["n"] += 1
        assert "login" in prompt.lower() or "session" in prompt.lower() or "explore" in prompt.lower(), \
            "应用探索 prompt"
        r = MagicMock()
        r.structured_output = {"vulnerabilities": []}
        r.text = "{}"
        return r
    monkeypatch.setattr(act, "run_gitnexus_verdict_agent", fake_verdict)
    monkeypatch.setattr(act, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    inp = MagicMock(); inp.workspace_name = "ws"; inp.api_key = None
    await act.run_auth_gitnexus_judge(inp)
    assert explored["n"] == 1, "0 候选应触发探索"


# test_workflows_safety.py 追加
def test_auth_gitnexus_judge_runs_after_config_scan():
    """workflows 编排：run_auth_gitnexus_judge 在 run_auth_config_scan 之后。"""
    import inspect
    from shannon_whitebox.pipeline import workflows
    src = inspect.getsource(workflows)
    i_config = next(i for i, l in enumerate(src.splitlines()) if "run_auth_config_scan" in l and "activities." in l)
    i_judge = next(i for i, l in enumerate(src.splitlines()) if "run_auth_gitnexus_judge" in l and "activities." in l)
    assert i_config < i_judge, "run_auth_gitnexus_judge 应在 run_auth_config_scan 之后"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_auth_gitnexus_judge.py::test_auth_judge_explores_when_zero_candidates packages/whitebox/tests/pipeline/test_workflows_safety.py::test_auth_gitnexus_judge_runs_after_config_scan -v`
Expected: FAIL — 探索段未实装 + workflows 未编排。

- [ ] **Step 3: Write the implementation**

(a) `prompts/auth_gitnexus_explore.txt`（新建，对标 `authz_gitnexus_explore.txt`）：

```
<role>
You are an Authentication audit agent in the GitNexus track's autonomous
exploration mode. The deterministic layer produced zero auth-logic candidates
(typically because auth handler identification missed login/session/oauth
routes, or checkers found no missing/misuse). Autonomously explore to find
auth-logic defects the deterministic layer missed.
</role>

<methodology>
1. Enumerate auth-related routes via grep: app.post('/login'...), router.*,
   @PostMapping('/login'), Route::post('/login'), oauth/callback, /reset, /token.
2. For each auth handler (login/signup/reset/oauth-callback/token), read its body:
   - login success path: does it call session.regenerate (session fixation)?
   - password write: is it hashed via bcrypt/argon2/scrypt (plaintext)?
   - reset/otp token: crypto-random or Math.random/time-based (predictable)?
   - OAuth callback: does it verify state/nonce/PKCE (CSRF/replay)?
   - OIDC id_token: does it jwt.verify signature + iss/aud/exp/sub claims?
3. Verdict: missing/misused security primitive → auth-logic candidate.
</methodology>

<entry_points_summary>
GitNexus deterministic-layer entry-point summary (may be incomplete — grep to supplement):
{{ENTRY_POINTS_SUMMARY}}
</entry_points_summary>

<output>
Output JSON only:
{"vulnerabilities": [{"ID": "AUTH-GN-EXPLORE-NN",
"vulnerability_type": "Session_Management_Flaw | Login_Flow_Logic | Token_Management_Issue | OAuth_Flow_Issue",
"externally_exploitable": true, "source_endpoint": "...",
"vulnerable_code_location": "file:line", "missing_defense": "...",
"exploitation_hypothesis": "...", "suggested_exploit_technique": "...",
"confidence": "low", "notes": "explore-discovered, needs review"}]}
</output>

<conservative_policy>
ALL explore-discovered findings MUST be confidence=low and notes containing
"explore-discovered" — soft candidates NOT validated by deterministic checkers.
</conservative_policy>
```

(b) `activities.py` `run_auth_gitnexus_judge` 加 candidate_count==0 探索段（在 T5 的 `if candidate_count > 0` 之后、`# candidate_count == 0 分支在 T6` 注释处）：

```python
            else:  # candidate_count == 0
                await _session.log_info(
                    "auth GitNexus 轨：0 候选 → 触发自主探索（多轮 agent 读 auth 源码）。",
                    "warning",
                )
                explore_prompt = prompt_manager.load_sync(
                    "auth_gitnexus_explore",
                    variables={"ENTRY_POINTS_SUMMARY": f"{entry_point_total} entry points (handler_count={handler_count})"},
                )
                result = await run_gitnexus_verdict_agent(
                    prompt=explore_prompt, repo_path=str(repo), audit_session=_session,
                    structured_output_schema={"type": "object",
                        "properties": {"vulnerabilities": {"type": "array"}}},
                )
                raw = result.structured_output or result.text
                parsed = VulnerabilityQueue.parse_lenient(
                    raw if isinstance(raw, str) else json.dumps(raw) if raw is not None else "{}")
                for v in parsed.queue.vulnerabilities:
                    data = v.model_dump()
                    data["source_track"] = "gitnexus"
                    data["needs_review"] = True
                    if not data.get("evidence_chain"):
                        data["evidence_chain"] = "gitnexus explore-discovered (0 deterministic candidates)"
                    vulnerabilities.append(data)
```

> `prompt_manager` 需在 `if/else` 两分支前构造一次（提到 candidate_count 计算后），两分支共用。参考 `run_authz_gitnexus_judge` 的探索段（:375-419）。

(c) `workflows.py` 在 `:303 run_auth_config_scan` 之后、`run_authz_gitnexus_judge:373` 之前（或同 phase 合适位置）插入：

```python
                    # auth GitNexus 轨逻辑类判定（吃确定性候选；config_scan 先产 config 类 queue，
                    # auth_judge 追加逻辑类）— spec-2b
                    await workflow.execute_activity(
                        activities.run_auth_gitnexus_judge, act_input,
                        start_to_close_timeout=timedelta(minutes=30),
                        retry_policy=retry_for("gitnexus-verdict"),
                    )
```

> 插入点：在 `run_auth_config_scan` activity 块之后、同 phase 内。缩进与同级 activity 一致。`retry_for("gitnexus-verdict")`（对标 authz_judge spec-1a T5 的 retry）。

(d) `worker.py` 注册 activity：在 `run_authz_gitnexus_judge` 注册处附近加 `run_auth_gitnexus_judge`（执行时 grep 定位 worker 的 activities 列表）。

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/whitebox/tests/pipeline/test_auth_gitnexus_judge.py packages/whitebox/tests/pipeline/test_workflows_safety.py -v`
Expected: PASS（探索测试 + 编排锚点 + spec-1a/1b safety 锚点未破）。

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py prompts/auth_gitnexus_explore.txt packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/src/shannon_whitebox/worker.py packages/whitebox/tests/pipeline/test_auth_gitnexus_judge.py packages/whitebox/tests/pipeline/test_workflows_safety.py
git commit -m "feat(auth): 候选空探索 + workflows 编排(config_scan→auth_judge) + worker 注册（spec-2b T6）"
```

---

## 验证（真机，task 全过后）

- **V1/V2**：moa-auth（Node/Egg）白盒跑，`SESSION_REGENERATE_MISSING` 候选 → 多轮判定 → `auth_gitnexus_queue.json` 条目（对照 moa-auth 基准 AUTH-VULN-01）；候选空时探索产软候选。
- **V3/V4**：首批 6 检查器覆盖 spike 确认的 4 类；moa-auth/futu 误报 = 0（缺失/误用型二元信号）。
- **V5**：`SHANNON_LLM_TRACK_ENABLED=0` 时 auth 逻辑类有 GitNexus 轨产出。
- **V7**：`run_auth_config_scan` 行为不变；config 类 + 逻辑类同 `auth_gitnexus_queue.json` 共存（`source_track="gitnexus"` 一致，`evidence_chain` 区分）。
- **V8 端到端**：moa-auth 白盒跑通整条链路（端点识别 → 检查器 → 多轮判定 → queue 追加）。

---

## Self-Review

**Spec coverage**（spec-2b G1/G2/G3 + spike §7）：
- G1（候选生成）→ T1（骨架+端点识别）+ T2-T4（6 检查器）+ T5（render）✓
- G2（多轮判定）→ T5（judge activity + prompt + queue 追加）+ T6（探索段）✓
- G3（独立兜底）→ T6（workflows 编排，关 LLM 轨仍跑）✓
- spike §7.1 检查项首批（6）→ T2/T3/T4 ✓；USER_ENUMERATION 后置（spec 声明，非缺陷）
- spike §7.2 语言范围（Node 全 6 + Go 4，session 类后置）→ T2/T3 原语规则表 ✓（Go session 类 regenerate/logout 标后置，spec R4）
- spike §9 原型验证 → T2 Step 5（moa-auth session_regenerate_missing）✓
- 非目标（不改 config_scan/vuln-auth/merger；业务逻辑型靠 LLM 轨）→ Global Constraints ✓
- queue 追加（非覆盖）→ T5（读现有 + 合并）✓

**Placeholder 扫描**：fixture（CodeIndex/FuncBlock/EntryPoint 字段、ActivityInput 必填、`_get_paths` mock、`intent_for` key）标注"以实际为准，按实际补全 fixture，不删断言"——TDD fixture 适配指引，非占位空话。workflows 缩进 + worker 注册位置标注"grep 定位，与同级一致"——执行指引，代码块给出目标形态。T2 Step 5 端到端验证标"moa-auth 无 code_index.json 则记待真机冒烟，不阻塞 commit"——明确的 fallback，非 TODO。无 TBD/"implement later"。

**类型一致**：
- `AuthCandidate`（T1 定义）跨 T2-T5 一致（id/handler_id/endpoint/check_type/verdict_signal/evidence_callee/expected/file_path/line/code_snippet/confidence/needs_deep_agent）✓
- `AuthTrackBuildResult(markdown, candidates, handler_count, entry_point_total)`（T1）= T5/T6 activity 解包（`md, candidates, handler_count, entry_point_total`）✓
- 检查器签名 `_check_xxx(handler, index, endpoint) -> AuthCandidate | None` 跨 T2-T4 一致 ✓
- `run_gitnexus_verdict_agent(*, prompt, repo_path, structured_output_schema, audit_session)`（spec-0 + spec-1a T2）= T5/T6 调用一致 ✓
- queue 追加逻辑（读现有 + 合并）T5 定义、T6 探索段共用 vulnerabilities 列表 ✓

**Scope check**：6 task 各自独立 test cycle（T1 模型+识别 / T2 首检查器+原型 / T3 三个 MISSING_POSITIVE / T4 两个 negative/缺失 / T5 render+activity 判定 / T6 探索+编排）。T2→T3→T4 检查器同质叠加；T5/T6 activity 编排。合在一个 plan（单一目标：auth GitNexus 轨），可分别 review。

**已知 follow-up（plan 外，spec 声明）**：
- USER_ENUMERATION_DIFF_ERROR 检查器（low confidence，后置）
- Go session 类（regenerate/logout）检查器（库多样，后置）
- Python/Java/PHP 原语规则扩展（首批 Node+Go）
