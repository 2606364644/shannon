# Shannon-Py 安全效果修复设计

> 修复 Python 重构版中影响安全扫描效果的 9 项缺陷，对齐 TypeScript 原版的安全能力

**日期**: 2026-05-29
**前置文档**: `docs/2026-05-29-shannon-py-gap-analysis.md`

---

## 修复清单总览

| # | 缺陷 | 安全影响 | 修复策略 |
|---|------|---------|---------|
| S1 | misconfig 漏洞类完全缺失 | 无法检测安全头/CORS/Cookie/Open Redirect | 恢复完整 misconfig 类（agent + prompt + schema + config） |
| S2 | Exploit prompt 从400+行降为19行骨架 | exploit 阶段无方法论/分类/证据要求 | 移植 TS 原文 + 重构为 shared partial 模块化 |
| S3 | Report prompt 从113行降为22行 | 报告无过滤规则，质量低 | 移植 TS 结构化报告修改流程 + 动态变量 |
| S4 | 认证预校验缺失 | 无效凭据浪费整个扫描周期 | 复用 AgentExecutor + 移植 validate-authentication prompt |
| S5 | Playwright 反检测+会话隔离缺失 | 黑盒exploit被WAF检测；并发agent互相干扰 | 移植 playwright-config-writer + 会话映射表 |
| S6 | 代码路径 deny rule 执行缺失 | agent可读取/编辑排除目录的敏感文件 | 移植 settings-writer 写入 SDK deny 规则 |
| S7 | Preflight 安全检查缺失 | 可扫描云元数据服务、内网 | 补全 SSRF/DNS/loopback/credential/URL 检查 |
| S8 | 白盒recon不用静态prompt | 白盒可能尝试动态分析而无URL | 移植 recon-static.txt + prompt override 参数 |
| S9 | Blackbox无per-agent queue门控 | 对空queue运行exploit浪费资源 | 增强 ExploitationChecker 检查 queue 非空 |

---

## S1 — 恢复 misconfig 漏洞类

### Core 模型层 (`shannon_core/models/`)

**agents.py**:
- 新增 `MISCONFIG_VULN = AgentName("misconfig-vuln")` 及对应 `AgentDefinition`
- 新增 `MISCONFIG_EXPLOIT = AgentName("misconfig-exploit")` 及对应 `AgentDefinition`
- `MISCONFIG_VULN`: prerequisites=[RECON], prompt_template="vuln-misconfig", deliverable="misconfig_analysis_deliverable.md", model_tier="medium"
- `MISCONFIG_EXPLOIT`: prerequisites=[MISCONFIG_VULN], prompt_template="misconfig-exploit", deliverable="misconfig_exploitation_evidence.md", model_tier="medium"
- Report agent prerequisites 从5个增至6个，加入 MISCONFIG_EXPLOIT

**queue_schemas.py**:
- 新增 `MisconfigVulnerability(BaseVulnerability)` 含8个字段：
  - `source_endpoint: str | None`
  - `vulnerable_code_location: str | None`
  - `missing_defense: str | None`
  - `exploitation_hypothesis: str | None`
  - `suggested_exploit_technique: str | None`
  - `vulnerable_parameter: str | None`
  - `redirect_sink: str | None`
  - `existing_validation: str | None`
- 加入 `Vulnerability` Union 类型

**config.py**:
- `VulnClass` Literal 加入 `"misconfig"`
- `ALL_VULN_CLASSES` 从5项增至6项

### Prompt 层 (`prompts/`)

- 从 TS 复制 `vuln-misconfig.txt` (285行) — 覆盖安全头缺失、CORS错误、Cookie标志、Clickjacking、Open Redirect、信息泄露
- `misconfig-exploit.txt` 按 S2 的重构策略，使用 shared partial + 专项指导结构

### Pipeline 层

