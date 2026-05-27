# 配置参考

shannon-py 使用 YAML 配置文件定义扫描行为。配置文件通过 `parse_config` 函数加载并验证。

## 最小配置

最小的有效配置只需要一个字段：

```yaml
description: "My target web application"
```

所有其他字段均有默认值，可以省略。

## 完整 YAML 字段参考

### Config（顶层配置）

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `description` | string \| null | 否 | null | 目标应用描述 |
| `rules` | Rules \| null | 否 | null | 扫描范围规则 |
| `authentication` | Authentication \| null | 否 | null | 认证配置 |
| `pipeline` | PipelineConfig \| null | 否 | null | 流水线配置 |
| `vuln_classes` | list[VulnClass] \| null | 否 | null | 要测试的漏洞类型（null 表示全部） |
| `exploit` | boolean | 否 | true | 是否执行漏洞利用阶段 |
| `report` | ReportConfig \| null | 否 | null | 报告配置 |
| `rules_of_engagement` | string \| null | 否 | null | 交战规则（额外指令） |

### Rules（扫描范围规则）

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `avoid` | list[Rule] | 否 | [] | 排除规则列表 |
| `focus` | list[Rule] | 否 | [] | 聚焦规则列表 |

### Rule（规则）

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `description` | string | 是 | - | 规则描述 |
| `type` | RuleType | 是 | - | 规则类型 |
| `value` | string | 是 | - | 规则值 |

### ReportConfig（报告配置）

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `min_severity` | Severity \| null | 否 | null | 最低严重级别过滤 |
| `min_confidence` | Confidence \| null | 否 | null | 最低置信度过滤 |
| `guidance` | string \| null | 否 | null | 报告生成指导 |

### Authentication（认证配置）

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `login_type` | "form" \| "sso" \| "api" \| "basic" | 是 | - | 登录类型 |
| `login_url` | string | 是 | - | 登录页面 URL |
| `credentials` | Credentials | 是 | - | 认证凭据 |
| `login_flow` | list[string] \| null | 否 | null | 登录流程步骤 |
| `success_condition` | SuccessCondition | 是 | - | 登录成功判定条件 |

### Credentials（凭据）

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `username` | string | 是 | - | 用户名 |
| `password` | string \| null | 否 | null | 密码 |
| `totp_secret` | string \| null | 否 | null | TOTP 密钥（2FA） |

### SuccessCondition（登录成功判定）

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `type` | "url_contains" \| "element_present" \| "url_equals_exactly" \| "text_contains" | 是 | - | 判定方式 |
| `value` | string | 是 | - | 判定值 |

### PipelineConfig（流水线配置）

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `retry_preset` | "default" \| "subscription" \| null | 否 | null | 重试策略预设 |
| `max_concurrent_pipelines` | integer \| null | 否 | null | 最大并发流水线数 |

## 规则系统详解

规则用于控制扫描范围，分为 `avoid`（排除）和 `focus`（聚焦）两种。

- **avoid**：排除匹配的 URL / 路径 / 参数，不进行扫描
- **focus**：仅扫描匹配的目标，忽略其他内容

### 支持的规则类型

| 类型 | 说明 | value 格式 | 示例 |
|---|---|---|---|
| `url_path` | 排除/聚焦特定 URL 路径 | 路径模式 | `/api/internal/*` |
| `subdomain` | 排除/聚焦特定子域名 | 子域名模式 | `admin.example.com` |
| `domain` | 排除/聚焦特定域名 | 域名 | `example.com` |
| `method` | 排除/聚焦特定 HTTP 方法 | HTTP 方法 | `OPTIONS`, `TRACE` |
| `header` | 排除/聚焦特定请求头 | 头名:值模式 | `X-Internal: true` |
| `parameter` | 排除/聚焦特定参数 | 参数名模式 | `debug`, `test` |
| `code_path` | 排除/聚焦特定代码路径 | 文件路径模式 | `src/test/*` |

### url_path 规则校验

