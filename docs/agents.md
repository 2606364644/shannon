# Shannon Agent 系统

## 1. Agent 总览表

| Agent名称 | 阶段 | 前置条件 | Prompt 模板 | Deliverable 文件名 | 模型层级 |
|---|---|---|---|---|---|
| pre-recon | 预侦察 | 无 | pre-recon-code | pre_recon_deliverable.md | large |
| recon | 侦察 | pre-recon | recon | recon_deliverable.md | medium |
| injection-vuln | 漏洞分析 | recon | vuln-injection | injection_analysis_deliverable.md | medium |
| xss-vuln | 漏洞分析 | recon | vuln-xss | xss_analysis_deliverable.md | medium |
| auth-vuln | 漏洞分析 | recon | vuln-auth | auth_analysis_deliverable.md | medium |
| ssrf-vuln | 漏洞分析 | recon | vuln-ssrf | ssrf_analysis_deliverable.md | medium |
| authz-vuln | 漏洞分析 | recon | vuln-authz | authz_analysis_deliverable.md | medium |
| recon-blackbox | 侦察(黑盒) | 无 | recon-blackbox | recon_deliverable.md | medium |
| injection-exploit | 漏洞利用 | recon | injection-exploit | injection_exploitation_evidence.md | medium |
| xss-exploit | 漏洞利用 | recon | xss-exploit | xss_exploitation_evidence.md | medium |
| auth-exploit | 漏洞利用 | recon | auth-exploit | auth_exploitation_evidence.md | medium |
| ssrf-exploit | 漏洞利用 | recon | ssrf-exploit | ssrf_exploitation_evidence.md | medium |
| authz-exploit | 漏洞利用 | recon | authz-exploit | authz_exploitation_evidence.md | medium |
| report | 报告 | 全部5个exploit agent | report-executive | comprehensive_security_assessment_report.md | medium |

---

## 2. 流水线阶段分组

### 2.1 白盒流水线 (Whitebox)

```
preflight → pre-recon → recon → [injection-vuln, xss-vuln, auth-vuln, ssrf-vuln, authz-vuln]（并行）
                            ↓
                    [injection-exploit, xss-exploit, auth-exploit, ssrf-exploit, authz-exploit]（并行）
                            ↓
                    汇总报告 → report
```

**执行顺序：**

1. **预侦察**（串行）：`pre-recon` 独占执行，使用 large 模型，拥有唯一完整的源代码访问权限
2. **侦察**（串行）：`recon` 依赖 pre-recon 产出，结合浏览器交互和源代码分析进行攻击面映射
3. **漏洞分析**（并行）：5个 vuln agent 同时启动，各自分析不同漏洞类型的源代码数据流
4. **漏洞利用**（并行）：5个 exploit agent 同时启动，针对对应漏洞类型执行实际利用
5. **报告**（串行）：`report` 等待所有 exploit agent 完成后生成综合安全评估报告

### 2.2 黑盒流水线 (Blackbox)

```
preflight → recon-blackbox → [injection-exploit, xss-exploit, auth-exploit, ssrf-exploit, authz-exploit]（并行）
                                    ↓
                            汇总报告 → report
```

**执行顺序：**

1. **侦察**（串行）：`recon-blackbox` 无前置依赖，仅通过外部访问发现攻击面
2. **漏洞利用**（并行）：5个 exploit agent 同时启动，直接针对目标发起攻击
3. **报告**（串行）：`report` 生成最终报告

---

## 3. 各 Agent 详细说明

### 3.1 pre-recon（预侦察 Agent）

