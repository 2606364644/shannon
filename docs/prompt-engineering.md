# Shannon-Py Prompt 工程指南

## 1. 模板系统概述

Shannon-Py 使用基于文件的 Prompt 模板系统驱动其 14 个 AI 安全测试代理。系统由 `PromptManager` 类管理，负责模板加载、片段引入和变量插值。

### 核心架构

模板文件存储在项目根目录的 `prompts/` 目录中，每个代理对应一个 `.txt` 文件。文件命名规则为 `<template_name>.txt`，其中 `template_name` 来自 `AGENTS` 字典中 `AgentDefinition.prompt_template` 字段。

### 加载流程

```python
prompt = prompt_manager.load_sync(
    template_name="vuln-injection",
    variables={"web_url": "https://target.com", "repo_path": "/repo"},
    config=distributed,
    pipeline_testing=False,
)
```

`load_sync` 方法执行三个阶段：

1. **文件定位** — 根据模板名称拼接路径，若启用 pipeline-testing 则切换到子目录
2. **`_process_includes`** — 递归解析所有 `@include()` 指令，展开共享片段
3. **`_interpolate`** — 替换所有 `{{VARIABLE}}` 占位符，包括配置变量和自定义变量

最终将多余空行压缩为双换行后返回渲染结果。

### 目录结构

```
prompts/
├── pre-recon-code.txt          # 白盒预侦察
├── recon.txt                   # 白盒侦察
├── recon-blackbox.txt          # 黑盒侦察
├── vuln-injection.txt          # 注入漏洞分析
├── vuln-xss.txt                # XSS 漏洞分析
├── vuln-auth.txt               # 认证漏洞分析
├── vuln-ssrf.txt               # SSRF 漏洞分析
├── vuln-authz.txt              # 授权漏洞分析
├── injection-exploit.txt       # 注入漏洞利用
├── xss-exploit.txt             # XSS 漏洞利用
├── auth-exploit.txt            # 认证漏洞利用
├── ssrf-exploit.txt            # SSRF 漏洞利用
├── authz-exploit.txt           # 授权漏洞利用
├── report-executive.txt        # 执行报告生成
├── shared/                     # 共享片段目录
│   ├── _rules.txt
│   ├── _target.txt
│   ├── _vuln-scope.txt
│   ├── _exploit-scope.txt
│   ├── _rules-of-engagement.txt
│   ├── _code-path-rules.txt
│   └── login-instructions.txt
└── pipeline-testing/           # 测试模式模板
    ├── pre-recon-code.txt
    ├── recon.txt
    ├── vuln-injection.txt
    └── shared/
        └── _filesystem.txt
```

### 代理工作流顺序

模板按以下流水线顺序执行：

```
PRE-RECON-CODE → RECON → [VULN-* ×5 并行] → [*-EXPLOIT ×5 并行] → REPORT-EXECUTIVE
```

- **预侦察阶段**: `pre-recon-code.txt` — 唯一拥有完整源码访问权限的代理
- **侦察阶段**: `recon.txt` (白盒) 或 `recon-blackbox.txt` (黑盒) — 攻击面映射
- **漏洞分析阶段**: 5 个 `vuln-*.txt` 并行执行 — 注入、XSS、认证、SSRF、授权
- **漏洞利用阶段**: 5 个 `*-exploit.txt` 并行执行 — 对应 5 种漏洞类型
- **报告阶段**: `report-executive.txt` — 生成最终安全评估报告

---

## 2. 完整变量参考表

### 2.1 核心变量

以下变量由 `PromptManager._interpolate` 方法处理，按处理顺序排列：

