# Shannon API 参考文档

---

## shannon-core

### 模型类 (Model Classes)

#### `Config`

文件路径: `packages/core/src/shannon_core/models/config.py`

```python
class Config(BaseModel)
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `rules` | `Rules \| None` | `None` |
| `authentication` | `Authentication \| None` | `None` |
| `pipeline` | `PipelineConfig \| None` | `None` |
| `description` | `str \| None` | `None` |
| `vuln_classes` | `list[VulnClass] \| None` | `None` |
| `exploit` | `bool` | `True` |
| `report` | `ReportConfig \| None` | `None` |
| `rules_of_engagement` | `str \| None` | `None` |

顶层扫描配置模型，包含规则、认证、流水线、漏洞类别、利用、报告和交战规则等配置项。

---

#### `DistributedConfig`

文件路径: `packages/core/src/shannon_core/models/config.py`

```python
class DistributedConfig(BaseModel)
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `avoid` | `list[Rule]` | （必填） |
| `focus` | `list[Rule]` | （必填） |
| `description` | `str` | （必填） |
| `vuln_classes` | `list[VulnClass]` | （必填） |
| `exploit` | `bool` | （必填） |
| `report` | `ReportConfig` | （必填） |
| `rules_of_engagement` | `str` | （必填） |
| `authentication` | `Authentication \| None` | `None` |

经过展平和默认值填充后的配置模型，传递给各 Agent 使用。

---

#### `AgentDefinition`

文件路径: `packages/core/src/shannon_core/models/agents.py`

```python
class AgentDefinition(BaseModel)
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `name` | `AgentName` | （必填） |
| `display_name` | `str` | （必填） |
| `prerequisites` | `list[AgentName]` | （必填） |
| `prompt_template` | `str` | （必填） |
| `deliverable_filename` | `str` | （必填） |
| `model_tier` | `Literal["small", "medium", "large"]` | `"medium"` |

不可变（frozen）模型，定义单个 Agent 的名称、前置依赖、提示模板、输出文件名和模型层级。

---

#### `Rule`

文件路径: `packages/core/src/shannon_core/models/config.py`

```python
class Rule(BaseModel)
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `description` | `str` | （必填） |
| `type` | `RuleType` | （必填） |
| `value` | `str` | （必填） |

单条规则，包含描述、类型和匹配值。

---

#### `Rules`

文件路径: `packages/core/src/shannon_core/models/config.py`

```python
class Rules(BaseModel)
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `avoid` | `list[Rule]` | `[]` |
| `focus` | `list[Rule]` | `[]` |

规则集合，分为规避规则和聚焦规则。

---

#### `ReportConfig`

文件路径: `packages/core/src/shannon_core/models/config.py`

```python
class ReportConfig(BaseModel)
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `min_severity` | `Severity \| None` | `None` |
| `min_confidence` | `Confidence \| None` | `None` |
| `guidance` | `str \| None` | `None` |

报告生成配置，控制最低严重程度、最低置信度和附加指导信息。

---

#### `SuccessCondition`

文件路径: `packages/core/src/shannon_core/models/config.py`

```python
class SuccessCondition(BaseModel)
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `type` | `Literal["url_contains", "element_present", "url_equals_exactly", "text_contains"]` | （必填） |
| `value` | `str` | （必填） |

认证成功的判断条件。

---

#### `Credentials`

文件路径: `packages/core/src/shannon_core/models/config.py`

```python
class Credentials(BaseModel)
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `username` | `str` | （必填） |
| `password` | `str \| None` | `None` |
| `totp_secret` | `str \| None` | `None` |

目标系统的登录凭据。

---

#### `Authentication`

文件路径: `packages/core/src/shannon_core/models/config.py`

```python
class Authentication(BaseModel)
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `login_type` | `Literal["form", "sso", "api", "basic"]` | （必填） |
| `login_url` | `str` | （必填） |
| `credentials` | `Credentials` | （必填） |
| `login_flow` | `list[str] \| None` | `None` |
| `success_condition` | `SuccessCondition` | （必填） |

目标系统的认证配置。

---

#### `PipelineConfig`

文件路径: `packages/core/src/shannon_core/models/config.py`

```python
class PipelineConfig(BaseModel)
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `retry_preset` | `Literal["default", "subscription"] \| None` | `None` |
| `max_concurrent_pipelines` | `int \| None` | `None` |

流水线执行配置。

---

#### `AgentMetrics`

文件路径: `packages/core/src/shannon_core/models/metrics.py`

```python
class AgentMetrics(BaseModel)
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `duration_ms` | `int` | （必填） |
| `input_tokens` | `int \| None` | `None` |
| `output_tokens` | `int \| None` | `None` |
| `cost_usd` | `float \| None` | `None` |
| `num_turns` | `int \| None` | `None` |
| `model` | `str \| None` | `None` |

单个 Agent 的执行度量数据。

---

#### `SessionMetadata`

文件路径: `packages/core/src/shannon_core/models/metrics.py`

```python
class SessionMetadata(BaseModel)
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `id` | `str` | （必填） |
| `web_url` | `str` | （必填） |
| `repo_path` | `str \| None` | `None` |
| `output_path` | `str \| None` | `None` |

允许额外字段（`extra = "allow"`），存储会话元数据。

---

#### `BaseVulnerability`