- Whitebox workflow: misconfig 自动通过 `ALL_VULN_CLASSES` 包含在并行 vuln agents 列表中
- Blackbox workflow: misconfig 自动包含在并行 exploit agents 列表中
- Playwright session mapping: `vuln-misconfig` → `agent6`, `misconfig-exploit` → `agent6`

---

## S2 — Exploit Prompt 移植 + 重构

### 公共方法论抽取

从5个 TS exploit prompt 中提取公共内容到 `prompts/shared/_exploit-methodology.txt`：

- OWASP 3阶段 exploitation workflow（Recon → Verify → Exploit）
- Proof levels 定义（conclusive / probable / inconclusive）
- 分类框架（EXPLOITED / BLOCKED_BY_SECURITY / OUT_OF_SCOPE / FALSE_POSITIVE）
- 通用证据检查清单（screenshot、HTTP request/response、impact description）
- WAF 规避原则

### 每个 exploit prompt 结构

```
@include(shared/_target.txt)
@include(shared/_exploit-methodology.txt)
@include(shared/_exploit-scope.txt)

## {Type} 专项指导
{该类型特有的攻击技术、sink/source 模式、绕过策略 — 从对应 TS prompt 提取}

## 漏洞验证
{{VULNERABILITY_ENTRIES}}

## 输出要求
{该类型的结构化输出格式}
```

### 6个 exploit prompt 文件

| 文件 | TS 来源 | 专项内容 |
|------|---------|---------|
| `injection-exploit.txt` | `exploit-injection.txt` (451行) | SQL/CMD/SSTI/LFI payload构造、union query、blind injection |
| `xss-exploit.txt` | `exploit-xss.txt` (442行) | 反射/存储/DOM型XSS PoC、CSP bypass、context-aware encoding |
| `auth-exploit.txt` | `exploit-auth.txt` (423行) | Session fixation/hijacking、JWT manipulation、OAuth abuse |
| `authz-exploit.txt` | `exploit-authz.txt` (425行) | 水平/垂直越权、workflow bypass、IDOR |
| `ssrf-exploit.txt` | `exploit-ssrf.txt` (502行) | 云元数据访问、内部服务探测、DNS rebinding、redirect abuse |
| `misconfig-exploit.txt` | `exploit-misconfig.txt` (369行) | Open redirect PoC、CORS exploitation、安全头确认 |

### `recon-blackbox.txt` 增强

从当前23行增强至150-200行，包含：
- 浏览器自动化侦察方法论（crawl + API discovery）
- 端点/参数枚举策略
- 认证上下文处理
- 输出格式要求（与 whitebox recon deliverable 格式对齐）

---

## S3 — Report Prompt 增强

### 移植 TS 结构化报告修改流程

`report-executive.txt` 改为结构化报告修改 prompt：

- 读取已拼接的 `{type}_exploitation_evidence.md` 文件
- 注入 Executive Summary 段落（漏洞统计、严重性分布、攻击面概述）
- 应用报告过滤规则
- 清理冗余（去除重复发现、统一格式）
- 添加 remediation 建议

### 动态变量

| 变量 | 来源 | 用途 |
|------|------|------|
| `{{REPORT_FILTERS_BLOCK}}` | `DistributedConfig.report` | 当配置了过滤规则时注入过滤指令块；否则为空 |
| `{{REPORT_FILTER_RULES}}` | 从 `min_severity`/`min_confidence`/`guidance` 生成 | 具体过滤条件文本（如 "Exclude vulnerabilities below HIGH severity"） |
| `{{VULN_SUMMARY_SUBSECTIONS}}` | 按 `vuln_classes` 动态生成 | 每个漏洞类的摘要子节模板 |

### PromptManager 扩展

在 `manager.py` 中新增这些变量的生成逻辑：

- `REPORT_FILTERS_BLOCK`: 当 `config.report` 有任何非 None 字段时渲染过滤指令块
- `REPORT_FILTER_RULES`: 将 severity/confidence 阈值转为自然语言文本
- `VULN_SUMMARY_SUBSECTIONS`: 遍历 `vuln_classes` 生成每个类型的摘要模板行