| 变量名 | 数据来源 | 默认值 | 说明 |
|---|---|---|---|
| `{{WEB_URL}}` | `variables["web_url"]` | `""` | 目标应用 URL，几乎所有模板使用 |
| `{{REPO_PATH}}` | `variables["repo_path"]` | `""` | 本地仓库路径，白盒模板使用 |
| `{{PLAYWRIGHT_SESSION}}` | `variables["playwright_session"]` | `"agent1"` | 浏览器会话标识，用于会话隔离 |
| `{{DESCRIPTION}}` | `config.description` | `""` | 目标描述，自动添加 "Description: " 前缀（非空时） |
| `{{AUTH_CONTEXT}}` | `config.authentication` | `"No authentication configured"` | 认证信息，非空时显示 login_type |
| `{{RULES_AVOID}}` | `config.avoid` | `"None"` | 排除规则，每条以 `- ` 开头 |
| `{{RULES_FOCUS}}` | `config.focus` | `"None"` | 聚焦规则，每条以 `- ` 开头 |
| `{{VULN_CLASSES_TESTED}}` | `config.vuln_classes` | `"injection, xss, auth, authz, ssrf"` | 测试的漏洞类型列表 |
| `{{EXPLOITATION}}` | `config.exploit` | `"enabled"` | 是否启用漏洞利用（enabled/disabled） |
| `{{RULES_OF_ENGAGEMENT}}` | `config.rules_of_engagement` | `""` | 交战规则（原始文本） |
| `{{LOGIN_INSTRUCTIONS}}` | 固定值 | `""` | 登录指令（当前始终为空，保留接口） |

### 2.2 自定义变量

`_interpolate` 方法末尾遍历 `variables` 字典，将所有键名转为大写后替换对应占位符：

```python
for key, value in variables.items():
    token = "{{" + key.upper() + "}}"
    if token in result:
        result = result.replace(token, value)
```

这意味着通过 `prompt_variables` 传入的任意键值对都会被自动处理。例如：

```python
prompt_variables = {
    "vulnerability_entries": json.dumps(queue_data),
}
```

会将 `{{VULNERABILITY_ENTRIES}}` 替换为对应的 JSON 字符串。

### 2.3 共享片段中的变量

共享片段通过 `@include` 展开后参与变量替换，其中包含额外的变量：

| 变量名 | 所在片段 | 说明 |
|---|---|---|
| `{{CODE_RULES_AVOID}}` | `shared/_code-path-rules.txt` | 代码级排除规则 |
| `{{CODE_RULES_FOCUS}}` | `shared/_code-path-rules.txt` | 代码级聚焦规则 |
| `{{user_instructions}}` | `shared/login-instructions.txt` | 用户提供的登录指令 |
| `{{totp_secret}}` | `shared/login-instructions.txt` | TOTP 密钥 |

### 2.4 各模板变量使用矩阵

| 模板 | WEB_URL | REPO_PATH | PLAYWRIGHT_SESSION | DESCRIPTION | AUTH_CONTEXT | RULES_AVOID | RULES_FOCUS | VULN_CLASSES | RULES_OF_ENGAGEMENT | LOGIN_INSTRUCTIONS | VULNERABILITY_ENTRIES | CODE_RULES_* |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pre-recon-code | - | ✓ | - | ✓ | - | - | - | ✓ | ✓ | - | - | ✓ |
| recon | ✓ | ✓ | ✓ | ✓ | - | ✓ | ✓ | ✓ | ✓ | ✓ | - | ✓ |
| recon-blackbox | ✓ | - | - | - | ✓ | ✓ | ✓ | - | ✓ | - | - | - |
| vuln-injection | ✓(片段) | ✓(片段) | ✓ | - | - | ✓(片段) | - | - | ✓(片段) | ✓ | - | ✓ |
| vuln-xss | ✓(片段) | ✓(片段) | ✓ | - | - | ✓(片段) | - | - | ✓(片段) | ✓ | - | ✓ |
| vuln-auth | ✓(片段) | ✓(片段) | ✓ | - | - | ✓(片段) | - | - | ✓(片段) | ✓ | - | ✓ |
| vuln-ssrf | ✓(片段) | ✓(片段) | ✓ | - | - | ✓(片段) | - | - | ✓(片段) | ✓ | - | ✓ |
| vuln-authz | ✓(片段) | ✓(片段) | ✓ | - | - | ✓(片段) | - | - | ✓(片段) | ✓ | - | ✓ |
| injection-exploit | ✓ | - | - | - | ✓ | ✓ | ✓ | - | ✓ | - | ✓ | - |
| xss-exploit | ✓ | - | - | - | ✓ | ✓ | ✓ | - | ✓ | - | ✓ | - |
| auth-exploit | ✓ | - | - | - | ✓ | ✓ | ✓ | - | ✓ | - | ✓ | - |
| ssrf-exploit | ✓ | - | - | - | ✓ | ✓ | ✓ | - | ✓ | - | ✓ | - |
| authz-exploit | ✓ | - | - | - | ✓ | ✓ | ✓ | - | ✓ | - | ✓ | - |
| report-executive | ✓ | - | - | ✓ | - | - | - | - | ✓ | - | - | - |