文件路径: `packages/core/src/shannon_core/models/queue_schemas.py`

```python
class BaseVulnerability(BaseModel)
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `ID` | `str` | （必填） |
| `vulnerability_type` | `str` | （必填） |
| `externally_exploitable` | `bool` | （必填） |
| `confidence` | `str` | （必填） |
| `notes` | `str \| None` | `None` |

所有漏洞模型的基类。

---

#### `InjectionVulnerability`

文件路径: `packages/core/src/shannon_core/models/queue_schemas.py`

```python
class InjectionVulnerability(BaseVulnerability)
```

继承 `BaseVulnerability` 的所有字段，并包含以下额外字段：

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `source` | `str \| None` | `None` |
| `combined_sources` | `str \| None` | `None` |
| `path` | `str \| None` | `None` |
| `sink_call` | `str \| None` | `None` |
| `slot_type` | `str \| None` | `None` |
| `sanitization_observed` | `str \| None` | `None` |
| `concat_occurrences` | `str \| None` | `None` |
| `verdict` | `str \| None` | `None` |
| `mismatch_reason` | `str \| None` | `None` |
| `witness_payload` | `str \| None` | `None` |

注入漏洞模型，记录污点源、汇聚调用、净化情况和判定结果。

---

#### `XssVulnerability`

文件路径: `packages/core/src/shannon_core/models/queue_schemas.py`

```python
class XssVulnerability(BaseVulnerability)
```

继承 `BaseVulnerability` 的所有字段，并包含以下额外字段：

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `source` | `str \| None` | `None` |
| `source_detail` | `str \| None` | `None` |
| `path` | `str \| None` | `None` |
| `sink_function` | `str \| None` | `None` |
| `render_context` | `str \| None` | `None` |
| `encoding_observed` | `str \| None` | `None` |
| `verdict` | `str \| None` | `None` |
| `mismatch_reason` | `str \| None` | `None` |
| `witness_payload` | `str \| None` | `None` |

跨站脚本漏洞模型，记录来源、汇聚函数、渲染上下文和编码情况。

---

#### `AuthVulnerability`

文件路径: `packages/core/src/shannon_core/models/queue_schemas.py`

```python
class AuthVulnerability(BaseVulnerability)
```

继承 `BaseVulnerability` 的所有字段，并包含以下额外字段：

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `source_endpoint` | `str \| None` | `None` |
| `vulnerable_code_location` | `str \| None` | `None` |
| `missing_defense` | `str \| None` | `None` |
| `exploitation_hypothesis` | `str \| None` | `None` |
| `suggested_exploit_technique` | `str \| None` | `None` |

认证漏洞模型，记录源端点、缺失防御和利用假设。

---

#### `SsrfVulnerability`

文件路径: `packages/core/src/shannon_core/models/queue_schemas.py`

```python
class SsrfVulnerability(BaseVulnerability)
```

继承 `BaseVulnerability` 的所有字段，并包含以下额外字段：

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `source_endpoint` | `str \| None` | `None` |
| `vulnerable_parameter` | `str \| None` | `None` |
| `vulnerable_code_location` | `str \| None` | `None` |
| `missing_defense` | `str \| None` | `None` |
| `exploitation_hypothesis` | `str \| None` | `None` |
| `suggested_exploit_technique` | `str \| None` | `None` |

服务端请求伪造漏洞模型，记录易受攻击参数和利用建议。

---

#### `AuthzVulnerability`

文件路径: `packages/core/src/shannon_core/models/queue_schemas.py`

```python
class AuthzVulnerability(BaseVulnerability)
```

继承 `BaseVulnerability` 的所有字段，并包含以下额外字段：

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `endpoint` | `str \| None` | `None` |
| `vulnerable_code_location` | `str \| None` | `None` |
| `role_context` | `str \| None` | `None` |
| `guard_evidence` | `str \| None` | `None` |
| `side_effect` | `str \| None` | `None` |
| `reason` | `str \| None` | `None` |
| `minimal_witness` | `str \| None` | `None` |

授权漏洞模型，记录角色上下文、防护证据和最小见证。

---

#### `VulnerabilityQueue`

文件路径: `packages/core/src/shannon_core/models/queue_schemas.py`

```python
class VulnerabilityQueue(BaseModel)
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `vulnerabilities` | `list[Vulnerability]` | `[]` |

漏洞队列，包含一个 `Vulnerability` 联合类型的列表。`Vulnerability` 定义为：

```python
Vulnerability = Union[InjectionVulnerability, XssVulnerability, AuthVulnerability, SsrfVulnerability, AuthzVulnerability, BaseVulnerability]
```

---

#### `WhiteboxScanResult`

文件路径: `packages/core/src/shannon_core/models/result.py`

```python
class WhiteboxScanResult(BaseModel)
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `status` | `str` | （必填） |
| `completed_agents` | `list[str]` | （必填） |
| `agent_metrics` | `dict[str, AgentMetrics]` | （必填） |
| `error` | `str \| None` | `None` |
| `workspace_path` | `str \| None` | `None` |

白盒扫描结果模型。

---

#### `BlackboxScanResult`

文件路径: `packages/core/src/shannon_core/models/result.py`

```python
class BlackboxScanResult(BaseModel)
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `status` | `str` | （必填） |
| `completed_agents` | `list[str]` | （必填） |
| `agent_metrics` | `dict[str, AgentMetrics]` | （必填） |
| `has_whitebox_results` | `bool` | `False` |
| `error` | `str \| None` | `None` |
| `workspace_path` | `str \| None` | `None` |

