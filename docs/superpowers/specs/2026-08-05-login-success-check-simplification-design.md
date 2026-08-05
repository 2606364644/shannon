# 登录成功判定简化设计

- 日期:2026-08-05
- 分支:feat/fork-py
- 状态:待 plan

## 1. 背景与动机

黑盒扫描(blackbox)的认证登录阶段,有一个"登录成功判定"配置项(字段 `success_condition`)。经代码核查 + 实测,发现两个问题:

### 1.1 `success_condition` 是死字段

用户在 Web UI / CLI 必填这个字段(4 种 type:`url_contains` / `element_present` / `url_equals_exactly` / `text_contains`),但:

- prompt builder(`_build_auth_context` / `build_login_instructions`,`manager.py`)从不把它的 type/value 注入 prompt;
- `login-instructions.txt:46-49` 的 VERIFICATION 段引用了 `success_condition.type`,但那是固定 prose,没有占位符注入用户填的具体值——模型只看到"如果有 url_contains 类型就检查 URL 是否含指定值"这种泛化指引,**拿不到用户填的值**;
- 4 种 type 也没有任何 Python 代码去执行(全仓库零命中)。

实际判定靠 `login-instructions.txt:51-54` 的主观规则(页面不是登录页 / 没有认证错误 / 有认证内容),模型纯自判。**字段填不填,对扫描行为零影响。**

### 1.2 cookie 兜底建立在弱信号上

`validate_authentication.py:143-167` 用 `auth-state.json` 的 `cookies`/`origins` 数量做客观兜底。但**"有 cookie" ≠ "登录成功"**:

- CSRF cookie(Django / Spring / Express csurf)、匿名 session cookie(`PHPSESSID` / `ASP.NET_SessionId`,session_start 就 set)、限流/错误计数 cookie、分析/bot cookie(`_ga` / `__cf_bm`)——这些**登录页就有,登录失败也在**。
- `validate_authentication.py:152-153` 注释假设"a state with cookies/origins means the browser really did authenticate"——这个假设错了,cookie 是弱信号。

两个分支各有问题:
- **True 分支**(`:145-146`,模型说成功 + 无 cookie → 推翻为失败):对 sessionStorage / 内存 token 登录(真成功但 `state save` 抓不到 token)产生**假阴性**(真成功被判挂)。
- **False 分支**(`:148-157`,模型说失败 + 有 cookie → override 成功):登录失败但有 CSRF/匿名 cookie 时产生**假阳性**(隐蔽危险——扫描器以为登录,带未认证态扫,漏报所有需登录的漏洞)。

### 1.3 实测证据(2026-08-05,本会话)

探针 `scripts/validate_login_success_probe.py`,两引擎(glm-anthropic `--json_schema` + glm-openai `response_format`)× 3 场景(SUCCESS / PARTIAL / FAILURE)× 12 次 = **72 次**:

- `login_success` 结构化字段判定 **72/72 全对,零误判,零提取失败**;
- 根因 B(memory: blackbox-auth-validation-two-root-causes,GLM 在 `--json_schema` 下误填 false)**在隔离测试中未复现**(含 glm-anthropic 引擎 36/36 全对)。

**结论:信息明确时,模型经结构化输出通道判定 `login_success` 高度可靠。** cookie 兜底要纠正的"字段误填"几乎不发生,而它引入的假阳性真实且危险。

**测试局限(诚实):** 隔离测试(给定明确观察文本判定),非真实 agent 多轮浏览器交互;72 次统计效力有限(<1.4% 的低概率偶发可能没碰到)。

## 2. 设计决策

四条,核心是**剥掉建立在弱信号(cookie)上的"防御性兜底",信一个被证明可靠的模型判定 + 给它明确指引**。

### D1. 删独立 `success_condition` 字段

数据模型 `SuccessCondition`(`config.py:25-27`)+ `Authentication.success_condition`(`config.py:45`,必填)删除;4 种 type 删除。涉及 parser 清洗、前端表单/校验/i18n、API 契约、scan_manager 写 YAML。删字段不影响判定(它本就是死字段)。

### D2. 判定并入 `login_flow`(自然语言指引)

用户在 `login_flow`(自由文本列表,`Authentication.login_flow`)里用自然语言写"成功标志",如:

```
1. open <login_url>
2. fill username / password
3. click submit
4. 登录成功标志:URL 应跳转到 /dashboard,或页面出现 "Welcome"
```

`login_flow` 已通过 `build_login_instructions`(`manager.py:364,388`)注入 `{{user_instructions}}`,**零接通成本**——写了 = 给模型指引,不写 = 模型自判。比 4 种固定 type 更灵活(能描述任意条件),且语义自然(判定本就是登录流程的一部分)。

- 标准 form 登录(只填 credentials、不写 login_flow):默认模型自判,无配置负担。
- 想加判定 / 复杂登录:写 login_flow(含成功标志)。

### D3. 去掉 cookie 兜底,纯信模型 `login_success` 字段判定

`validate_authentication.py:143-167` 的 cookie 仲裁改成**直接返回字段判定**(纯信 `verdict["login_success"]`)。两个分支都去掉(True 分支反向推翻 + False 分支 cookie override)。