注：标有"(片段)"的变量表示该变量出现在通过 `@include` 引入的共享片段中，而非模板主文件直接引用。

---

## 3. @include 机制

### 语法

在模板文件中使用 `@include(relative_path)` 引入外部文件片段：

```
@include(shared/_rules.txt)
```

### 路径解析规则

- 路径相对于当前模板所在目录解析
- 使用 `pathlib.Path.resolve()` 获取绝对路径
- 解析后的路径必须以 `base_dir` 为前缀，否则抛出路径遍历错误

```python
include_path = (base_dir / raw_path).resolve()
base_resolved = base_dir.resolve()
if not str(include_path).startswith(str(base_resolved)):
    raise PentestError(f"Path traversal in @include: {raw_path}", ...)
```

### 安全保护

系统防止路径遍历攻击。例如 `@include(../../etc/passwd)` 会被拒绝，因为解析后的路径不以 `base_dir` 开头。如果引用的文件不存在，`@include` 静默返回空字符串。

### 共享片段说明

| 片段文件 | 用途 | 引用者 |
|---|---|---|
| `shared/_target.txt` | 定义目标 URL 和文件系统路径 | 所有 vuln-\* 模板 |
| `shared/_vuln-scope.txt` | 定义外部攻击者范围约束 | 所有 vuln-\* 模板 |
| `shared/_exploit-scope.txt` | 定义漏洞利用约束和裁决分类 | exploit 模板 |
| `shared/_rules.txt` | 注入排除/聚焦规则 | 所有 vuln-\* 模板 |
| `shared/_rules-of-engagement.txt` | 交战规则包装器 | pre-recon-code、recon、所有 vuln-\* 模板 |
| `shared/_code-path-rules.txt` | 代码路径级别的排除/聚焦规则 | pre-recon-code、recon、所有 vuln-\* 模板 |
| `shared/login-instructions.txt` | 登录流程指令（表单/SSO） | 需要浏览器交互的模板 |

### 递归包含

`@include` 支持递归。被引入的文件中可以包含进一步的 `@include` 指令，系统会按深度优先顺序展开所有嵌套引用。例如：

- `vuln-injection.txt` 引用 `@include(shared/_target.txt)`
- `_target.txt` 包含 `{{WEB_URL}}` 和 `{{REPO_PATH}}` 变量
- 变量在所有片段展开完成后统一替换

pipeline-testing 模板同样使用 `@include`：

- `pipeline-testing/vuln-injection.txt` 引用 `@include(shared/_filesystem.txt)`
- 此处 `shared/` 解析为 `prompts/pipeline-testing/shared/`

---

## 4. 自定义 Prompt 指南

### 修改现有模板

直接编辑 `prompts/` 目录下的 `.txt` 文件即可。修改会在下次运行时生效。

**注意事项：**
- 保持 `{{VARIABLE}}` 语法不变，变量名必须与系统提供的数据匹配
- 保持 `@include()` 指令格式不变
- 不要在变量占位符周围添加额外空格
- 模板使用 XML 标签（如 `<role>`、`<objective>`）组织结构，保持一致性

### 创建新代理模板

1. 在 `prompts/` 目录创建新的 `.txt` 文件
2. 在 `AGENTS` 字典中注册代理定义，指定 `prompt_template` 字段
3. 如需复用内容，创建共享片段并使用 `@include` 引入

### 添加新变量

有两种方式：

**方式一：通过 prompt_variables 传递**

在 `AgentExecutor.execute()` 调用时传入 `prompt_variables` 字典。这些变量会在 `_interpolate` 末尾被自动处理：

```python
result = await executor.execute(
    agent_name="injection-exploit",
    repo_path="/path/to/repo",
    web_url="https://target.com",
    prompt_variables={
        "vulnerability_entries": json.dumps(queue_data),
        "custom_context": "additional info",
    },
)
```

模板中使用 `{{VULNERABILITY_ENTRIES}}` 和 `{{CUSTOM_CONTEXT}}` 即可。