黑盒扫描结果模型，额外包含是否存在白盒扫描结果的标记。

---

#### `PentestError`

文件路径: `packages/core/src/shannon_core/models/errors.py`

```python
class PentestError(Exception)
```

**签名：**

```python
def __init__(
    self,
    message: str,
    category: PentestErrorType,
    retryable: bool = False,
    error_code: ErrorCode | None = None,
    context: dict | None = None,
)
```

| 实例属性 | 类型 |
|----------|------|
| `message` | `str` |
| `category` | `PentestErrorType`（即 `str`） |
| `retryable` | `bool` |
| `error_code` | `ErrorCode \| None` |
| `context` | `dict` |

渗透测试统一的异常类，携带错误分类、是否可重试、错误码和上下文信息。

---

### 枚举类型 (Enums)

#### `AgentName`

文件路径: `packages/core/src/shannon_core/models/agents.py`

```python
class AgentName(str, Enum)
```

| 成员 | 值 |
|------|-----|
| `PRE_RECON` | `"pre-recon"` |
| `RECON` | `"recon"` |
| `INJECTION_VULN` | `"injection-vuln"` |
| `XSS_VULN` | `"xss-vuln"` |
| `AUTH_VULN` | `"auth-vuln"` |
| `SSRF_VULN` | `"ssrf-vuln"` |
| `AUTHZ_VULN` | `"authz-vuln"` |
| `RECON_BLACKBOX` | `"recon-blackbox"` |
| `INJECTION_EXPLOIT` | `"injection-exploit"` |
| `XSS_EXPLOIT` | `"xss-exploit"` |
| `AUTH_EXPLOIT` | `"auth-exploit"` |
| `SSRF_EXPLOIT` | `"ssrf-exploit"` |
| `AUTHZ_EXPLOIT` | `"authz-exploit"` |
| `REPORT` | `"report"` |

所有 Agent 的名称枚举，共 14 个成员。

---

#### `DeliverableType`

文件路径: `packages/core/src/shannon_core/models/deliverables.py`

```python
class DeliverableType(str, Enum)
```

| 成员 | 值 |
|------|-----|
| `CODE_ANALYSIS` | `"CODE_ANALYSIS"` |
| `RECON` | `"RECON"` |
| `INJECTION_ANALYSIS` | `"INJECTION_ANALYSIS"` |
| `XSS_ANALYSIS` | `"XSS_ANALYSIS"` |
| `AUTH_ANALYSIS` | `"AUTH_ANALYSIS"` |
| `AUTHZ_ANALYSIS` | `"AUTHZ_ANALYSIS"` |
| `SSRF_ANALYSIS` | `"SSRF_ANALYSIS"` |
| `INJECTION_EVIDENCE` | `"INJECTION_EVIDENCE"` |
| `XSS_EVIDENCE` | `"XSS_EVIDENCE"` |
| `AUTH_EVIDENCE` | `"AUTH_EVIDENCE"` |
| `AUTHZ_EVIDENCE` | `"AUTHZ_EVIDENCE"` |
| `SSRF_EVIDENCE` | `"SSRF_EVIDENCE"` |
| `REPORT` | `"REPORT"` |

可交付成果类型枚举，共 13 个成员。

---

#### `ErrorCode`

文件路径: `packages/core/src/shannon_core/models/errors.py`

```python
class ErrorCode(str, Enum)
```

| 成员 | 值 |
|------|-----|
| `CONFIG_NOT_FOUND` | `"CONFIG_NOT_FOUND"` |
| `CONFIG_VALIDATION_FAILED` | `"CONFIG_VALIDATION_FAILED"` |
| `CONFIG_PARSE_ERROR` | `"CONFIG_PARSE_ERROR"` |
| `AGENT_EXECUTION_FAILED` | `"AGENT_EXECUTION_FAILED"` |
| `OUTPUT_VALIDATION_FAILED` | `"OUTPUT_VALIDATION_FAILED"` |
| `API_RATE_LIMITED` | `"API_RATE_LIMITED"` |
| `SPENDING_CAP_REACHED` | `"SPENDING_CAP_REACHED"` |
| `INSUFFICIENT_CREDITS` | `"INSUFFICIENT_CREDITS"` |
| `GIT_CHECKPOINT_FAILED` | `"GIT_CHECKPOINT_FAILED"` |
| `GIT_ROLLBACK_FAILED` | `"GIT_ROLLBACK_FAILED"` |
| `PROMPT_LOAD_FAILED` | `"PROMPT_LOAD_FAILED"` |
| `DELIVERABLE_NOT_FOUND` | `"DELIVERABLE_NOT_FOUND"` |
| `REPO_NOT_FOUND` | `"REPO_NOT_FOUND"` |
| `TARGET_UNREACHABLE` | `"TARGET_UNREACHABLE"` |
| `AUTH_FAILED` | `"AUTH_FAILED"` |
| `AUTH_LOGIN_FAILED` | `"AUTH_LOGIN_FAILED"` |
| `BILLING_ERROR` | `"BILLING_ERROR"` |