### ReportAssembler 增强

在 `blackbox/services/report_assembler.py` 中：
- 调用 `PromptManager` 生成报告过滤变量
- 将变量注入到 report agent 的 prompt context 中
- 通过 `prompt_variables` 参数传递

预计 `report-executive.txt` 目标行数：80-100行。

---

## S4 — 认证预校验

### 新增 `validate_authentication.py` (whitebox/services/)

直接复用现有 `AgentExecutor`，与 TS 实现方式完全一致：

```
validate_authentication(config, repo_path, web_url, prompt_manager, executor) -> AuthValidationResult
```

内部流程：
1. 通过 `PromptManager.load_sync("validate-authentication")` 加载 prompt
2. 注入 `{{WEB_URL}}`、`{{LOGIN_INSTRUCTIONS}}`、`{{PLAYWRIGHT_SESSION}}` 变量
3. 调用 `executor.execute()` → `run_claude_prompt()` → SDK 驱动 Playwright 浏览器
4. 解析结构化输出 `AuthValidationResult`

### AuthValidationResult 模型 (`shannon_core/models/`)

```python
@dataclass
class AuthValidationResult:
    success: bool
    failure_point: str | None  # "username_or_password" | "totp_secret" | "out_of_band"
    failure_detail: str | None
```

### 集成点

- Blackbox workflow: preflight 后、recon 前，作为独立 activity 调用
- 失败时: 抛出 `PentestError(error_code=ErrorCode.AUTH_FAILED, retryable=False)`
- 新增 Temporal activity `run_auth_validation` (2min timeout, 3 retries)

### Prompt

从 TS 移植 `validate-authentication.txt` (25行) 到 `prompts/` 目录。

---

## S5 — Playwright 反检测 + 会话隔离

### 会话隔离映射

在 `shannon_core/models/agents.py` 中新增 `PLAYWRIGHT_SESSION_MAPPING` dict：

```python
PLAYWRIGHT_SESSION_MAPPING: dict[str, str] = {
    "pre-recon-code": "agent1",
    "recon": "agent2",
    "validate-authentication": "agent1",
    "vuln-injection": "agent1",
    "vuln-xss": "agent2",
    "vuln-auth": "agent3",
    "vuln-ssrf": "agent4",
    "vuln-authz": "agent5",
    "vuln-misconfig": "agent6",
    "injection-exploit": "agent1",
    "xss-exploit": "agent2",
    "auth-exploit": "agent3",
    "ssrf-exploit": "agent4",
    "authz-exploit": "agent5",
    "misconfig-exploit": "agent6",
    "report-executive": "agent3",
    "recon-blackbox": "agent2",
}
```

`PromptManager` 渲染时通过 `prompt_template` 名称查找对应 session，替代当前硬编码的 `"agent1"`。

### 反检测配置

新增 `playwright_config_writer.py` (whitebox/services/)，移植 TS `playwright-config-writer.ts` 逻辑：

- 在 `<repo>/.shannon/` 下生成 `.playwright/cli.config.json`
- 写入 `scripts/stealth.js`，包含：
  - 删除 `navigator.webdriver`
  - 伪造 `navigator.plugins` 数组
  - mock `chrome.runtime`
  - `--disable-blink-features=AutomationControlled` Chrome flag
- 提供 `write_stealth_config(repo_path)` 和 `cleanup_stealth_config(repo_path)` 方法

### 集成点

- Whitebox workflow: preflight 阶段调用 `write_stealth_config()`，完成/失败时调用 `cleanup_stealth_config()`
- Blackbox workflow: 同上

---

## S6 — 代码路径访问控制（SDK 硬拦截）

### 新增 `settings_writer.py` (whitebox/services/)

直接移植 TS `settings-writer.ts` (41行) 逻辑：

