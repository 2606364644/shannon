# Spec:报告渲染崩溃修复(queue 格式契约对齐)

- **日期**:2026-06-22
- **状态**:Draft(待评审)
- **触发**:NodeGoat 白盒扫描在 reporting 阶段 `render-findings` 崩溃,54m/$21.95 的扫描在最后一步全废
- **范围**:`shannon-core` / `shannon-whitebox` / `shannon-blackbox` 三包
- **交付**:分两阶段(L1+L0.5+L3 立即止血 → L2+prompt 冒烟后治本)

---

## 1. Context(为什么改)

白盒扫描的全部价值(5 个 vuln agent 53 分钟的漏洞分析)在**最后一步** —— 报告渲染 —— 被一个格式问题清零。这不是偶发:任何 vuln agent "自作主张"手写一个格式不符的 standalone queue JSON,都会让 `render-findings` activity 抛 `ValidationError`,而该异常被 Temporal 误判为可重试,重试 4 次数据不变全部失败,workflow 整体失败。

**目标**:
1. **止血**(L1+L0.5):queue 文件无论什么格式(裸 list / 坏 JSON / 字段漂移),报告都能渲染出来;确定性的数据错误不再触发无意义重试。
2. **治本**(L2):兑现 vuln prompt 早已承诺的"queue 在 session 末尾自动捕获",让 harness 写出规范格式,从源头杜绝 agent 手写格式漂移。
3. **防回归**(L3):单元测试锁定容错行为与 schema 契约。

---

## 2. 根因分析(带证据)

### 2.1 直接报错点
`packages/core/src/shannon_core/services/findings_renderer.py:214`
```python
queue = VulnerabilityQueue.model_validate_json(content)   # 严格解析
```
`VulnerabilityQueue`(`queue_schemas.py:61-62`)期望顶层 object:
```python
class VulnerabilityQueue(BaseModel):
    vulnerabilities: list[Vulnerability] = []
```
NodeGoat 的 `auth_exploitation_queue.json` 顶层是**裸 list** `[{...}×9]` → pydantic `ValidationError: Input should be an object`。

### 2.2 为什么文件是 list(根因核心)
- harness 正规 capture 在 `executor.py:106-109`:仅当 `result.structured_output is not None` 才写 queue 文件。
- 但 whitebox 的 `run_agent`(`activities.py:91-101`)调 `executor.execute(...)` 时**未传 `structured_output_schema`** → vuln agent 的 `structured_output` 恒为 `None` → harness **从不为 vuln agent 写 queue 文件**。
- 而 5 个 vuln prompt(`prompts/vuln-*.txt`)**全都**写着 *"The exploitation queue is captured automatically at the end of your session."* —— 这是**设计意图与实现的脱节**:prompt 承诺自动捕获,代码却没接上。
- [Auth] agent 因此"好心"用 Write 工具补写了一个 standalone JSON,凭自己理解用了裸 list 格式。其余 4 个 agent 把 queue 嵌进 markdown、未写 standalone 文件,所以 deliverables 里只有 auth 一个 queue 文件。

### 2.3 跨 workspace 佐证
| workspace | auth queue 顶层 |
|---|---|
| host-docker ✅ | `{"vulnerabilities":[...]}` |
| juice-shop ✅ | `{"vulnerabilities":[...]}` |
| **NodeGoat ❌** | **裸 list** |

(前者由 blackbox 阶段 / 更早流程写入规范格式。)

### 2.4 放大效应(为什么 54 分钟全废)
1. `ValidationError` 非 `PentestError` → 走 `classify_error_for_temporal`(`errors.py:94`)Level 2 字符串匹配,不含任何 NON_RETRYABLE 关键词 → 落**默认 `("TransientError", True)`**(`errors.py:179`)。
2. Temporal 重试同一 activity 4 次,输入数据不变 → 确定性地同样失败。
3. `render_findings_from_queues` 遍历 5 个 vuln class **无单 class 隔离** → 一个坏 queue 让整个渲染抛出 → 报告阶段失败 → 前面 53 分钟 deliverable 无法渲染成最终报告。

### 2.5 三层防御缺失
| 层 | 现状 |
|---|---|
| 写入层 | vuln agent 无 schema 约束、harness 不 capture,queue 文件格式靠 agent 自觉 → 不可控 |
| 读取层 | renderer + blackbox 两处(`coverage_renderer.py:107`、`exploitation_checker.py:208`)是**裸 pydantic**,遇 list 直接崩;其余 6 处读取已用宽容 `data.get("vulnerabilities", [])` |
| 错误分类层 | 确定性格式错被误判 retryable → 4 次无意义重试 |