错误码枚举，用于标识各类渗透测试错误。

---

### 类型别名 (Type Aliases)

文件路径: `packages/core/src/shannon_core/models/agents.py`

```python
VulnType = Literal["injection", "xss", "auth", "ssrf", "authz"]
```

文件路径: `packages/core/src/shannon_core/models/config.py`

```python
RuleType = Literal["url_path", "subdomain", "domain", "method", "header", "parameter", "code_path"]
VulnClass = Literal["injection", "xss", "auth", "authz", "ssrf"]
Severity = Literal["low", "medium", "high", "critical"]
Confidence = Literal["low", "medium", "high"]
```

---

### 函数 (Functions)

#### `parse_config`

文件路径: `packages/core/src/shannon_core/config/parser.py`

**签名：**

```python
def parse_config(config_path: str) -> Config
```

**描述：** 从指定路径解析 YAML 配置文件并返回验证后的 `Config` 对象。

**返回值：** `Config`

---

#### `distribute_config`

文件路径: `packages/core/src/shannon_core/config/parser.py`

**签名：**

```python
def distribute_config(config: Config | None) -> DistributedConfig
```

**描述：** 将顶层配置展平为 `DistributedConfig`；若传入 `None` 则使用默认值。

**返回值：** `DistributedConfig`

---

#### `is_spending_cap_behavior`

文件路径: `packages/core/src/shannon_core/utils/billing.py`

**签名：**

```python
def is_spending_cap_behavior(turns: int, cost: float, text: str) -> bool
```

**描述：** 根据 Agent 执行轮次、费用和输出文本判断是否触及消费上限。

**返回值：** `bool`

---

#### `run_with_concurrency_limit`

文件路径: `packages/core/src/shannon_core/utils/concurrency.py`

**签名：**

```python
async def run_with_concurrency_limit(
    coroutines: list[Callable[[], Awaitable[T]]],
    limit: int,
) -> list[T]
```

**描述：** 以指定的并发限制并发执行协程列表，任一协程抛出异常时向上传播。

**返回值：** `list[T]`

---

#### `async_read_file`

文件路径: `packages/core/src/shannon_core/utils/file_io.py`

**签名：**

```python
async def async_read_file(path: str | Path) -> str
```

**描述：** 异步读取文件全部内容并以字符串返回。

**返回值：** `str`

---

#### `async_write_file`

文件路径: `packages/core/src/shannon_core/utils/file_io.py`

**签名：**

```python
async def async_write_file(path: str | Path, content: str) -> None
```

**描述：** 异步写入文件，自动创建父目录。

**返回值：** `None`

---

#### `async_path_exists`

文件路径: `packages/core/src/shannon_core/utils/file_io.py`

**签名：**

```python
async def async_path_exists(path: str | Path) -> bool
```

**描述：** 异步检查路径是否存在。

**返回值：** `bool`

---

#### `async_read_json`

文件路径: `packages/core/src/shannon_core/utils/file_io.py`

**签名：**

```python
async def async_read_json(path: str | Path) -> dict | list
```

**描述：** 异步读取并解析 JSON 文件。

**返回值：** `dict | list`

---

#### `async_write_json`

文件路径: `packages/core/src/shannon_core/utils/file_io.py`

**签名：**

```python
async def async_write_json(path: str | Path, data: dict | list, indent: int = 2) -> None
```

**描述：** 异步将数据序列化为 JSON 并写入文件。

**返回值：** `None`

---

#### `format_timestamp`

文件路径: `packages/core/src/shannon_core/utils/formatting.py`

**签名：**

```python
def format_timestamp() -> str
```

**描述：** 返回当前 UTC 时间的 ISO 8601 格式字符串。

**返回值：** `str`

---

#### `truncate_text`

文件路径: `packages/core/src/shannon_core/utils/formatting.py`

**签名：**

```python
def truncate_text(text: str, max_length: int = 200) -> str
```

**描述：** 截断文本到指定最大长度，超出部分以 `"..."` 结尾。

**返回值：** `str`

---

### 常量 (Constants)

#### `AGENTS`

文件路径: `packages/core/src/shannon_core/models/agents.py`

```python
AGENTS: dict[AgentName, AgentDefinition]
```

所有 Agent 定义的字典，键为 `AgentName` 枚举值，值为对应的 `AgentDefinition` 实例。包含 14 个 Agent 的完整定义。

---

#### `DELIVERABLE_FILENAMES`

文件路径: `packages/core/src/shannon_core/models/deliverables.py`

```python
DELIVERABLE_FILENAMES: dict[DeliverableType, str]
```

可交付成果类型到文件名的映射字典。

| 键 | 值 |
|----|-----|
| `DeliverableType.CODE_ANALYSIS` | `"pre_recon_deliverable.md"` |
| `DeliverableType.RECON` | `"recon_deliverable.md"` |
| `DeliverableType.INJECTION_ANALYSIS` | `"injection_analysis_deliverable.md"` |
| `DeliverableType.XSS_ANALYSIS` | `"xss_analysis_deliverable.md"` |
| `DeliverableType.AUTH_ANALYSIS` | `"auth_analysis_deliverable.md"` |
| `DeliverableType.AUTHZ_ANALYSIS` | `"authz_analysis_deliverable.md"` |
| `DeliverableType.SSRF_ANALYSIS` | `"ssrf_analysis_deliverable.md"` |
| `DeliverableType.INJECTION_EVIDENCE` | `"injection_exploitation_evidence.md"` |
| `DeliverableType.XSS_EVIDENCE` | `"xss_exploitation_evidence.md"` |
| `DeliverableType.AUTH_EVIDENCE` | `"auth_exploitation_evidence.md"` |
| `DeliverableType.AUTHZ_EVIDENCE` | `"authz_exploitation_evidence.md"` |
| `DeliverableType.SSRF_EVIDENCE` | `"ssrf_exploitation_evidence.md"` |
| `DeliverableType.REPORT` | `"comprehensive_security_assessment_report.md"` |