- `sync_code_path_deny_rules(repo_path, rules)`: 从 config 的 `avoid` 规则中筛选 `code_path` 类型，将每个 glob 映射为 `Read()`/`Edit()` tool 的 deny 条目，写入 `~/.claude/settings.json`
- `cleanup_settings()`: 当无 avoid 规则时删除 settings 文件
- 文件格式与 TS 完全一致：

```json
{
  "permissions": {
    "deny": [
      "Read(./secrets/**)",
      "Edit(./secrets/**)"
    ]
  }
}
```

### 集成点

- Whitebox workflow: preflight 后、pre-recon 前调用 `sync_code_path_deny_rules()`
- Blackbox workflow: 同上
- Workflow 完成/失败时: 调用 `cleanup_settings()`

---

## S7 — Preflight 安全检查补全

### 完整 preflight 检查序列

对齐 TS 的 5 项检查：

| 顺序 | 检查 | 实现方式 |
|------|------|---------|
| 1 | Repo 路径 + `.git` | 已有（仅 whitebox 和有 repo 的 blackbox） |
| 2 | Config 解析验证 | `parse_config()` + `distribute_config()` |
| 3 | `code_path` glob 匹配验证 | `pathlib.glob()` 验证每条 rule 至少匹配一个文件 |
| 4 | 凭据验证 | 直接 HTTP 请求验证（见下方） |
| 5 | 目标 URL 可达性 + 安全检查 | DNS + HTTP HEAD + SSRF/loopback 检查 |

### 凭据验证

新增 `credential_validator.py` (shannon_core/utils/)，对齐 TS `validateCredential` 逻辑：

| Provider | 验证方式 |
|----------|---------|
| `anthropic_api` | POST `https://api.anthropic.com/v1/messages` 最小请求，检查非 401/403 |
| `bedrock` | 用 `boto3` 调用 `sts:GetCallerIdentity` |
| `vertex` | 用 `google-cloud-aiplatform` 验证 GCP 项目访问权限 |
| `litellm_router` | 用配置的 `base_url` + `auth_token` 发测试请求 |

失败时抛出 `PentestError(error_code=ErrorCode.AUTH_FAILED, retryable=False)`。

### URL 安全检查

新增 `security.py` (shannon_core/utils/)：

| 函数 | 功能 |
|------|------|
| `resolve_host(url) -> str` | DNS 解析，返回 pinned IP |
| `check_ssrf(ip) -> bool` | 检查不在 `169.254.0.0/16` |
| `check_loopback(ip) -> bool` | 检查不是 `127.0.0.1`/`::1`/`0.0.0.0` |
| `check_url_reachable(url, timeout=10) -> bool` | HTTP HEAD + TLS skip |

### Whitebox vs Blackbox preflight 差异

| 检查 | Whitebox | Blackbox |
|------|----------|----------|
| Config 解析 | ✅ | ✅ |
| 凭据验证 | ✅ | ✅ |
| Repo + `.git` | ✅ | 仅当 `repo_path` 存在时 |
| `code_path` glob 匹配 | ✅ | 仅当 `repo_path` 存在时 |
| URL 可达性 + SSRF/loopback | ✅ | ✅ |

Blackbox 的 `repo_path` 可选，支持无 repo 的纯 URL 独立扫描模式。

---

## S8 — 白盒 Recon 静态 Prompt

### 移植 `recon-static.txt`

从 TS 复制 `recon-static.txt` (380行) 到 `prompts/` 目录。核心特征：
- 无浏览器/HTTP 工具使用（纯源码分析）
- 3阶段 Task Agent 策略（源码映射 → 安全模式关联 → 攻击面文档化）
- 输出格式与 `recon.txt` 的 `recon_deliverable.md` 对齐

### 支持 prompt override

- `PipelineInput` / `AgentInput` 模型新增 `prompt_override: str | None = None` 字段
- `AgentExecutor.execute()` 接受 `prompt_override` 参数，当非 None 时替代 `AgentDefinition.prompt_template` 查找 prompt 文件
- `run_agent` activity 将该参数传递给 executor

### Whitebox workflow 集成