- **职责**：作为流水线中唯一拥有完整源代码访问权限的 Agent，对目标代码库进行全面的安全架构分析，生成供所有后续 Agent 使用的基础情报。分析涵盖架构、认证机制、攻击面、XSS/SSRF sink、数据安全等方面。
- **输入**：目标应用源代码（位于工作目录）、项目描述（`DESCRIPTION`）、待测试漏洞类型列表（`VULN_CLASSES_TESTED`）、确定性 code_index 产物（`code_index.json`、`code_index_summary.md`）
- **输出**：`pre_recon_deliverable.md`，包含执行摘要、架构与技术栈、认证与授权、数据安全、攻击面分析、基础设施安全、代码索引、关键文件路径、XSS Sinks、SSRF Sinks 等结构化章节。同时输出 `entry_points.json`（裁决结果）并复制发现的 API schema 文件到 `schemas/` 目录。
- **Prompt 策略要点**：
  - **Phase 0 入口点裁决**：系统性地审查 code_index 产出的每个候选入口点，高置信度（≥0.8）自动确认，低置信度（`needs_llm_review=true`）由 LLM 读取源码上下文后判定 confirmed/rejected/reclassified，输出 `entry_points.json`
  - **Phase 1 补充发现**：Entry Point Mapper Agent 从"全知全能的发现者"转为"补充发现者"，专注于 code_index 确定性规则无法覆盖的模式（配置文件路由、动态注册、未知框架）
  - Phase 1 启动 Architecture Scanner、Entry Point Mapper（补充发现）、Security Pattern Hunter；Phase 2 启动 XSS/Injection Sink Hunter、SSRF/External Request Tracer、Data Security Auditor；Phase 3 综合所有发现生成报告
  - 强调"瀑布影响"：此 Agent 的分析不完整将导致后续 10+ 个 Agent 出现盲区
  - 所有源代码分析必须通过 Task Agent 完成，禁止直接使用 Read/Glob/Grep
  - 从外部攻击者视角分析，仅关注网络可达的攻击面
  - 使用 TodoWrite 工具追踪各阶段进度
  - 采用分块写入（CHUNKED WRITING）避免超出 32K token 限制
  - 裁决完成后，确定性 `rebuildCallChains` activity 从确认的入口点构建 CallChain，更新 `code_index.json`

### 3.2 recon（侦察 Agent - 白盒）

- **职责**：作为攻击面架构师，结合预侦察报告、浏览器交互探索和源代码分析，创建全面的应用攻击面映射。为所有后续漏洞分析和利用 Agent 提供基础情报。
- **输入**：`pre_recon_deliverable.md`（必须首先读取理解）、目标 URL（`WEB_URL`）、源代码路径（`REPO_PATH`）、登录指引（`LOGIN_INSTRUCTIONS`）、规则配置（`RULES_AVOID`/`RULES_FOCUS`）
- **输出**：`recon_deliverable.md`，包含执行摘要、技术与服务映射、认证与会话管理流程、角色分配与权限存储、API 端点清单、输入向量、网络与交互映射、角色与权限架构、授权漏洞候选端点、注入源等章节。
- **Prompt 策略要点**：
  - 四步方法论：综合初始数据 → 浏览器交互探索 → 并行 Task Agent 源代码关联分析 → 综合文档化
  - 启动并行 Task Agent：Route Mapper、Authorization Checker、Input Validator、Session Handler、Authorization Architecture Agent
  - 使用 playwright-cli 进行浏览器自动化（带 session 隔离参数）
  - 重点映射授权架构：角色层次、权限模型、授权决策点、对象所有权验证模式
  - 输出专门的授权漏洞候选端点列表（水平/垂直/上下文三类），供 authz-vuln Agent 使用
  - 仅关注网络可达的组件，排除本地开发工具和构建脚本

### 3.3 injection-vuln（注入漏洞分析 Agent）