---

#### `ALL_VULN_CLASSES`

文件路径: `packages/core/src/shannon_core/models/config.py`

```python
ALL_VULN_CLASSES: list[VulnClass] = ["injection", "xss", "auth", "authz", "ssrf"]
```

所有支持的漏洞类别列表。

---

## shannon-whitebox

### 类 (Classes)

#### `AgentExecutor`

文件路径: `packages/whitebox/src/shannon_whitebox/agents/executor.py`

**构造器签名：**

```python
def __init__(self, prompt_manager: PromptManager)
```

**描述：** Agent 执行器，负责加载提示、调用 LLM、验证输出和管理 Git 状态。

**`execute` 方法签名：**

```python
async def execute(
    self,
    agent_name: AgentName,
    repo_path: str,
    web_url: str = "",
    deliverables_path: str | None = None,
    config_path: str | None = None,
    api_key: str | None = None,
    pipeline_testing: bool = False,
    prompt_variables: dict[str, str] | None = None,
) -> AgentMetrics
```

**描述：** 执行指定 Agent 的完整流程，包括配置解析、提示加载、Git 检查点创建、LLM 调用、消费上限检测、输出验证和 Git 提交。

**返回值：** `AgentMetrics`

---

#### `ClaudeRunResult`

文件路径: `packages/whitebox/src/shannon_whitebox/agents/runner.py`

```python
@dataclass
class ClaudeRunResult
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `text` | `str` | `""` |
| `success` | `bool` | `False` |
| `duration` | `int` | `0` |
| `turns` | `int` | `0` |
| `cost` | `float` | `0.0` |
| `model` | `str \| None` | `None` |
| `structured_output` | `Any \| None` | `None` |
| `error` | `str \| None` | `None` |
| `retryable` | `bool` | `True` |

LLM 调用的返回结果数据类。

---

#### `PromptManager`

文件路径: `packages/whitebox/src/shannon_whitebox/prompts/manager.py`

**构造器签名：**

```python
def __init__(self, prompts_dir: Path)
```

**描述：** 提示模板管理器，负责加载模板、处理 `@include` 指令和变量插值。

**`load_sync` 方法签名：**

```python
def load_sync(
    self,
    template_name: str,
    variables: dict[str, str],
    config: DistributedConfig | None = None,
    pipeline_testing: bool = False,
) -> str
```

**描述：** 同步加载提示模板，处理文件包含和配置变量插值，返回完整的提示字符串。

**返回值：** `str`

---

#### `SessionManager`

文件路径: `packages/whitebox/src/shannon_whitebox/session.py`

**构造器签名：**

```python
def __init__(self, workspaces_dir: Path)
```

**描述：** 工作空间管理器，负责创建、列举和查询工作空间及其会话数据。

**方法：**

```python
def create_workspace(self, web_url: str, repo_path: str, name: str | None = None) -> Path
```

创建新的工作空间目录并初始化 `session.json`，返回工作空间路径。

```python
def list_workspaces(self) -> list[Path]
```

列出所有包含 `session.json` 的工作空间，按修改时间倒序排列。

```python
def get_workspace(self, name: str) -> Path | None
```

按名称查找工作空间，返回路径或 `None`。

```python
def get_session_data(self, workspace_path: Path) -> dict
```

读取并解析工作空间的 `session.json`。

```python
def update_session(self, workspace_path: Path, data: dict) -> None
```

合并更新工作空间的 `session.json`。

```python
def mark_agent_completed(self, workspace_path: Path, agent_name: AgentName) -> None
```

在会话数据中标记指定 Agent 为已完成。

```python
def is_agent_completed(self, workspace_path: Path, agent_name: AgentName) -> bool
```

检查指定 Agent 是否已在该工作空间中完成。

---

#### `GitManager`

文件路径: `packages/whitebox/src/shannon_whitebox/git_manager.py`

全部为静态方法，用于管理 Agent 执行过程中的 Git 状态。

**`create_checkpoint` 签名：**

```python
@staticmethod
def create_checkpoint(repo_path: Path, agent_name: str | AgentName, attempt: int = 1) -> None
```

**描述：** 在 Agent 执行前创建 Git 检查点提交。

**返回值：** `None`

**`commit` 签名：**

```python
@staticmethod
def commit(repo_path: Path, agent_name: str | AgentName) -> None
```

**描述：** 提交 Agent 的可交付成果。

**返回值：** `None`

**`rollback` 签名：**

```python
@staticmethod
def rollback(repo_path: Path, reason: str) -> None
```

**描述：** 通过 `git reset --hard HEAD` 和 `git clean -fd` 回滚到上一个检查点。

**返回值：** `None`

**`get_commit_hash` 签名：**

```python
@staticmethod
def get_commit_hash(repo_path: Path) -> str | None
```

**描述：** 获取当前 HEAD 的提交哈希值。

**返回值：** `str | None`

---

#### `AuditSession`

文件路径: `packages/whitebox/src/shannon_whitebox/audit/session.py`

**构造器签名：**

```python
def __init__(self, workspace_path: Path)
```

**描述：** 审计会话管理器，负责记录工作流日志和 Agent 执行日志。

**方法：**

```python
async def log(self, message: str) -> None
```

向工作流日志追加一条消息。

```python
async def log_phase(self, phase: str, status: str) -> None
```

记录阶段状态变更。

```python
async def start_agent(self, agent_name: AgentName, prompt: str, attempt: int = 1) -> None
```

记录 Agent 开始执行并保存使用的提示文本。

```python
async def end_agent(self, agent_name: AgentName, success: bool, metrics: AgentMetrics | None = None) -> None
```

记录 Agent 执行结束状态和度量数据。

```python
async def save_session(self, session_data: dict) -> None
```

保存完整的会话数据到 `session.json`。

---

#### `LogStream`

文件路径: `packages/whitebox/src/shannon_whitebox/audit/log_stream.py`

**构造器签名：**

```python
def __init__(self, file_path: Path)
```

**描述：** 带时间戳的日志流，支持异步追加。

**方法：**

```python
async def append(self, line: str) -> None
```

追加一行带 UTC 时间戳的日志。

```python
async def append_lines(self, lines: list[str]) -> None
```

追加多行日志。

---

#### `WhiteboxScanWorkflow`

文件路径: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`