**方式二：在 _interpolate 方法中硬编码**

修改 `PromptManager._interpolate` 方法，添加专用的替换逻辑。适用于需要条件格式化的变量，如 `{{DESCRIPTION}}` 的 "Description: " 前缀处理。

### 最佳实践

- **使用 @include 管理共享内容** — 将重复出现的指令（范围定义、规则、文件系统路径）提取到 `shared/` 目录
- **保持模板职责单一** — 每个模板对应一个明确的代理职责
- **利用 XML 标签组织结构** — 使用 `<role>`、`<objective>`、`<scope>` 等标签划分逻辑区块
- **变量命名使用大写** — `{{VARIABLE_NAME}}` 格式，键名不区分大小写（系统自动转大写）
- **避免深层嵌套 include** — 虽然 `@include` 支持递归，但过深的嵌套会增加调试难度

---

## 5. Pipeline-Testing 模式

### 激活方式

通过 CLI 标志 `--pipeline-testing` 激活，或在代码中设置 `pipeline_testing=True` 参数。

### 工作原理

```python
base_dir = self.prompts_dir
if pipeline_testing:
    base_dir = base_dir / "pipeline-testing"
```

激活后，模板加载路径从 `prompts/` 切换到 `prompts/pipeline-testing/`，系统会读取该目录下的简化模板而非生产模板。

### 模板差异

生产模板（如 `prompts/vuln-injection.txt`）包含完整的漏洞分析方法论、数据格式规范和详细的执行指令，通常有 300-400 行。测试模板则极简化：

**生产模板示例** (`prompts/pre-recon-code.txt`): 417 行，包含完整的代码分析方法论、Task Agent 策略、报告结构定义。

**测试模板示例** (`prompts/pipeline-testing/pre-recon-code.txt`):

```
@include(shared/_filesystem.txt)

Run: `save-deliverable --type CODE_ANALYSIS --content 'Pre-recon analysis complete'`.
Then say "Done".
```

测试模板的核心特征：
- 仅引用必要的共享片段（文件系统路径）
- 执行最简操作（保存占位交付物）
- 快速完成，不进行实际分析
- 验证工作流管道的连通性

### 适用场景

- **CI/CD 测试** — 验证代理启动、交付物保存和管道流转等基础功能
- **快速迭代** — 在开发新代理时验证模板加载和变量替换是否正确
- **集成测试** — 确认 14 个代理的完整执行序列可以顺利走通

---

## 6. Prompt 调试技巧

### 检查渲染后的 Prompt

工作区目录保存了每个代理的渲染后 prompt，可直接查看变量替换结果：

```
workspaces/<workspace-name>/prompts/<agent-name>.txt
```

这是调试变量替换问题最直接的方式。打开文件后可以看到所有 `{{VARIABLE}}` 是否被正确替换。

### 查看执行日志

```
workspaces/<workspace-name>/workflow.log
```

工作流日志记录了各阶段的执行状态，包括 prompt 加载是否成功、代理是否启动等。

### 查看代理日志

```
workspaces/<workspace-name>/agents/<agent-name>.log
```

每个代理的独立日志文件记录了详细的执行过程，可用于定位具体的执行失败原因。

### 使用 Pipeline-Testing 快速验证

当需要验证模板修改是否正确时，使用 pipeline-testing 模式可以快速确认：

1. 模板文件路径是否正确
2. `@include` 引用是否解析成功
3. 变量替换是否完成
4. 交付物保存是否正常

```bash
shannon run --pipeline-testing --config config.yaml
```

### 常见问题排查

| 现象 | 可能原因 | 排查方法 |
|---|---|---|
| 变量未被替换 | 变量名拼写错误或未传入 | 检查渲染后的 prompt 文件中残留的 `{{...}}` |
| @include 返回空 | 引用路径错误或文件不存在 | 确认路径相对于当前模板目录 |
| 路径遍历错误 | include 路径超出 base_dir | 确保引用文件在 prompts 目录树内 |
| Prompt 加载失败 | 模板文件不存在 | 检查 AgentDefinition.prompt_template 与文件名是否匹配 |
| 变量值为空 | config 未提供或字段为空 | 检查 config 对象的对应属性 |