- **职责**：作为注入分析专家，专注于白盒代码分析和数据流追踪，识别 SQL 注入、命令注入、LFI/RFI、SSTI、路径遍历和不安全反序列化漏洞。通过源到 sink 的完整追踪证明漏洞可达性。
- **输入**：`recon_deliverable.md`（主要情报来源）、`pre_recon_deliverable.md`（Section 7 注入源列表）、目标 URL、源代码路径
- **输出**：`injection_analysis_deliverable.md`（Markdown 分析报告）和 `injection_exploitation_queue.json`（结构化 JSON 漏洞队列）
- **Prompt 策略要点**：
  - 七步负向注入漏洞分析方法论：为每个注入源创建 Todo → 追踪数据流路径（含路径分叉处理） → 检测 sink 并标记 slot 类型 → 匹配净化与 sink 上下文 → 判定漏洞/安全 → 记录到发现列表 → 评分置信度
  - Sink 类型分类：SQLi（DB 调用、原始 SQL）、Command（exec、system、subprocess）、File（include、require、fopen）、SSTI（template render/compile）、Deserialize（pickle、unserialize）
  - Slot 标签系统：SQL-val/like/num/enum/ident、CMD-argument/part-of-string、FILE-path/include、TEMPLATE-expression、DESERIALIZE-object、PATH-component
  - 关键规则：净化后的拼接会使净化失效；仅纳入 `externally_exploitable=true` 的漏洞
  - 每个漏洞包含 `witness_payload`（概念验证输入，不在分析阶段执行）

### 3.4 xss-vuln（XSS 漏洞分析 Agent）

- **职责**：作为跨站脚本分析专家，通过 sink 到 source 的反向污点分析，识别所有类型的 XSS 漏洞（反射型、存储型、DOM 型）。核心任务是发现输出编码与渲染上下文不匹配的位置。
- **输入**：`recon_deliverable.md`、`pre_recon_deliverable.md`（Section 9 XSS Sinks 列表）、目标 URL、源代码路径
- **输出**：`xss_analysis_deliverable.md`（Markdown 分析报告）和 `xss_exploitation_queue.json`（结构化 JSON 漏洞队列）
- **Prompt 策略要点**：
  - 反向追踪方法论：从 XSS sink 出发 → 反向追踪数据来源 → 检查净化 → 判定漏洞
  - 早期终止规则：若遇到上下文匹配的净化器且无中间变异，则标记为 SAFE 并停止追踪
  - 数据库读取检查点：若反向追踪到达数据库读取操作且之前无有效净化，标记为存储型 XSS
  - 渲染上下文分类：HTML_BODY、HTML_ATTRIBUTE、JAVASCRIPT_STRING、URL_PARAM、CSS_VALUE，每种需要不同的编码防御
  - 高级主题考量：DOM Clobbering、mXSS、模板注入、CSP 绕过
  - 使用终端 curl 测试反射型 XSS，使用 playwright-cli 测试 DOM 型和存储型 XSS
  - 自 XSS（Self-XSS）不算有效发现

### 3.5 auth-vuln（认证漏洞分析 Agent）

- **职责**：作为认证分析专家，专注于白盒代码审计，识别应用认证机制中的逻辑缺陷。核心任务是找出应用未能正确回答"你是你声称的那个人吗？"的位置。
- **输入**：`recon_deliverable.md`、`pre_recon_deliverable.md`（Section 3 和 6 用于指导分析）、目标 URL、源代码路径
- **输出**：`auth_analysis_deliverable.md`（Markdown 分析报告）和 `auth_exploitation_queue.json`（结构化 JSON 漏洞队列）
- **Prompt 策略要点**：
  - 九项检查方法论：传输与缓存 → 速率限制/CAPTCHA/监控 → 会话管理(Cookie) → Token/会话属性 → 会话固定 → 密码与账户策略 → 登录/注册响应 → 恢复与登出 → SSO/OAuth
  - 漏洞类型分类：Authentication_Bypass、Session_Management_Flaw、Login_Flow_Logic、Token_Management_Issue、Reset_Recovery_Flaw、Transport_Exposure、Abuse_Defenses_Missing、OAuth_Flow_Issue
  - 特别关注 nOAuth 攻击：验证 OAuth 用户标识使用不可变 `sub` 声明而非可变属性
  - 置信度向下取整规则：不确定时优先使用 Medium/Low 以减少误报
  - 每个检查必须给出最终 verdict（vulnerable 或 safe）