```python
@workflow.defn
class WhiteboxScanWorkflow
```

**`run` 方法签名：**

```python
@workflow.run
async def run(self, input: PipelineInput) -> PipelineState
```

**描述：** Temporal 工作流定义，按顺序执行预检、预侦察、侦察，然后并行执行漏洞分析 Agent。

**返回值：** `PipelineState`

---

#### `PipelineInput`

文件路径: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`

```python
@dataclass
class PipelineInput
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `repo_path` | `str` | （必填） |
| `web_url` | `str` | `""` |
| `config_path` | `str \| None` | `None` |
| `output_path` | `str \| None` | `None` |
| `workspace_name` | `str \| None` | `None` |
| `resume_from_workspace` | `str \| None` | `None` |
| `vuln_classes` | `list[VulnType] \| None` | `None` |
| `pipeline_testing_mode` | `bool` | `False` |
| `api_key` | `str \| None` | `None` |
| `deliverables_subdir` | `str` | `".shannon/deliverables"` |

白盒扫描流水线的输入数据。

---

#### `PipelineState`

文件路径: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`

```python
@dataclass
class PipelineState
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `status` | `str` | `"running"` |
| `completed_agents` | `list[str]` | `field(default_factory=list)` |
| `agent_metrics` | `dict[str, dict]` | `field(default_factory=dict)` |
| `start_time` | `float` | `0.0` |
| `error` | `str \| None` | `None` |

白盒扫描流水线的运行状态。

---

#### `ActivityInput`

文件路径: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`

```python
@dataclass
class ActivityInput
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `repo_path` | `str` | （必填） |
| `web_url` | `str` | `""` |
| `config_path` | `str \| None` | `None` |
| `workspace_name` | `str \| None` | `None` |
| `deliverables_subdir` | `str` | `".shannon/deliverables"` |
| `pipeline_testing_mode` | `bool` | `False` |
| `api_key` | `str \| None` | `None` |

Temporal Activity 的输入数据。

---

### 函数 (Functions)

#### `run_claude_prompt`

文件路径: `packages/whitebox/src/shannon_whitebox/agents/runner.py`

**签名：**

```python
async def run_claude_prompt(
    prompt: str,
    repo_path: str,
    model_tier: str = "medium",
    output_format: dict | None = None,
    api_key: str | None = None,
    deliverables_subdir: str | None = None,
    provider_config: dict | None = None,
) -> ClaudeRunResult
```

**描述：** 调用 Claude Agent SDK 执行提示（当前为桩实现，尚未集成 SDK）。

**返回值：** `ClaudeRunResult`

---

#### `validate_deliverable`

文件路径: `packages/whitebox/src/shannon_whitebox/agents/validators.py`

**签名：**

```python
async def validate_deliverable(deliverables_path: Path, agent_name: AgentName) -> bool
```

**描述：** 验证指定 Agent 的可交付成果文件是否存在，不存在则抛出 `PentestError`。

**返回值：** `bool`

---

#### `get_vuln_type`

文件路径: `packages/whitebox/src/shannon_whitebox/agents/validators.py`

**签名：**

```python
def get_vuln_type(agent_name: AgentName) -> str | None
```

**描述：** 从 Agent 名称中提取漏洞类型字符串（如 `"injection"`），不匹配则返回 `None`。

**返回值：** `str | None`

---

#### `get_queue_filename`

文件路径: `packages/whitebox/src/shannon_whitebox/agents/validators.py`

**签名：**

```python
def get_queue_filename(agent_name: AgentName) -> str | None
```

**描述：** 返回与漏洞类型对应的利用队列文件名（如 `"injection_exploitation_queue.json"`）。

