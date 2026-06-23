# Prompt 优化(对比原始 shannon 退化修复)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复重构项目 prompt 相对原始 shannon 的三处问题——vuln-authz 框架端点 IDOR 方法论丢失(A)、recon-static 离线模式未接通(B)、prompt_manager 缺占位符警告(C)。

**Architecture:** 三处独立改动,按 C→A→B 顺序实现。C 在 `PromptManager._interpolate` 末尾加残留占位符检测;A 在 `vuln-authz.txt` 补回两个方法论块;B 拆两步——先在 `AgentExecutor` 抽纯函数 `resolve_template_name` 实现 recon 离线 template 切换(B-机制),再给 `recon-static.txt` 补「Endpoint Security Context」section 让离线 deliverable 有 authz 要读的输入(B-内容)。

**Tech Stack:** Python 3(pytest TDD)、基于文件的 prompt 模板(`@include` + `{{VAR}}` 插值)、temporalio pipeline。

## Global Constraints

(每个 task 的需求都隐式包含以下约束,源自 spec `docs/superpowers/specs/2026-06-23-prompt-optimization-design.md`)

- **测试只跑改动相关子集**,绝不跑全套/全包——全量会 hang 在 Temporal/网络慢测试(memory 记录)。
- **prompt 变量统一 `{{UPPER_CASE}}` 格式**,文件路径变量用 `{{DELIVERABLES_PATH}}`(不用 `.shannon/deliverables/` 硬编码)。
- **`recon-static.txt` 已存在**(165 行,git commit `e7b0555`)——Task 4 是**修改**不是新建。
- **IDOR 归 authz**(授权),不归 auth(认证);framework endpoint guidance 关注 ownership validation,属授权范畴。
- **commit 用 conventional commits** 风格(`feat(...)` / `fix(...)`),参考 `git log --oneline -5`。
- **`AgentName` 是 `str, Enum`**:`AgentName.RECON == "recon"` 为 True,`AGENTS["recon"]` 可命中枚举键。
- **logger 约定**:`import logging` + `logger = logging.getLogger(__name__)`(见 `framework_analyzer.py:11,17`)。

## File Structure

| 文件 | 责任 | 本计划动作 |
|---|---|---|
| `packages/core/src/shannon_core/prompts/manager.py` | prompt 模板加载/插值 | Task 1 加占位符检测 |
| `packages/core/tests/test_prompt_manager.py` | manager 单测 | Task 1 加 2 个用例 |
| `prompts/vuln-authz.txt` | 授权分析 agent prompt | Task 2 补方法论块 |
| `packages/whitebox/tests/test_vuln_prompts_chinese.py` | vuln prompt 内容断言 | Task 2 加断言用例 |
| `packages/core/src/shannon_core/agents/executor.py` | agent 执行/template 解析 | Task 3 抽 `resolve_template_name` + 改 execute |
| `packages/core/tests/test_executor_template.py` | template 解析单测 | Task 3 新建 |
| `prompts/recon-static.txt` | 离线/静态侦察 prompt | Task 4 补 Endpoint Security Context section |
| `packages/core/tests/test_recon_static_prompt.py` | recon-static 内容断言 | Task 4 新建 |

---

## Task 1: prompt_manager 补残留占位符警告(C)

**Files:**
- Modify: `packages/core/src/shannon_core/prompts/manager.py`(顶部 import + `_interpolate` 末尾,约行 1、156)
- Test: `packages/core/tests/test_prompt_manager.py`(加 2 个用例)

**Interfaces:**
- Consumes: 无(独立改动)
- Produces: `PromptManager._interpolate` 在返回前 log warning 残留的 `{{UPPER_CASE}}` 占位符;不影响返回值。

- [ ] **Step 1: 写失败测试 — 漏传变量触发 warning**

追加到 `packages/core/tests/test_prompt_manager.py` 末尾:

```python
def test_unresolved_placeholder_logs_warning(prompts_dir, caplog):
    """漏传的 {{UPPER_CASE}} 变量应触发 warning。"""
    import logging
    (prompts_dir / "unresolved-test.txt").write_text("Hello {{WEB_URL}} and {{MISSING_VAR}} world")
    manager = PromptManager(prompts_dir)
    with caplog.at_level(logging.WARNING, logger="shannon_core.prompts.manager"):
        result = manager.load_sync("unresolved-test", {"web_url": "https://x.com", "repo_path": "/r"})
    assert "https://x.com" in result  # 已知变量正常替换
    # 残留的 MISSING_VAR 应被报告
    assert any("MISSING_VAR" in r.message for r in caplog.records), \
        "残留的 {{MISSING_VAR}} 应触发 warning"


def test_natural_language_placeholder_not_flagged(prompts_dir, caplog):
    """合法的自然语言填空提示(含空格/小写)不应被误报。"""
    import logging
    (prompts_dir / "fillin-test.txt").write_text(
        "Count: {{number of confirmed vulnerabilities}}\nURL: {{WEB_URL}}"
    )
    manager = PromptManager(prompts_dir)
    with caplog.at_level(logging.WARNING, logger="shannon_core.prompts.manager"):
        result = manager.load_sync("fillin-test", {"web_url": "https://x.com", "repo_path": "/r"})
    # 自然语言填空提示保留在结果里(给 agent 看的占位词)
    assert "{{number of confirmed vulnerabilities}}" in result
    # 不应有任何 warning(自然语言占位符不是真变量)
    unresolved_warnings = [r for r in caplog.records if "Unresolved" in r.message or "placeholder" in r.message.lower()]
    assert unresolved_warnings == [], f"自然语言填空提示不应触发 warning: {unresolved_warnings}"
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `pytest packages/core/tests/test_prompt_manager.py::test_unresolved_placeholder_logs_warning packages/core/tests/test_prompt_manager.py::test_natural_language_placeholder_not_flagged -v`
Expected: 两个测试 FAIL(当前无检测逻辑,第一个测试断言 warning 不出现 → 失败)。

- [ ] **Step 3: 实现 — 加 import + logger + 检测**

3a. 在 `packages/core/src/shannon_core/prompts/manager.py` 顶部(import 区,行 1-8 附近)加:

```python
import logging
```
（放在现有 `import re` 之前或之后,保持字母序。）

3b. 在 import 区之后、`def strip_conditional_blocks` 之前(约行 10)加模块级 logger:

```python
logger = logging.getLogger(__name__)
```

3c. 在 `_interpolate` 方法末尾,把:
```python
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result
```
改为:
```python
        result = re.sub(r"\n{3,}", "\n\n", result)

        # 检测残留的未解析占位符(只匹配真变量格式 {{UPPER_CASE}},
        # 排除自然语言填空提示如 {{number of ...}})
        remaining = re.findall(r"\{\{[A-Z][A-Z0-9_]*\}\}", result)
        if remaining:
            logger.warning(
                "Unresolved prompt placeholders in %s: %s",
                template_name,
                sorted(set(remaining)),
            )

        return result
```

- [ ] **Step 4: 运行测试,确认通过**

Run: `pytest packages/core/tests/test_prompt_manager.py::test_unresolved_placeholder_logs_warning packages/core/tests/test_prompt_manager.py::test_natural_language_placeholder_not_flagged -v`
Expected: 两个测试 PASS。

- [ ] **Step 5: 回归 — 跑 manager 全部测试**

Run: `pytest packages/core/tests/test_prompt_manager.py -v`
Expected: 全部 PASS(现有用例不受影响——现有模板无残留 `{{UPPER_CASE}}`)。

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/prompts/manager.py packages/core/tests/test_prompt_manager.py
git commit -m "feat(prompt): _interpolate 补残留占位符 warning(对齐原始 TS,排除自然语言填空提示)"
```

---

## Task 2: vuln-authz 补回框架端点 IDOR 方法论(A)

