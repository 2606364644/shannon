## ADDED Requirements

### Requirement: 完整 YAML 字段参考
文档 SHALL 列出 `Config` 模型的所有 YAML 字段，包括字段名、类型、是否必填、默认值、说明。覆盖所有嵌套结构：Rules、Authentication、Credentials、PipelineConfig、ReportConfig。

#### Scenario: 用户编写配置文件
- **WHEN** 用户查看字段参考编写 YAML
- **THEN** 能知道每个字段的类型和含义，无需查看源码

### Requirement: 示例配置文件
文档 SHALL 提供至少两个完整示例：最小配置（仅必填字段）和完整配置（包含所有可选字段）。

#### Scenario: 用户复制示例配置
- **WHEN** 用户复制最小配置示例
- **THEN** 能直接用于 CLI 的 `--config` 参数

### Requirement: 规则系统详解
文档 SHALL 详细说明 Rules 系统：`avoid` 和 `focus` 规则列表、7 种规则类型（url_path、subdomain、domain、method、header、parameter、code_path）各自的含义和 `value` 格式要求。

#### Scenario: 用户配置扫描范围
- **WHEN** 用户阅读规则系统部分
- **THEN** 能编写规则来排除特定路径或聚焦特定域名

### Requirement: 认证配置详解
文档 SHALL 说明 4 种登录类型（form、sso、api、basic）的配置方式，包括 Credentials（username、password、totp_secret）、login_flow、SuccessCondition（url_contains、element_present、url_equals_exactly、text_contains）。

#### Scenario: 用户配置表单登录
- **WHEN** 用户按文档配置 form 类型认证
- **THEN** 能正确填写 login_url、credentials 和 success_condition

### Requirement: 漏洞类型配置说明
文档 SHALL 说明 `vuln_classes` 字段的可选值（injection、xss、auth、authz、ssrf）和 `exploit` 布尔标志的作用。

#### Scenario: 用户限定扫描范围
- **WHEN** 用户只想测试 XSS 和注入漏洞
- **THEN** 能通过文档知道如何配置 `vuln_classes: ["injection", "xss"]`

### Requirement: 报告配置说明
文档 SHALL 说明 ReportConfig 的三个字段：min_severity、min_confidence、guidance，以及各自的可选值。

#### Scenario: 用户自定义报告输出
- **WHEN** 用户设置 `min_severity: "high"`
- **THEN** 报告仅包含 high 和 critical 级别的发现