### 3.6 ssrf-vuln（SSRF 漏洞分析 Agent）

- **职责**：作为 SSRF 分析专家，通过白盒代码分析识别用户输入如何影响服务器端出站 HTTP 请求。核心任务是发现应用未能正确限制请求目标的位置。
- **输入**：`recon_deliverable.md`、`pre_recon_deliverable.md`（Section 10 SSRF Sinks 列表）、目标 URL、源代码路径
- **输出**：`ssrf_analysis_deliverable.md`（Markdown 分析报告）和 `ssrf_exploitation_queue.json`（结构化 JSON 漏洞队列）
- **Prompt 策略要点**：
  - 七项检查方法论：HTTP 客户端使用模式 → 协议与 scheme 验证 → 主机名和 IP 地址验证 → 端口限制 → URL 解析绕过 → 请求修改与头部 → 响应处理与信息泄露
  - 反向污点分析方法：从 SSRF sink 出发反向追踪，检查净化器是否匹配上下文
  - SSRF 分类：URL_Manipulation、Redirect_Abuse、Webhook_Injection、API_Proxy_Bypass、File_Fetch_Abuse、Service_Discovery
  - 攻击分类：Reflected SSRF（立即用户输入）、Stored SSRF（数据库读取）、Blind SSRF（无响应）、Semi-blind SSRF（仅错误/时间信息）
  - 特别关注云元数据端点（169.254.169.254 等）

### 3.7 authz-vuln（授权漏洞分析 Agent）

- **职责**：作为授权分析专家，专注于白盒代码审计，识别应用授权机制中的逻辑缺陷。核心任务是找出应用未能正确回答"你被允许执行这个操作吗？"的位置。
- **输入**：`recon_deliverable.md`（Section 8 授权漏洞候选端点列表：水平/垂直/上下文三类）、`pre_recon_deliverable.md`、目标 URL、源代码路径
- **输出**：`authz_analysis_deliverable.md`（Markdown 分析报告）和 `authz_exploitation_queue.json`（结构化 JSON 漏洞队列）
- **Prompt 策略要点**：
  - 三维分析方法论：
    - **水平授权分析**：追踪端点到副作用，检查是否存在充足的所有权验证 guard（会话认证 + 标识符绑定当前用户 + 租户成员验证）
    - **垂直授权分析**：追踪端点到特权操作，检查是否存在充足的角色/能力 guard
    - **上下文/工作流授权分析**：向前追踪工作流步骤，验证后续步骤是否验证前置状态
  - Guard 充足性标准：必须在副作用之前执行、支配所有代码路径、使用正确的 guard
  - UI 层面的检查（隐藏按钮等）不算有效 guard
  - 特别关注多租户数据隔离和共享服务数据泄露

### 3.8 recon-blackbox（黑盒侦察 Agent）

- **职责**：在无源代码访问的条件下，仅通过外部访问对目标 Web 应用进行黑盒侦察，发现和映射攻击面。
- **输入**：目标 URL（`WEB_URL`）、认证上下文（`AUTH_CONTEXT`）、规则配置
- **输出**：`recon_deliverable.md`，包含技术栈检测、端点发现、输入向量识别、认证流程映射、访问控制映射
- **Prompt 策略要点**：
  - 五项任务：技术检测 → 端点发现 → 输入向量识别 → 认证分析 → 访问控制映射
  - 仅使用浏览器工具和 HTTP 请求进行探索
  - 无源代码访问，完全依赖外部观察
  - 使用 playwright-cli 和 HTTP 请求系统性地探索应用

### 3.9 injection-exploit（注入漏洞利用 Agent）