**Files:**
- Modify: `prompts/vuln-authz.txt`(在 `<starting_context>` 之后,约行 45-47 之间插入)
- Test: `packages/whitebox/tests/test_vuln_prompts_chinese.py`(加断言用例)

**Interfaces:**
- Consumes: recon deliverable 的 Section 4.2 Endpoint Security Context(由 `recon.txt` / Task 4 的 `recon-static.txt` 产出)
- Produces: vuln-authz prompt 含 Section 0 引导 + Framework Endpoint Guidance,提升 finale-rest/epilogue 端点 IDOR 检测概率。

- [ ] **Step 1: 写失败测试 — 断言 vuln-authz 含框架端点方法论**

追加到 `packages/whitebox/tests/test_vuln_prompts_chinese.py` 末尾:

```python
def test_vuln_authz_has_framework_endpoint_guidance():
    """vuln-authz 必须含 finale-rest/epilogue 框架端点 IDOR 方法论(原始退化修复)。"""
    src = _read("vuln-authz")
    # 必须引导先读 recon 的 Endpoint Security Context(Section 4.2)
    assert "Endpoint Security Context" in src, \
        "vuln-authz 须引导读 recon 的 Endpoint Security Context"
    assert "recon_deliverable.md" in src, "须引用 recon deliverable 路径"
    # 必须含框架端点方法论关键词
    assert "finale-rest" in src, "须含 finale-rest 框架端点指引"
    assert "epilogue" in src, "须含 epilogue 框架端点指引"
    # 必须要求在 finding 里记录 framework_origin
    assert "framework_origin" in src, "须要求 finding 记录 framework_origin 字段"
    # 必须提示自动生成端点默认缺 ownership validation → assume vulnerable
    assert "ownership validation" in src.lower() or "ownership check" in src.lower(), \
        "须提示框架端点默认缺 ownership validation"
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `pytest packages/whitebox/tests/test_vuln_prompts_chinese.py::test_vuln_authz_has_framework_endpoint_guidance -v`
Expected: FAIL(当前 vuln-authz.txt 不含 "Endpoint Security Context" 引导、"framework_origin" 等)。

- [ ] **Step 3: 实现 — 在 vuln-authz.txt 插入两个方法论块**

在 `prompts/vuln-authz.txt` 中,定位 `<starting_context>` 块结束处(当前是行 39-45):
```
<starting_context>
- Your **primary source of truth** for authorization test targets is the reconnaissance report at `{{DELIVERABLES_PATH}}/recon_deliverable.md`. Look specifically for:
  - **"Horizontal" section:** Endpoints where users access resources by ID that might belong to other users
  - **"Vertical" section:** Admin/privileged endpoints that regular users shouldn't access
  - **"Context" section:** Multi-step workflows where order/state matters
- You are the final analysis specialist. Your findings complete the analysis phase.
</starting_context>
```

把 `</starting_context>` 这一行替换为 `</starting_context>` + 下方两个新块(用 Edit,old_string 为整段 `<starting_context>...</starting_context>`,new_string 为原内容 + 追加两块)。**完整 new_string:**

```
<starting_context>
- Your **primary source of truth** for authorization test targets is the reconnaissance report at `{{DELIVERABLES_PATH}}/recon_deliverable.md`. Look specifically for:
  - **"Horizontal" section:** Endpoints where users access resources by ID that might belong to other users
  - **"Vertical" section:** Admin/privileged endpoints that regular users shouldn't access
  - **"Context" section:** Multi-step workflows where order/state matters
- You are the final analysis specialist. Your findings complete the analysis phase.
</starting_context>

<endpoint_security_context_reading>
### Read Endpoint Security Context (REQUIRED — Do This First)

Before analyzing any authorization vulnerabilities:

1. **Read Recon deliverable:**
   - Open `{{DELIVERABLES_PATH}}/recon_deliverable.md`
   - Locate the "Endpoint Security Context" section (Section 4.2)
   - Extract all endpoints with their security context

2. **For each endpoint in your TODO list:**
   - Look up its security context in Section 4.2
   - Note: Authentication requirement
   - Note: Middleware chain
   - Note: Framework origin (manual vs auto-generated)
   - Note: Ownership validation status