`url_path` 类型的规则值必须以 `/` 开头。解析器会在加载配置时进行验证，不符合要求会抛出 `CONFIG_VALIDATION_FAILED` 错误。

```yaml
rules:
  avoid:
    - description: "Exclude internal API"
      type: url_path
      value: /api/internal/*
```

## 认证配置详解

支持四种登录类型：

- **form**：传统表单登录，需要 `login_url` + `credentials` + `success_condition`
- **sso**：SSO 单点登录
- **api**：API 密钥认证
- **basic**：HTTP Basic 认证

### login_flow

对于复杂的登录流程，可以通过 `login_flow` 字段描述登录步骤。该字段是一个字符串列表，按顺序定义登录过程的各个阶段。

### success_condition

登录成功后有四种判定方式：

| 类型 | 说明 |
|---|---|
| `url_contains` | 登录后 URL 包含指定字符串 |
| `url_equals_exactly` | 登录后 URL 完全匹配 |
| `element_present` | 页面中存在指定元素 |
| `text_contains` | 页面文本包含指定内容 |

## 漏洞类型和报告配置

### vuln_classes（漏洞类型）

| 值 | 说明 |
|---|---|
| `injection` | SQL 注入、命令注入等 |
| `xss` | 跨站脚本 |
| `auth` | 认证相关漏洞 |
| `authz` | 授权/访问控制漏洞 |
| `ssrf` | 服务端请求伪造 |

如果 `vuln_classes` 为 null 或省略，默认测试全部 5 种漏洞类型。

### exploit 标志

- `true`（默认）：执行漏洞利用阶段
- `false`：仅分析，不执行利用（对应 CLI 的 `--no-exploit` 参数）

### ReportConfig（报告过滤）

- **min_severity**：`"low"` | `"medium"` | `"high"` | `"critical"` — 按严重级别过滤报告
- **min_confidence**：`"low"` | `"medium"` | `"high"` — 按置信度过滤报告
- **guidance**：自由文本，用于指导报告生成的方向和重点

## 完整配置示例

```yaml
description: "Example web application for security testing"

rules:
  avoid:
    - description: "Exclude internal API endpoints"
      type: url_path
      value: /api/internal/*
    - description: "Exclude admin subdomain"
      type: subdomain
      value: admin.example.com
    - description: "Exclude OPTIONS requests"
      type: method
      value: OPTIONS
    - description: "Exclude test parameters"
      type: parameter
      value: debug
  focus:
    - description: "Focus on main application domain"
      type: domain
      value: example.com
    - description: "Focus on API endpoints"
      type: url_path
      value: /api/*
    - description: "Focus on source code paths"
      type: code_path
      value: src/main/*

authentication:
  login_type: form
  login_url: "https://example.com/login"
  credentials:
    username: testuser
    password: testpass
    totp_secret: "JBSWY3DPEHPK3PXP"
  login_flow:
    - "Navigate to login page"
    - "Fill username field"
    - "Fill password field"
    - "Submit form"
    - "Enter TOTP code if prompted"
  success_condition:
    type: url_contains
    value: /dashboard

pipeline:
  retry_preset: default
  max_concurrent_pipelines: 4

vuln_classes:
  - injection
  - xss
  - auth
  - authz
  - ssrf

exploit: true

report:
  min_severity: medium
  min_confidence: medium
  guidance: "Focus on authentication and authorization bypass vulnerabilities"

rules_of_engagement: "Do not test denial of service vectors. Stop immediately if any data corruption is detected."
```

## 安全验证

配置加载时会进行以下安全检查：

- `description`、`rules_of_engagement`、`authentication.login_url`、`credentials.username` 字段会检查危险模式，包括路径遍历（`../`）、HTML 标签（`<>`）、`javascript:` 协议、`data:` 协议和 `file:` 协议
- `url_path` 类型规则的值必须以 `/` 开头
- 配置文件不能为空
- 配置文件必须是有效的 YAML 格式