> 注:`exploitation_checker.validate_queue`(`exploitation_checker.py:65-136`)已有完善的 4 级容错(返回 `QueueValidationResult`),但 `check_coverage`(line 208)与 `coverage_renderer`(line 107)在 validate 之后又调裸 `model_validate_json` —— **容错逻辑与崩溃点脱节**。本 Spec 的 L1 helper 本质是把 `validate_queue` 的解析思路下沉到 `VulnerabilityQueue` 自身,让所有消费者共享。

---

## 3. 修复设计概览

分四层,按风险/收益分两阶段交付:

| 层 | 内容 | 风险 | 阶段 |
|---|---|---|---|
| **L0.5** | `errors.py`:确定性格式错 → non-retryable(3 行) | 极低 | 阶段 1 |
| **L1** | `VulnerabilityQueue.parse_lenient` 容错解析 + 三处消费者替换 + 单 class/单 vuln 隔离 | 低(纯防御) | 阶段 1 |
| **L3** | 单元测试锁定 L1/L0.5/L2 行为 | 低 | 阶段 1 + 2 |
| **L2** | `VULN_QUEUE_SCHEMA` + `run_agent` 传 schema + 5×2 prompt 改动 | 中(改 vuln agent 输出行为) | 阶段 2(需冒烟) |

**L1 不可省**:即便 L2 落地,agent 仍可能不服从 prompt 而手写覆盖;且历史 workspace 的坏 queue 文件仍在磁盘。L1 是永久防线。

---

## 4. 详细设计

### 4.1 L0.5 — 错误分类根治重试放大

`packages/core/src/shannon_core/models/errors.py` 的 `classify_error_for_temporal` Level 2(在 default 之前)加:

```python
# 确定性的数据/格式错误:重试不改变输入,重试纯浪费
if "validation error" in text or "input should be" in text:
    return ("OutputValidationError", False)
```

- 复用已有 non-retryable 类型 `OutputValidationError`(`errors.py:126`)。
- 与 L1 正交:即便 L1 完整无逃逸,任何其它路径的同类格式错也不会再触发 4 次重试。

### 4.2 L1 — 容错解析 + 隔离

#### 4.2.1 `queue_schemas.py`:新增 lenient 解析

放 `VulnerabilityQueue` 上(三处消费者已 import 该模型,零新依赖;模型层单向依赖 services 层,无循环):

```python
from dataclasses import dataclass

@dataclass
class LenientParseResult:
    queue: "VulnerabilityQueue"
    warnings: list[str]        # e.g. ["wrapped bare-list form (9 entries)", "dropped 1 malformed entry"]
    original_form: str         # "object" | "bare_list" | "object_no_key" | "invalid_json"

class VulnerabilityQueue(BaseModel):
    vulnerabilities: list[Vulnerability] = []

    @classmethod
    def parse_lenient(cls, content: str) -> LenientParseResult:
        """容错解析,消化历史/手写多形态。永不抛异常。"""
        # 形态判定 → 归一化为 dict → 逐条 model_validate(单条失败跳过+记录)
```

**支持的形态**:
| 输入 | 行为 | warning |
|---|---|---|
| `{"vulnerabilities":[...]}` | 正常解析 | 无 |
| **裸 list `[...]`** | 包装成 `{"vulnerabilities": list}` | `"wrapped bare-list form (N entries)"` |
| object 但无 `vulnerabilities` key | 空 queue | `"object has no 'vulnerabilities' key"` |
| 非法 JSON | 空 queue | `"invalid json: <原因>"` |
| 单条 vuln 字段不符 schema | 该条跳过,其余保留 | `"dropped N malformed entr(y/ies)"` |

**设计原则:不静默**。`warnings` 非空时调用方必须落地可见(renderer 写进 findings.md 顶部;blackbox 用 `logger.warning`)。

#### 4.2.2 三处消费者替换

| 文件:行 | 现状 | 改为 |
|---|---|---|
| `findings_renderer.py:213-214` | 裸 `model_validate_json` | `parse_lenient` + warning 落 findings.md |
| `coverage_renderer.py:107` | 裸 | `parse_lenient` + `logger.warning` |
| `exploitation_checker.py:208` | 裸 | `parse_lenient` + `logger.warning` |

> `exploitation_checker.validate_queue`(line 121)已是 lenient 范本,**不改**。

#### 4.2.3 单 class / 单 vuln 隔离(`findings_renderer.py`)

外层 for 循环(5 个 class)已天然隔离;补两层内层防御:
- 每个 class 的解析包 try(实际 `parse_lenient` 已不抛,此层为 belt-and-suspenders)。
- **`render_entry(vuln)` 单条隔离**:某条目渲染崩 → 该条写占位说明,不影响其它条目。

#### 4.2.4 坏 class 的文案:写"解析失败"而非"none_found"