3. **Prioritize endpoints with:**
   - Framework origin: "finale-rest auto-generated" or "epilogue auto-generated"
   - Ownership validation: "none detected" or "absent"
   - HTTP methods: DELETE, PUT, PATCH (mutation operations)
   - Authentication: "user" only (no role restriction)

**For framework auto-generated endpoints:** These typically lack ownership validation by default. Assume vulnerable unless Recon explicitly found an ownership check that dominates all code paths to side effects.
</endpoint_security_context_reading>

<framework_endpoint_guidance>
**Framework Endpoint Guidance:**
When Recon reports an endpoint with `Framework Origin: finale-rest auto-generated` or `epilogue auto-generated`:
- The endpoint was generated by an ORM-to-REST framework, not manually coded
- Default behavior is CRUD without ownership checks
- Check if the framework's `create.end`, `update.end`, `destroy.end` hooks add ownership validation
- If no hooks override the default behavior → the endpoint is vulnerable to IDOR
- Document the framework origin in your finding:
  ```json
  {
    "endpoint": "DELETE /api/Feedbacks/:id",
    "framework_origin": "finale-rest auto-generated",
    "recon_ownership_check": "none detected",
    "guard_evidence": "isAuthenticated() only, no ownership validation"
  }
  ```
</framework_endpoint_guidance>
```

（插入后,原有的 `@include(shared/_static-dataflow-hints.txt)` 紧跟其后。）

- [ ] **Step 4: 运行测试,确认通过**

Run: `pytest packages/whitebox/tests/test_vuln_prompts_chinese.py::test_vuln_authz_has_framework_endpoint_guidance -v`
Expected: PASS。

- [ ] **Step 5: 回归 — 跑 vuln prompt 全部断言**

Run: `pytest packages/whitebox/tests/test_vuln_prompts_chinese.py -v`
Expected: 全部 PASS(新块不破坏现有中文标题/语言块断言)。

- [ ] **Step 6: Commit**

```bash
git add prompts/vuln-authz.txt packages/whitebox/tests/test_vuln_prompts_chinese.py
git commit -m "fix(prompt): vuln-authz 补回 finale-rest/epilogue 框架端点 IDOR 方法论(Section 0 + Framework Guidance)"
```

---

## Task 3: executor 抽 resolve_template_name 接通 recon 离线模式(B-机制)

**Files:**
- Modify: `packages/core/src/shannon_core/agents/executor.py`(加模块级函数 + 改 execute 行 66)
- Test: `packages/core/tests/test_executor_template.py`(新建)

**Interfaces:**
- Consumes: `AgentName`(from `shannon_core.models.agents`)
- Produces: `resolve_template_name(agent_name, prompt_override, default_template, web_url) -> str`;`AgentExecutor.execute` 调用它决定 template。recon + 无 web_url + 无 override → 返回 `"recon-static"`。

- [ ] **Step 1: 写失败测试 — 新建 test_executor_template.py**

新建 `packages/core/tests/test_executor_template.py`:

```python
from shannon_core.agents.executor import resolve_template_name
from shannon_core.models.agents import AgentName


def test_recon_offline_uses_recon_static():
    """recon + 无 web_url → recon-static(离线模式)。"""
    result = resolve_template_name(
        agent_name=AgentName.RECON,
        prompt_override=None,
        default_template="recon",
        web_url="",
    )
    assert result == "recon-static"


def test_recon_live_uses_default():
    """recon + 有 web_url → 默认 recon(live 模式不变)。"""
    result = resolve_template_name(
        agent_name=AgentName.RECON,
        prompt_override=None,
        default_template="recon",
        web_url="https://target.com",
    )
    assert result == "recon"


def test_prompt_override_wins_over_offline_logic():
    """显式 prompt_override 优先,不被离线逻辑覆盖。"""
    result = resolve_template_name(
        agent_name=AgentName.RECON,
        prompt_override="custom-recon",
        default_template="recon",
        web_url="",
    )
    assert result == "custom-recon"