**返回值：** `str | None`

---

#### `run_preflight`

文件路径: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`

**签名：**

```python
@activity.defn
async def run_preflight(input: ActivityInput) -> None
```

**描述：** Temporal Activity，验证目标仓库是否存在且为 Git 仓库。

**返回值：** `None`

---

#### `run_agent`

文件路径: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`

**签名：**

```python
@activity.defn
async def run_agent(input: ActivityInput) -> dict
```

**描述：** Temporal Activity，根据 `ActivityInput` 中的 `workspace_name` 确定要执行的 Agent 并运行。

**返回值：** `dict`（`AgentMetrics` 序列化后的字典）

---

#### `run_vuln_agent`

文件路径: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`

**签名：**

```python
@activity.defn
async def run_vuln_agent(input: ActivityInput) -> dict
```

**描述：** Temporal Activity，委托给 `run_agent` 执行漏洞分析 Agent。

**返回值：** `dict`

---

#### `run_scan`

文件路径: `packages/whitebox/src/shannon_whitebox/worker.py`

**签名：**

```python
async def run_scan(input: PipelineInput, temporal_address: str = "localhost:7233") -> dict
```

**描述：** 启动 Temporal Worker 并执行白盒扫描工作流，返回工作流结果。

**返回值：** `dict`

---

#### `main`

文件路径: `packages/whitebox/src/shannon_whitebox/worker.py`

**签名：**

```python
def main() -> None
```

**描述：** 命令行入口，以可选的仓库路径参数启动扫描。

---

### CLI 命令

文件路径: `packages/whitebox/src/shannon_whitebox/cli/main.py`

#### `cli`

```python
@click.group()
def cli()
```

Shannon 白盒扫描器的 CLI 根命令组。

---

#### `start`

```python
@cli.command()
@click.option("-r", "--repo", required=True, help="Target repository path")
@click.option("-o", "--output", default=None, help="Output directory for deliverables")
@click.option("-w", "--workspace", default=None, help="Workspace name (supports resume)")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
def start(repo, output, workspace, config_path, pipeline_testing, temporal_address)
```

启动白盒安全扫描。

---

#### `logs`

```python
@cli.command()
@click.argument("workspace_name")
def logs(workspace_name)
```

查看指定工作空间的执行日志。

---

#### `workspaces`

```python
@cli.command()
def workspaces()
```

列出所有工作空间及其概要信息。

---

## shannon-blackbox

### 类 (Classes)

#### `ReconExecutor`

文件路径: `packages/blackbox/src/shannon_blackbox/agents/recon_executor.py`

**构造器签名：**

```python
def __init__(self, agent_executor: AgentExecutor)
```

**描述：** 黑盒侦察执行器，封装对 `RECON_BLACKBOX` Agent 的调用。

**`execute` 方法签名：**

```python
async def execute(
    self,
    workspace_path: Path,
    deliverables_path: Path,
    web_url: str,
    config_path: str | None = None,
    api_key: str | None = None,
    pipeline_testing: bool = False,
) -> AgentMetrics
```

**描述：** 执行黑盒侦察 Agent。

**返回值：** `AgentMetrics`

---

#### `ExploitExecutor`

文件路径: `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`

**构造器签名：**

```python
def __init__(self, agent_executor: AgentExecutor)
```

**描述：** 黑盒利用执行器，加载漏洞利用队列后调用对应的利用 Agent。

**`execute` 方法签名：**

```python
async def execute(
    self,
    agent_name: AgentName,
    vuln_type: str,
    workspace_path: Path,
    deliverables_path: Path,
    web_url: str,
    config_path: str | None = None,
    api_key: str | None = None,
    pipeline_testing: bool = False,
) -> AgentMetrics
```

**描述：** 执行指定漏洞类型的利用 Agent，自动加载对应的利用队列作为提示变量。

**返回值：** `AgentMetrics`

---

#### `ReportAssembler`

文件路径: `packages/blackbox/src/shannon_blackbox/services/report_assembler.py`

**`assemble` 静态方法签名：**

```python
@staticmethod
async def assemble(
    deliverables_path: Path,
    vuln_classes: list[str],
    report_path: Path,
) -> None
```

**描述：** 按漏洞类别聚合利用证据或发现内容，拼接为最终报告。

**返回值：** `None`

---

#### `ExploitationChecker`

文件路径: `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py`

**`should_exploit` 静态方法签名：**

```python
@staticmethod
async def should_exploit(
    deliverables_path: Path,
    vuln_type: str,
    exploit_enabled: bool = True,
) -> bool
```

**描述：** 检查指定漏洞类型是否需要执行利用阶段，依据是利用队列中是否存在漏洞条目。

**返回值：** `bool`

---

#### `BlackboxScanWorkflow`

文件路径: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`

```python
@workflow.defn
class BlackboxScanWorkflow
```

**`run` 方法签名：**

```python
@workflow.run
async def run(self, input: BlackboxPipelineInput) -> BlackboxPipelineState
```

**描述：** Temporal 工作流定义，执行预检、侦察（若无白盒结果）、并行利用、报告汇编和报告生成。

**返回值：** `BlackboxPipelineState`

---

#### `BlackboxPipelineInput`

文件路径: `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`