`none_found_label`("No authentication vulnerabilities found.")是**语义陈述**(没找到漏洞);解析失败是**元数据问题**(可能找到但读不出)。混为一谈会让用户误判。坏 class 渲染为:

> ⚠️ Authentication queue auto-recovered from bare-list form (9 entries; 2 dropped due to schema mismatch). Raw queue preserved at `auth_exploitation_queue.json`. Verify data integrity.

整文件不可恢复时:

> ⚠️ Authentication queue unparsable; findings unavailable for this class. See logs.

### 4.3 L2 — vuln agent 正规 structured output 捕获

#### 4.3.1 `queue_schemas.py`:新增 schema(单一宽松形态)

```python
VULN_QUEUE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "vulnerabilities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ID": {"type": "string"},
                    "vulnerability_type": {"type": "string"},
                    "externally_exploitable": {"type": "boolean"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "notes": {"type": ["string", "null"]},
                },
                "required": ["ID", "vulnerability_type", "externally_exploitable", "confidence"],
                "additionalProperties": True,   # 容纳各 class 差异字段
            },
        }
    },
    "required": ["vulnerabilities"],   # 强制 object,杜绝裸 list
}
```

**为何宽松不 union**:
- provider-agnostic(union/discriminator 在 anthropic/openai 支持度未验证,宽松 object 是最稳交集)。
- 5 个 vuln class 共享 `BaseVulnerability` 必填字段,差异字段皆 optional,`additionalProperties` 可兜底。
- 边角(agent 填 `confidence: "High"` 大写等)由 L1 `parse_lenient` 单条跳过兜底,不需 schema 强制。

**一致性**:`VULN_QUEUE_SCHEMA`(前置约束)与 `parse_lenient`(后置兜底)描述同一契约,字段定义从 `BaseVulnerability` 派生共享,避免漂移。

#### 4.3.2 `activities.py:91-101`:`run_agent` 传 schema

```python
from shannon_core.agents.validators import get_vuln_type
from shannon_core.models.queue_schemas import VULN_QUEUE_SCHEMA

schema = VULN_QUEUE_SCHEMA if get_vuln_type(agent_name) else None
metrics = await executor.execute(
    ...,
    structured_output_schema=schema,
)
```

- `get_vuln_type` 已识别 `*-vuln`(whitebox)与 `*-exploit`(blackbox)两类 agent name。
- **blackbox 的 `*-exploit` agent 通过 `prompt_variables["vulnerability_entries"]` 回灌 queue,不产 queue 文件** → 即便门控命中也不传 schema(或传了也无副作用,因 blackbox exploit 不写 queue)。实现时对 `*-exploit` 显式不传,语义更清晰。
- `executor.execute`(已支持 `structured_output_schema` 参数,`executor.py:37,82`)、provider(anthropic `options.output_format` `providers_anthropic.py:241`;openai `build_agent(output_format)` `providers_openai.py:74`)均无需改。

#### 4.3.3 prompt 改动(5 生产 + 5 pipeline-testing)

现有"captured automatically"会误导 agent 认为无需主动输出 → `structured_output` 空 → 静默丢数据。改为明确"经 structured output 提供 + 禁止手写":

> **现**:Write your deliverable markdown via the Write tool first. The exploitation queue is captured automatically at the end of your session.
>
> **改**:Write your deliverable markdown via the Write tool first. **Then provide your exploitation queue as the structured output of your final turn (it is captured automatically from your structured output — do NOT write the queue JSON file manually).**

涉及文件:
- 生产:`prompts/vuln-{auth,authz,injection,ssrf,xss}.txt`
- pipeline-testing:`prompts/pipeline-testing/vuln-*.txt`(同步改,避免 CI/生产行为分叉)

#### 4.3.4 L2 的静默丢数据监控(伴生)

若 agent 最后一轮未给有效 JSON → `structured_output=None` → executor 不写 queue → renderer 跳过 → 该 class 静默无 queue。补监控:
- `executor.py` 写 queue 成功后 `log.info`。
- `run_agent` 收到 `metrics.structured_output is None` 且是 vuln agent 时 `log.warning`(区分"agent 判定无漏洞" vs "queue 没生成")。

### 4.4 L3 — 测试(TDD,只跑相关子集)

> 项目全量 pytest 会 hang 于 Temporal/网络测试(memory:`pytest-whitebox-hang`)。**只跑改动相关子集**。