- **职责**：验证和利用注入漏洞，将分析阶段发现的潜在漏洞转化为实际可证明的安全影响。
- **输入**：目标 URL（`WEB_URL`）、从 injection-vuln Agent 的 exploitation queue 传入的漏洞条目（`VULNERABILITY_ENTRIES`）、认证上下文
- **输出**：`injection_exploitation_evidence.md`，包含每个漏洞的利用证明、HTTP 请求/响应和影响评估
- **Prompt 策略要点**：
  - 对每个漏洞：验证注入点存在 → 使用建议技术尝试利用 → 记录成功或失败的证据
  - 遵循强制利用约束：仅从外部网络对目标 URL 执行，不尝试需要内网访问的漏洞
  - 判定分类：EXPLOITED（成功）、BLOCKED_BY_SECURITY（被 WAF/控制阻止）、OUT_OF_SCOPE_INTERNAL（需要内网访问）、FALSE_POSITIVE（误报）

### 3.10 xss-exploit（XSS 漏洞利用 Agent）

- **职责**：验证和利用跨站脚本漏洞，针对不同渲染上下文构造并执行 XSS payload。
- **输入**：目标 URL（`WEB_URL`）、从 xss-vuln Agent 的 exploitation queue 传入的漏洞条目（`VULNERABILITY_ENTRIES`）、认证上下文
- **输出**：`xss_exploitation_evidence.md`，包含每个漏洞的利用证明、使用的 payload 和影响评估
- **Prompt 策略要点**：
  - 对每个漏洞：验证 XSS sink 存在 → 根据渲染上下文构造并执行 XSS payload → 记录成功或失败的证据
  - 遵循强制利用约束和判定分类规则

### 3.11 auth-exploit（认证漏洞利用 Agent）

- **职责**：验证和利用认证漏洞，对分析阶段发现的认证弱点发起实际攻击。
- **输入**：目标 URL（`WEB_URL`）、从 auth-vuln Agent 的 exploitation queue 传入的漏洞条目（`VULNERABILITY_ENTRIES`）、认证上下文
- **输出**：`auth_exploitation_evidence.md`，包含每个漏洞的利用证明、执行步骤和影响评估
- **Prompt 策略要点**：
  - 对每个漏洞：测试认证弱点 → 使用建议技术尝试利用 → 记录成功或失败的证据
  - 攻击技术包括：brute_force_login、credential_stuffing、session_hijacking、session_fixation、token_replay 等

### 3.12 ssrf-exploit（SSRF 漏洞利用 Agent）

- **职责**：验证和利用服务端请求伪造漏洞，尝试访问内部资源或敏感端点。
- **输入**：目标 URL（`WEB_URL`）、从 ssrf-vuln Agent 的 exploitation queue 传入的漏洞条目（`VULNERABILITY_ENTRIES`）、认证上下文
- **输出**：`ssrf_exploitation_evidence.md`，包含每个漏洞的利用证明、使用的 payload 和影响评估
- **Prompt 策略要点**：
  - 对每个漏洞：构造适当请求测试 SSRF 向量 → 尝试访问内部资源或敏感端点 → 记录成功或失败的证据
  - 攻击技术包括：internal_service_access、cloud_metadata_retrieval、port_scanning、webhook_abuse 等

### 3.13 authz-exploit（授权漏洞利用 Agent）

- **职责**：验证和利用授权漏洞，尝试访问超出预期范围的资源或执行越权操作。
- **输入**：目标 URL（`WEB_URL`）、从 authz-vuln Agent 的 exploitation queue 传入的漏洞条目（`VULNERABILITY_ENTRIES`）、认证上下文
- **输出**：`authz_exploitation_evidence.md`，包含每个漏洞的利用证明、角色提升步骤和影响评估
- **Prompt 策略要点**：
  - 对每个漏洞：测试授权绕过 → 尝试访问资源或执行超出范围的行动 → 记录成功或失败的证据
  - 覆盖水平越权（访问其他用户数据）、垂直越权（提升到高权限角色）、上下文绕过（跳过工作流步骤）

### 3.14 report（报告生成 Agent）

