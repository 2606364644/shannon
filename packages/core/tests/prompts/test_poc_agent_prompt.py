# packages/core/tests/prompts/test_poc_agent_prompt.py
"""poc-agent prompt 契约测试（spec 2026-08-27-poc-agent-direct-design §4.1-4.3）。

锁定的不变量（每条都源自 app-20260827-062331 XSS-VULN-01 四层失真实证）：
1. 翻译者非判定者（verdict 不容重审——判定轨职责，PoC 只翻译）；
2. 产出前强制源码验证：端点形态（SPA 路由 ≠ HTTP 投递点）/ 消费点（不信
   finding 注记，从代码读）/ 投递模型（存储型 = plant+trigger 两步）/ 认证形态；
3. 正确性自检主位、格式自检次位（用户明确要求：不能只关注格式而舍弃正确性）；
4. 宁缺毋错降级（无法产出正确 PoC → self_check=fail + 原因，不硬造）；
5. 输出契约含 curl/raw_http/steps/self_check 字段；
6. CLAUDE.md §1 铁律：不 @include 确定性 hints partial（poc-agent 是报告层、
   可读合并 queue——「报告层消费确定性产物，不喂判定轨 prompt」口径——但
   prompt 本身不得引确定性判定产物 partial）。

对齐 tests/prompts/test_static_dataflow_hints_decoupling.py 的锚定模式
（parents[4] = repo root，holds prompts/）。
"""
from pathlib import Path

PROMPT_PATH = Path(__file__).resolve().parents[4] / "prompts" / "poc-agent.txt"


def _prompt() -> str:
    assert PROMPT_PATH.exists(), f"missing prompt file: {PROMPT_PATH}"
    return PROMPT_PATH.read_text(encoding="utf-8")


def test_prompt_exists_and_declares_translator_role():
    text = _prompt()
    # 翻译者非判定者：verdict 不容重审
    assert "translat" in text.lower()
    assert "NOT" in text and ("re-judge" in text or "re-evaluate" in text
                              or "adjudicat" in text.lower())


def test_prompt_requires_endpoint_form_verification():
    # 端点形态验证：SPA 前端路由不得作为 HTTP 投递点（实证第 1 层错误）
    text = _prompt()
    assert "SPA" in text
    assert "front-end route" in text or "frontend route" in text \
        or "client-side route" in text
    assert "router" in text.lower()


def test_prompt_requires_code_grounded_consumption_check():
    # 消费点从代码读，不信 finding 注记（实证第 3 层错误：auditTaskId (body) 注记错）
    text = _prompt()
    assert "annotation" in text.lower() or "annotated" in text.lower()
    lowered = text.lower()
    assert ("read the" in lowered and "code" in lowered) \
        or "grep" in lowered


def test_prompt_requires_delivery_model_per_vuln_type():
    # 投递模型按类型匹配：存储型 = plant+trigger 两步；DOM/浏览器渲染 = 导航
    text = _prompt()
    assert "plant" in text.lower() and "trigger" in text.lower()
    assert "browser" in text.lower() and ("navigation" in text.lower()
                                          or "navigate" in text.lower())
    assert "stored" in text.lower()
    assert "DOM" in text


def test_prompt_auth_form_trusts_finding_annotations():
    # 认证形态不查码（2026-08-27 用户裁剪第 4 项强制验证）：信 finding 的
    # authentication_required/accessible_routes 注记，session 型 middleware →
    # <SESSION_COOKIE>，否则 <AUTH_TOKEN>；两字段皆缺才在 notes 标注未证实
    text = _prompt()
    assert "SESSION_COOKIE" in text and "AUTH_TOKEN" in text
    lowered = text.lower()
    assert "do not read code for this" in lowered   # 显式不查码指令
    assert "accessible_routes" in text              # 信任注记的来源字段


def test_self_check_correctness_primary_format_secondary():
    # 正确性自检主位、格式自检次位（用户明确要求）
    text = _prompt()
    assert "self_check" in text or "self-check" in text
    lowered = text.lower()
    ci = lowered.find("correctness")
    assert ci >= 0
    # PRIMARY/SECONDARY 措辞成对出现（顺序无关，两处都要在）
    assert "primary" in lowered and "secondary" in lowered


def test_prompt_locks_better_missing_than_wrong_degradation():
    # 宁缺毋错：产不出正确 PoC → fail + 原因，不硬造
    text = _prompt()
    lowered = text.lower()
    assert "fail" in lowered
    assert "cannot produce" in lowered or "cannot write" in lowered \
        or "unable to produce" in lowered or "degrade" in lowered


def test_output_contract_fields_present():
    # 输出契约字段：vulnerability_id/curl/raw_http/steps/self_check（+preconditions/
    # expected_response/notes）
    text = _prompt()
    for field in ("vulnerability_id", "curl", "raw_http", "steps",
                  "self_check", "preconditions", "expected_response", "notes"):
        assert f'"{field}"' in text, f"output contract missing field: {field}"


def test_prompt_uses_web_url_placeholder():
    # host 占位：{{WEB_URL}} 模板注入（渲染层单源）
    assert "{{WEB_URL}}" in _prompt()


def test_prompt_no_deterministic_hints_include():
    # CLAUDE.md §1：不得 @include 确定性 hints partial（poc-agent 读合并 queue
    # 是报告层消费，prompt 引确定性判定产物 partial 仍是禁止项）
    text = _prompt()
    assert "@include" not in text or \
        all("static-dataflow-hints" not in line for line in text.splitlines())
    assert "static_dataflow_hints" not in text


# ---------- output discipline（2026-08-28 NodeGoat auth 实证：66 轮 JSON 转义搏斗） ----------

def test_prompt_output_discipline_json_quoting_rules():
    # 值内裸英文双引号是转义地狱源头（"受害者"登录"：... 值内裸 " 全爆）：
    # 必须显式给出替代（「」/' '）+ 转义规则（\"）+ shell 单引号转义（'\''）
    text = _prompt()
    lowered = text.lower()
    assert "double quote" in lowered
    assert "escaped" in lowered            # \" 转义规则存在
    assert "'\\''" in text                 # shell 单引号嵌套写法
    assert "「」" in text or "single quotes" in lowered   # 值内替代写法


def test_prompt_output_discipline_validate_via_python_before_emit():
    # 终稿先落盘 python3 验证再输出（json.dump 管全部转义，不做字符串手术）
    text = _prompt()
    assert "python3 -m json.tool" in text
    lowered = text.lower()
    assert "json.dump" in lowered or "json.load" in lowered


def test_prompt_output_discipline_budget_bounded():
    # 预算有限 + 格式修复最多一轮：防 66 轮格式搏斗烧光预算零产出
    # （护栏：修不好就诚实降级 self_check=fail，不无限循环）
    text = _prompt()
    lowered = " ".join(text.split()).lower()   # 折叠换行，防措辞被 wrap 拆断
    assert "turn budget" in lowered
    assert "one repair round" in lowered or "1 repair round" in lowered