#### 阶段 1 测试
- `packages/core/tests/test_queue_schemas.py`:加 `test_parse_lenient_*`(bare_list 包装 / object 正常 / 坏 JSON / object 无 key / 单条 schema 拒绝但 queue 存活 / warnings 非空)。
- `packages/core/tests/test_findings_renderer.py`:加 `test_render_recovers_bare_list_queue`(NodeGoat 场景复现)/ `test_render_isolates_bad_class` / `test_render_bad_class_writes_warning_not_nonefound` / `test_render_entry_isolation`。
- `packages/core/tests/test_errors.py`(若无则新增 L0.5 用例):`ValidationError` 文本 → non-retryable。
- `packages/blackbox/tests/`:`coverage_renderer` / `exploitation_checker` 加 bare_list 场景。

#### 阶段 2 测试
- `test_queue_schemas.py`:`test_vuln_queue_schema_structure`(顶层 object / required vulnerabilities / items required 字段 / additionalProperties)。
- `packages/whitebox/tests/` 新增 `test_run_agent_schema.py`:mock `AgentExecutor.execute`,断言 `run_agent` 对 `*-vuln` 传 schema、对 `recon`/`pre-recon`/`report` 传 None。

---

## 5. 关键文件清单

| 文件 | 改动 | 层 |
|---|---|---|
| `packages/core/src/shannon_core/models/queue_schemas.py` | 加 `LenientParseResult` + `parse_lenient` + `VULN_QUEUE_SCHEMA` | L1+L2 |
| `packages/core/src/shannon_core/services/findings_renderer.py` | line 213-214 替换 + 单 vuln 隔离 + warning 文案 | L1 |
| `packages/core/src/shannon_core/models/errors.py` | Level 2 加 `ValidationError → non-retryable` | L0.5 |
| `packages/blackbox/src/shannon_blackbox/services/coverage_renderer.py` | line 107 替换 | L1 |
| `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py` | line 208 替换 | L1 |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | `run_agent` line 91-101 传 schema + None 监控 | L2 |
| `packages/core/src/shannon_core/agents/executor.py` | queue 写入 log.info(伴生监控) | L2 |
| `prompts/vuln-{auth,authz,injection,ssrf,xss}.txt` | "captured automatically" 一句改写 | L2 |
| `prompts/pipeline-testing/vuln-*.txt` | 同步改写 | L2 |

**不改**(已自带容错):`workspace.py:67,124`、`paths.py:101`(`has_valid_whitebox_results`)、`*/cli/main.py`。

---

## 6. 验证方法

### 6.1 单元测试(阶段 1 完成后)
```bash
python -m pytest packages/core/tests/test_queue_schemas.py \
  packages/core/tests/test_findings_renderer.py \
  packages/core/tests/test_errors.py \
  packages/blackbox/tests/test_coverage_renderer.py \
  packages/blackbox/tests/test_exploitation_checker.py -v
```

### 6.2 端到端复现验证(阶段 1)
直接对 NodeGoat 的坏 queue 文件重跑 render(无需重扫):
```bash
# 用现有 NodeGoat deliverables 重跑 render_findings
# 预期:auth queue 自动从 bare-list 恢复渲染 9 条(或扣除坏条目),其余 class 正常,不再崩
```

### 6.3 L2 冒烟(阶段 2)
手动跑 1 个 vuln agent(如 `vuln-auth` on NodeGoat),验证:
- queue 文件由 harness(`executor.py:109`)写出,格式为 `{"vulnerabilities":[...]}` object。
- agent 未手写 standalone queue JSON(prompt 服从)。
- anthropic 主路径 + openai smoke 两个 provider 都验证(openai 经 `json.loads(text)` 解析,对 queue 包在 markdown code block 敏感)。

---

## 7. 风险与权衡

| 风险 | 缓解 |
|---|---|
| L1 list 包装掩盖真实数据问题 | `LenientParseResult.warnings` 非空必落地(findings.md 顶部 / logger) |
| L2 改变 5 个 vuln agent 输出行为 | 隔离到阶段 2,5 个 class 各冒烟;L1 作为永久防线兜底 |
| structured output + agentic tool-use 最后一轮约束 | prompt 明确"先 Write markdown,最后轮输出 queue JSON";None 监控区分丢数据 |
| openai provider 对 queue 包 markdown code block 敏感 | L2 冒烟覆盖 openai;L1 `parse_lenient` 兜底 |
| schema 与 parse_lenient 契约漂移 | 字段定义从 `BaseVulnerability` 派生共享 |

---

## 8. 交付计划

- **阶段 1(立即止血)**:L0.5 + L1 + L3(阶段1测试)。低风险纯防御,合入后 NodeGoat 式崩溃不再发生,坏 queue 可恢复渲染。
- **阶段 2(治本,冒烟后)**:L2 + L3(阶段2测试)+ prompt。改变 vuln agent 输出契约,需 5 个 class 人工冒烟验证后再合。

两阶段独立 commit/PR,阶段 2 不阻塞阶段 1 的止血价值。