```python
@dataclass
class BlackboxPipelineInput
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `web_url` | `str` | （必填） |
| `workspace_name` | `str \| None` | `None` |
| `config_path` | `str \| None` | `None` |
| `output_path` | `str \| None` | `None` |
| `repo_path` | `str \| None` | `None` |
| `resume_from_workspace` | `str \| None` | `None` |
| `vuln_classes` | `list[str] \| None` | `None` |
| `exploit` | `bool` | `True` |
| `pipeline_testing_mode` | `bool` | `False` |
| `api_key` | `str \| None` | `None` |
| `deliverables_subdir` | `str` | `".shannon/deliverables"` |

黑盒扫描流水线的输入数据。

---

#### `BlackboxPipelineState`

文件路径: `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`

```python
@dataclass
class BlackboxPipelineState
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `status` | `str` | `"running"` |
| `current_phase` | `str \| None` | `None` |
| `completed_agents` | `list[str]` | `field(default_factory=list)` |
| `agent_metrics` | `dict[str, dict]` | `field(default_factory=dict)` |
| `has_whitebox_results` | `bool` | `False` |
| `start_time` | `float` | `0.0` |
| `error` | `str \| None` | `None` |

黑盒扫描流水线的运行状态。

---

#### `BlackboxActivityInput`

文件路径: `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`

```python
@dataclass
class BlackboxActivityInput
```

| 字段 | 类型 | 默认值 |
|------|------|--------|
| `web_url` | `str` | （必填） |
| `repo_path` | `str \| None` | `None` |
| `config_path` | `str \| None` | `None` |
| `workspace_name` | `str \| None` | `None` |
| `deliverables_subdir` | `str` | `".shannon/deliverables"` |
| `pipeline_testing_mode` | `bool` | `False` |
| `api_key` | `str \| None` | `None` |
| `agent_name` | `str \| None` | `None` |
| `vuln_type` | `str \| None` | `None` |

黑盒 Temporal Activity 的输入数据。

---

### 函数 (Functions)

#### `run_blackbox_preflight`

文件路径: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`

**签名：**

```python
@activity.defn
async def run_blackbox_preflight(input: BlackboxActivityInput) -> None
```

**描述：** Temporal Activity，黑盒扫描的预检步骤（当前为空实现）。

**返回值：** `None`

---

#### `run_recon`

文件路径: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`

**签名：**

```python
@activity.defn
async def run_recon(input: BlackboxActivityInput) -> dict
```

**描述：** Temporal Activity，创建并执行黑盒侦察 Agent。

**返回值：** `dict`（`AgentMetrics` 序列化后的字典）

---

#### `run_exploit_agent`

文件路径: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`

**签名：**

```python
@activity.defn
async def run_exploit_agent(input: BlackboxActivityInput) -> dict
```

**描述：** Temporal Activity，根据输入中的 `vuln_type` 和 `agent_name` 执行对应的利用 Agent。

**返回值：** `dict`

---

#### `assemble_report`

文件路径: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`

**签名：**

```python
@activity.defn
async def assemble_report(input: BlackboxActivityInput) -> None
```

**描述：** Temporal Activity，调用 `ReportAssembler.assemble` 汇总所有漏洞类别的报告。

**返回值：** `None`

---

#### `run_report_agent`

文件路径: `packages/blackbox/src/shannon_blackbox/pipeline/activities.py`

**签名：**

```python
@activity.defn
async def run_report_agent(input: BlackboxActivityInput) -> dict
```

**描述：** Temporal Activity，执行报告生成 Agent。

**返回值：** `dict`

---

#### `run_scan`

文件路径: `packages/blackbox/src/shannon_blackbox/worker.py`

**签名：**

```python
async def run_scan(input: BlackboxPipelineInput, temporal_address: str = "localhost:7233") -> dict
```

**描述：** 启动 Temporal Worker 并执行黑盒扫描工作流，返回工作流结果。

**返回值：** `dict`

---

#### `main`

文件路径: `packages/blackbox/src/shannon_blackbox/worker.py`

**签名：**

```python
def main() -> None
```

**描述：** 命令行入口，以可选的目标 URL 参数启动黑盒扫描。

---

### CLI 命令

文件路径: `packages/blackbox/src/shannon_blackbox/cli/main.py`

#### `cli`

```python
@click.group()
def cli()
```

Shannon 黑盒扫描器的 CLI 根命令组。

---

#### `start`

```python
@cli.command()
@click.option("--url", required=True, help="Target URL to scan")
@click.option("-o", "--output", default=None, help="Output directory for deliverables")
@click.option("-w", "--workspace", default=None, help="Workspace name (resume if exists)")
@click.option("-c", "--config", "config_path", default=None, help="YAML configuration file")
@click.option("--vuln-classes", multiple=True, help="Vuln classes to test (default: all)")
@click.option("--no-exploit", is_flag=True, help="Skip exploitation phase")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts for testing")
@click.option("--temporal-address", default="localhost:7233", help="Temporal server address")
def start(url, output, workspace, config_path, vuln_classes, no_exploit, pipeline_testing, temporal_address)
```

启动黑盒安全扫描。

---

#### `logs`

```python
@cli.command()
@click.argument("workspace_name")
def logs(workspace_name)
```

查看指定工作空间的执行日志。

---

#### `workspaces`

```python
@cli.command()
def workspaces()
```

列出所有工作空间及其概要信息。