- **有 structured output**:`verdict["login_success"]=True → 成功`;`=False → 失败`(带 `failure_point` / `failure_detail`)。
- **无 structured output**(provider 异常,测试中 0 发生):视为失败(fail-fast),不再用 cookie 兜底。属极罕见的 provider 故障,可见可重试。

**save/load 机制保留**——`auth-state.json` 的 save 仍由 agent 在判定成功后执行,用途是**给下游 exploit agent 复用登录态**(`validate-authentication.txt:22-29` publish_session)。判定兜底和共享登录态是两回事,去掉前者不影响后者。`verify_auth_state` 函数不再做判定兜底(可保留作 save 文件完整性校验,或随 D3 精简)。

### D4. 改 `login-instructions.txt` 的 VERIFICATION 段

去掉对 `success_condition` 的悬空引用(`:46-49`),改成:若 `login_flow`(`{{user_instructions}}`)描述了成功标志,据此判定;否则综合 URL 跳转 / 页面内容 / cookie 自行判定。

## 3. 改动范围(按模块)

### core
- `models/config.py`:删 `SuccessCondition` + `Authentication.success_condition`
- `config/parser.py`:删 `success_condition` 清洗(`:100-103, 163-173`)
- `services/validate_authentication.py`:仲裁逻辑改纯信字段(D3);`verify_auth_state` 精简
- `prompts/manager.py`:**不动**(login_flow → `{{user_instructions}}` 通路已存在,D2 零改动)
- `prompts/shared/login-instructions.txt`:改 VERIFICATION 段(D4)

### web 前端
- `pages/ScanNewPage.tsx`:删 `SuccessConditionType` / `AuthFormState.scType,scValue` / `DEFAULT_AUTH` 对应项 / `buildAuthPayload` 的 `success_condition` 注入(`:66`)/ `authFromPayload` 还原(`:89-90`)/ `validateAuth` 的 `scValue` 必填校验(`:109`)
- `components/ScanFormFields.tsx`:删 success_condition UI(`:162-183`)
- `locales/{zh,en}.json`:删文案(`:383-394`)
- `api/types.ts`:删 `ScanAuthentication.success_condition`(`:255`)

### web 后端
- `models.py`:`ScanRequest.authentication`(dict)不变,前端不再传 `success_condition`
- `components/scan_manager.py`:`_resolve_blackbox_inputs` 写 YAML 时自然不再写 `success_condition`(Authentication 删字段后)

### blackbox CLI
- `cli/main.py`:无交互表单(吃 YAML),YAML 不再有 `success_condition`

### 测试
- 新增 `validate_authentication` 单测:纯信字段判定(True/False/无 structured output 各分支)
- 更新前端 `ScanNewPage` 测试(删 success_condition 相关断言)
- 更新 `test_scans_api.py` 的 success_condition fixture(改用 login_flow)
- 探针 `scripts/validate_login_success_probe.py` **保留**作回归基线

## 4. 关键不变量

1. **双引擎一致**:判定改动在 core,经 `run_claude_prompt` 统一抽象,两引擎流程一致(CLAUDE.md §2)。
2. **CLI/Web 合流**:`scan-config.yaml` 是合流点,删字段后两端一致。
3. **save/load 保留**:`auth-state.json` save 用于跨 agent 共享登录态,不删(只去掉判定兜底)。
4. **不涉双轨**:本改动只在黑盒 auth-validation,不碰 inj/xss/ssrf/authz 双轨(CLAUDE.md §1)。login_flow 是用户配置,非确定性产物,不重建确定性→LLM 轨 hints 桥梁。
5. **TS 对齐待核实(plan 阶段)**:原始 shannon(`/root/shannon`)的 Authentication schema 是否有 `success_condition`、是否依赖——plan 阶段核实。若 TS 有,删字段属合理偏离(本项目已有多处偏离,删死字段不破坏核心对齐)。

## 5. 风险与缓解

- **R1 残余假阴性**:去掉客观兜底后,若模型字段偶发误填(根因 B 真实偶发),结果是**假阴性**(扫描终止、可见、可重试)。可接受:可见失败能补救,远优于 cookie 兜底的隐蔽假阳性。缓解:扫描失败时 events 诊断清晰(`agent_end` + 字段值)。
- **R2 真实 agent vs 隔离测试**:测试是信息明确的隔离判定,真实 agent 场景页面可能复杂。缓解:D2 的 login_flow 指引保证模型判定有明确依据,降低"信息不充分 → 模型真判断错"。若需更高置信,plan 后可在 NodeGoat 真机端到端冒烟。
- **R3 标准登录无指引**:标准 form 登录不写 login_flow,纯模型自判。缓解:测试显示信息明确时模型可靠;VERIFICATION 段保留综合判定指引(URL/页面/cookie)。

## 6. 范围外(YAGNI)

- 不引入 Python 端确定性硬断言(URL/元素正则匹配):D2 自然语言指引足矣;硬断言增加复杂度,且认证模式多样难通用。
- 不自动探测认证模式(cookie vs token):复杂且不可靠。
- 不改 retry policy / agent max_turns / 双引擎调用层。