- **职责**：作为安全报告分析师，审阅并增强综合安全评估报告，添加执行摘要并确保格式规范。
- **输入**：目标 URL（`WEB_URL`）、`comprehensive_security_assessment_report.md`（由前置步骤汇总生成）、项目描述（`DESCRIPTION`）、交战规则（`RULES_OF_ENGAGEMENT`）
- **输出**：`comprehensive_security_assessment_report.md`（增强版）
- **Prompt 策略要点**：
  - 在报告顶部添加执行摘要：整体风险评级（Critical/High/Medium/Low）、发现并利用的漏洞总数、关键建议
  - 确保每个漏洞章节包含：严重程度评级、复现步骤、修复建议
  - 移除冗余和不一致性
  - 保留所有技术证据和发现

---

## 4. 漏洞类型与 Agent 映射

| 漏洞类型 | 分析 Agent | 利用 Agent | 分析 Deliverable | 利用 Deliverable |
|---|---|---|---|---|
| injection（SQLi、命令注入、LFI/RFI、SSTI、路径遍历、反序列化） | injection-vuln | injection-exploit | injection_analysis_deliverable.md | injection_exploitation_evidence.md |
| xss（反射型、存储型、DOM 型） | xss-vuln | xss-exploit | xss_analysis_deliverable.md | xss_exploitation_evidence.md |
| auth（认证绕过、会话管理缺陷、OAuth 问题） | auth-vuln | auth-exploit | auth_analysis_deliverable.md | auth_exploitation_evidence.md |
| ssrf（URL 操纵、重定向滥用、Webhook 注入） | ssrf-vuln | ssrf-exploit | ssrf_analysis_deliverable.md | ssrf_exploitation_evidence.md |
| authz（水平越权、垂直越权、工作流绕过） | authz-vuln | authz-exploit | authz_analysis_deliverable.md | authz_exploitation_evidence.md |

---

## 5. 白盒/黑盒 Agent 差异

### 5.1 白盒流水线 (Whitebox)

**适用场景**：拥有目标应用源代码访问权限

**独占 Agent**：
- `pre-recon`：仅白盒流水线使用，分析源代码生成架构情报
- `recon`：白盒侦察，结合源代码和浏览器交互
- 5个 vuln 分析 Agent（injection-vuln、xss-vuln、auth-vuln、ssrf-vuln、authz-vuln）：白盒代码审计，通过 Task Agent 分析源代码中的数据流

**执行流程**：
```
pre-recon → recon → 5个 vuln Agent（并行）→ 5个 exploit Agent（并行）→ report
```

### 5.2 黑盒流水线 (Blackbox)

**适用场景**：无源代码访问权限，仅通过外部网络访问目标

**独占 Agent**：
- `recon-blackbox`：仅黑盒流水线使用，通过浏览器和 HTTP 请求发现攻击面

**共享 Agent**：
- 5个 exploit Agent（injection-exploit、xss-exploit、auth-exploit、ssrf-exploit、authz-exploit）：两个流水线共用
- `report`：两个流水线共用

**执行流程**：
```
recon-blackbox → 5个 exploit Agent（并行）→ report
```

### 5.3 关键差异总结

| 维度 | 白盒流水线 | 黑盒流水线 |
|---|---|---|
| 源代码访问 | 有（pre-recon 为唯一持有者） | 无 |
| 侦察阶段 | pre-recon + recon（两步） | recon-blackbox（一步） |
| 漏洞分析阶段 | 有（5个 vuln Agent 并行代码审计） | 无（直接进入利用阶段） |
| 漏洞利用阶段 | 5个 exploit Agent（基于代码审计结果） | 5个 exploit Agent（基于黑盒侦察结果） |
| 数据流方向 | 源代码 → 分析 → 利用 → 报告 | 外部观察 → 利用 → 报告 |
| Exploit Agent 输入 | 从对应 vuln Agent 的 exploitation queue JSON 获取结构化漏洞数据 | 从 recon-blackbox 产出获取攻击面信息 |

---

## 6. Deliverable 文件格式