在 `WhiteboxScanWorkflow.run()` 中，recon agent 调用时传入 `prompt_override="recon-static"`。

---

## S9 — Blackbox per-agent Exploit Queue 门控

### 增强 `ExploitationChecker` (blackbox/services/exploitation_checker.py)

当前仅检查 queue 文件是否存在。增加检查文件内容是否非空：

```python
def should_exploit(self, repo_path: str, vuln_type: str, deliverables_subdir: str) -> bool:
    # 1. queue 文件是否存在
    # 2. vulnerabilities 数组是否非空
    # 两项都满足才返回 True
```

### Workflow 集成

在 `BlackboxScanWorkflow.run()` 中，exploit 阶段改为先过滤：

```python
types_to_exploit = [
    vt for vt in selected_classes
    if self._exploitation_checker.should_exploit(...)
]
# 只对 types_to_exploit 运行 exploit agent
```

没有可利用漏洞的 type 跳过，不浪费 LLM 调用，不产生空 evidence 文件。

---

## 新增依赖

| 包 | 用途 | 用于 |
|----|------|------|
| `httpx` | HTTP 客户端（凭据验证、URL 可达性检查） | shannon_core |
| `boto3` | AWS 凭据验证（可选，Bedrock 用户需要） | shannon_core |

---

## 新增文件清单

| 文件 | 位置 | 说明 |
|------|------|------|
| `vuln-misconfig.txt` | `prompts/` | 从 TS 移植 |
| `misconfig-exploit.txt` | `prompts/` | 移植 + 重构 |
| `_exploit-methodology.txt` | `prompts/shared/` | 新建（从 TS exploit prompts 抽取公共部分） |
| `recon-static.txt` | `prompts/` | 从 TS 移植 |
| `recon-blackbox.txt` | `prompts/` | 增强（23行 → 150-200行） |
| `report-executive.txt` | `prompts/` | 移植 + 重构（22行 → 80-100行） |
| `validate-authentication.txt` | `prompts/` | 从 TS 移植 |
| `5个 *-exploit.txt` | `prompts/` | 移植 + 重构（19行 → ~200-300行） |
| `credential_validator.py` | `shannon_core/utils/` | 新建 |
| `security.py` | `shannon_core/utils/` | 新建 |
| `validate_authentication.py` | `whitebox/services/` | 新建 |
| `playwright_config_writer.py` | `whitebox/services/` | 新建 |
| `settings_writer.py` | `whitebox/services/` | 新建 |

---

## 修改文件清单

| 文件 | 修改内容 |
|------|---------|
| `shannon_core/models/agents.py` | 新增 MISCONFIG_VULN/EXPLOIT, PLAYWRIGHT_SESSION_MAPPING |
| `shannon_core/models/queue_schemas.py` | 新增 MisconfigVulnerability |
| `shannon_core/models/config.py` | VulnClass 加 "misconfig", ALL_VULN_CLASSES 扩至6项 |
| `whitebox/prompts/manager.py` | PLAYWRIGHT_SESSION 查表, 新增 REPORT_FILTERS_BLOCK 等变量生成 |
| `whitebox/agents/executor.py` | 新增 prompt_override 参数支持 |
| `whitebox/pipeline/activities.py` | 增强 preflight, 新增 run_auth_validation activity |
| `whitebox/pipeline/shared.py` | PipelineInput/AgentInput 新增 prompt_override 字段 |
| `whitebox/pipeline/workflows.py` | recon 传 prompt_override, preflight 增强, 调用 settings_writer/stealth config |
| `blackbox/services/exploitation_checker.py` | 增加 queue 非空检查 |
| `blackbox/services/report_assembler.py` | 生成并注入报告过滤变量 |
| `blackbox/pipeline/workflows.py` | preflight 增强, queue 门控, 调用 settings_writer/stealth config/auth validation |
| `blackbox/pipeline/activities.py` | 增强 preflight, 新增 run_auth_validation activity |