def test_non_recon_agent_unaffected():
    """非 recon agent 不受离线逻辑影响。"""
    result = resolve_template_name(
        agent_name=AgentName.AUTHZ_VULN,
        prompt_override=None,
        default_template="vuln-authz",
        web_url="",
    )
    assert result == "vuln-authz"


def test_recon_string_value_also_matches():
    """agent_name 传字符串 value(如 workflows 的 AgentName.RECON.value)也能匹配。"""
    result = resolve_template_name(
        agent_name="recon",
        prompt_override=None,
        default_template="recon",
        web_url="",
    )
    assert result == "recon-static"
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `pytest packages/core/tests/test_executor_template.py -v`
Expected: 全部 FAIL(`resolve_template_name` 未定义,ImportError)。

- [ ] **Step 3: 实现 — 加 resolve_template_name + 改 execute**

3a. 在 `packages/core/src/shannon_core/agents/executor.py`,在 `class AgentExecutor:` 之前(约行 21,`if TYPE_CHECKING` 块之后)加模块级函数:

```python
def resolve_template_name(
    agent_name: AgentName,
    prompt_override: str | None,
    default_template: str,
    web_url: str,
) -> str:
    """决定 agent 实际使用的 prompt template 名。

    - 显式 prompt_override 优先(不被覆盖)。
    - recon agent 在无 live web target(离线/纯静态)时回退到 recon-static,
      对齐原始 shannon runner.ts:189 的 promptOverride 思路。
    - 其余情况用 AGENTS 字典里的默认 prompt_template。
    """
    if prompt_override:
        return prompt_override
    if agent_name == AgentName.RECON and not web_url:
        return "recon-static"
    return default_template
```

3b. 改 `AgentExecutor.execute` 的行 66,把:
```python
        template_name = prompt_override or defn.prompt_template
```
改为:
```python
        template_name = resolve_template_name(
            agent_name, prompt_override, defn.prompt_template, web_url,
        )
```

- [ ] **Step 4: 运行测试,确认通过**

Run: `pytest packages/core/tests/test_executor_template.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 回归 — 跑 executor 相关测试**

Run: `pytest packages/core/tests/test_executor_git_isolation.py packages/core/tests/test_agents.py packages/core/tests/test_agent_phase_map.py -v`
Expected: 全部 PASS(execute 行为对 live 场景零变化)。

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/agents/executor.py packages/core/tests/test_executor_template.py
git commit -m "feat(executor): recon agent 无 web_url 时回退 recon-static template(接通离线/静态模式)"
```

---

## Task 4: recon-static.txt 补 Endpoint Security Context section(B-内容)

**Files:**
- Modify: `prompts/recon-static.txt`(在 `## 2. Endpoint Map` 之后补 section,约行 94 之后)
- Test: `packages/core/tests/test_recon_static_prompt.py`(新建)

**Interfaces:**
- Consumes: 无
- Produces: 离线 recon deliverable 含「Endpoint Security Context」section(含 framework origin),供 Task 2 的 vuln-authz Section 0 读取(闭环交叉点①)。

- [ ] **Step 1: 写失败测试 — 新建 test_recon_static_prompt.py**

新建 `packages/core/tests/test_recon_static_prompt.py`:

```python
from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[2] / "prompts"


def _read(name: str) -> str:
    return (PROMPTS / f"{name}.txt").read_text(encoding="utf-8")


def test_recon_static_has_endpoint_security_context():
    """离线 recon deliverable 必须含 Endpoint Security Context section,
    供 vuln-authz 的 Section 0 读取(交叉点①闭环)。"""
    src = _read("recon-static")
    assert "Endpoint Security Context" in src, \
        "recon-static 须含 Endpoint Security Context section"
    # 必须含 framework origin 维度(供 authz 识别 finale-rest/epilogue 端点)
    assert "Framework Origin" in src or "framework origin" in src.lower(), \
        "recon-static 的 Endpoint Security Context 须含 Framework Origin 维度"
    assert "finale-rest" in src, "须覆盖 finale-rest 框架端点识别"
    assert "epilogue" in src, "须覆盖 epilogue 框架端点识别"


def test_recon_static_still_marks_no_browser():
    """静态分析约束保留(回归锚点)。"""
    src = _read("recon-static")
    assert "browser" in src.lower() and ("no" in src.lower() or "not" in src.lower())
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `pytest packages/core/tests/test_recon_static_prompt.py -v`
Expected: `test_recon_static_has_endpoint_security_context` FAIL(当前 recon-static.txt 无 Endpoint Security Context section);第二个测试 PASS(回归锚点,验证读文件正常)。

- [ ] **Step 3: 实现 — 在 recon-static.txt 补 section**

在 `prompts/recon-static.txt` 的 `## 2. Endpoint Map` 表格之后(当前该 section 结束于约行 94)、`## 3. Authentication Architecture` 之前,插入新 section。用 Edit,old_string 为:
```
## 2. Endpoint Map
| Method | Path | Handler | Auth | Parameters | Notes |
|--------|------|---------|------|------------|-------|

## 3. Authentication Architecture
```
new_string 为:
```
## 2. Endpoint Map
| Method | Path | Handler | Auth | Parameters | Notes |
|--------|------|---------|------|------------|-------|

## 2.1 Endpoint Security Context

For every endpoint in Section 2, document its security context. This is **descriptive analysis** — document what protections exist, NOT whether they are sufficient.

| Method | Path | Auth | Middleware | Framework Origin | Ownership Check | Notes |
|--------|------|------|------------|------------------|-----------------|-------|
| (per endpoint) | | anon/user/admin | (middleware chain) | manual / finale-rest auto-generated / epilogue auto-generated | none detected / yes (file:line) | |

**Framework Endpoints (finale-rest / epilogue):**
When auto-REST frameworks are detected (search for `finale.initialize()`, `epilogue.initialize()`, `finale.resource()`, `epilogue.resource()`):
1. List all models configured with the framework
2. For each model, enumerate all auto-generated endpoints: `GET/POST /api/{Model}s`, `GET/PUT/DELETE /api/{Model}s/:id`
3. Mark each endpoint's Framework Origin (finale-rest auto-generated / epilogue auto-generated)
4. Note any overrides or customizations applied after auto-generation
5. For auto-generated endpoints, ownership validation is typically absent by default — record "none detected" unless an explicit ownership check is found

## 3. Authentication Architecture
```

- [ ] **Step 4: 运行测试,确认通过**

Run: `pytest packages/core/tests/test_recon_static_prompt.py -v`
Expected: 两个测试 PASS。

- [ ] **Step 5: 回归 — 跑 recon-static + prompt manager 测试(验证插值不破坏)**

Run: `pytest packages/core/tests/test_recon_static_prompt.py packages/core/tests/test_prompt_manager.py -v`
Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add prompts/recon-static.txt packages/core/tests/test_recon_static_prompt.py
git commit -m "feat(prompt): recon-static 补 Endpoint Security Context section(闭环离线 authz 输入,交叉点①)"
```

---

## 闭环验证(Task 1-4 完成后)

- [ ] **Step 7: 跑全部新增/改动测试**

Run: `pytest packages/core/tests/test_prompt_manager.py packages/core/tests/test_executor_template.py packages/core/tests/test_recon_static_prompt.py packages/whitebox/tests/test_vuln_prompts_chinese.py -v`
Expected: 全部 PASS。

- [ ] **Step 8: 人工冒烟(可选,记忆标注 CLI 真实路径无自动测试)**

对 live 模式跑一次 recon(确认行为零变化);对离线模式(web_url 留空)跑一次 recon(确认走 recon-static template 且 deliverable 含 Endpoint Security Context)。具体命令见项目 `docs/getting-started.md`。

> 三处改动均待人工冒烟后随分支 merge(参考 memory 中其他 prompt/display 改动的合并惯例)。