### 6.1 总体说明

所有 deliverable 均为 Markdown 文件，保存在 `.shannon/deliverables/` 目录下。Agent 使用 `save-deliverable` CLI 工具保存文件，该工具提供自动验证功能。大型报告采用分块写入策略（CHUNKED WRITING），通过 Write/Edit 工具分多次写入以避免超出 32K token 限制。

### 6.2 各 Agent 输出文件

| Agent | Markdown Deliverable | Exploitation Queue JSON |
|---|---|---|
| pre-recon | pre_recon_deliverable.md | 无 |
| recon | recon_deliverable.md | 无 |
| injection-vuln | injection_analysis_deliverable.md | injection_exploitation_queue.json |
| xss-vuln | xss_analysis_deliverable.md | xss_exploitation_queue.json |
| auth-vuln | auth_analysis_deliverable.md | auth_exploitation_queue.json |
| ssrf-vuln | ssrf_analysis_deliverable.md | ssrf_exploitation_queue.json |
| authz-vuln | authz_analysis_deliverable.md | authz_exploitation_queue.json |
| recon-blackbox | recon_deliverable.md | 无 |
| injection-exploit | injection_exploitation_evidence.md | 无 |
| xss-exploit | xss_exploitation_evidence.md | 无 |
| auth-exploit | auth_exploitation_evidence.md | 无 |
| ssrf-exploit | ssrf_exploitation_evidence.md | 无 |
| authz-exploit | authz_exploitation_evidence.md | 无 |
| report | comprehensive_security_assessment_report.md | 无 |

### 6.3 Exploitation Queue JSON 格式

漏洞分析 Agent 除了生成 Markdown 报告外，还会生成结构化的 exploitation queue JSON 文件。该 JSON 使用 `VulnerabilityQueue` 模型，包含一个 `vulnerabilities` 数组。

**基础字段（BaseVulnerability）**：

| 字段 | 类型 | 说明 |
|---|---|---|
| ID | string | 唯一标识符（如 INJ-VULN-01、XSS-VULN-01） |
| vulnerability_type | string | 漏洞类型分类 |
| externally_exploitable | bool | 是否可从外部网络利用 |
| confidence | string | 置信度（high/med/low） |
| notes | string或null | 备注、假设、特殊情况说明 |

**各类型特有字段**：

**InjectionVulnerability**（injection-vuln）：`source`、`combined_sources`、`path`、`sink_call`、`slot_type`、`sanitization_observed`、`concat_occurrences`、`verdict`、`mismatch_reason`、`witness_payload`

**XssVulnerability**（xss-vuln）：`source`、`source_detail`、`path`、`sink_function`、`render_context`、`encoding_observed`、`verdict`、`mismatch_reason`、`witness_payload`

**AuthVulnerability**（auth-vuln）：`source_endpoint`、`vulnerable_code_location`、`missing_defense`、`exploitation_hypothesis`、`suggested_exploit_technique`

**SsrfVulnerability**（ssrf-vuln）：`source_endpoint`、`vulnerable_parameter`、`vulnerable_code_location`、`missing_defense`、`exploitation_hypothesis`、`suggested_exploit_technique`

**AuthzVulnerability**（authz-vuln）：`endpoint`、`vulnerable_code_location`、`role_context`、`guard_evidence`、`side_effect`、`reason`、`minimal_witness`

### 6.4 Markdown 报告通用结构

各漏洞分析 Agent 的 Markdown 报告通常遵循以下结构：

1. **执行摘要**：分析状态、关键结论、文档目的
2. **主要漏洞模式**：发现的共性漏洞模式描述、影响和代表条目
3. **利用战略情报**：防御机制分析（WAF/CSP/Cookie 安全等）、技术栈确认、推荐利用策略
4. **已确认安全的向量**：经追踪确认具有健全防御的输入向量列表
5. **分析约束与盲区**：未能完全分析的异步流程、存储过程、第三方依赖等
