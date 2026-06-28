# 重构安全效果回归修复设计

> 日期：2026-06-02
> 状态：待实施
> 范围：白盒 prompt 条件分支恢复 + 黑盒 exploit-auth 内容补全 + 黑盒 queue 验证激活

---

## 背景与问题

项目从 TypeScript (`/root/shannon/`) 重构为 Python (`/root/shannon-py/`) 后，白盒和黑盒扫描分为独立 pipeline 执行。通过逐文件对比两个项目的 prompt、编排逻辑和验证机制，发现重构导致以下安全测试效果削弱：

| # | 问题 | 影响场景 | 严重性 | 根因 |
|---|------|---------|--------|------|
| A | `<if-static>` 条件分支机制被移除 | 白盒 | P0 | PromptManager 未实现 `stripConditionalBlocks()` |
| B | auth-exploit.txt 丢失 250 行内容 | 黑盒 | P1 | 重构时内容未完整移植 |
| C | ExploitationChecker 是死代码 | 黑盒 | P1 | 黑盒 workflow 未调用已有服务 |
| D | 支出监控 pattern 减少 | 通用 | P2 | billing.py 未完整移植 |

---

## 修复 A：恢复白盒条件分支机制

### 问题细节

重构前的 TypeScript PromptManager 有 `stripConditionalBlocks(text, hasWebUrl)` 函数，根据是否存在 `WEB_URL` 自动选择 `<if-live>` 或 `<if-static>` 内容块。

Python 版 PromptManager 没有实现这个机制，重构时直接删除了所有 `<if-live>`/`<if-static>` 标签，硬编码为黑盒内容。

**受影响的文件：**

- `prompts/shared/_target.txt` — 白盒场景下 agent 看到空 `URL:` 而非 "Mode: Offline static code analysis (no live target)"
- `prompts/shared/_vuln-scope.txt` — 白盒场景下 6 个 vuln agent 收到的是 "EXTERNAL ATTACKER SCOPE" 而非 "STATIC ANALYSIS SCOPE"，缺少对 unsafe data flows、missing input validation、insecure defaults、hardcoded secrets、dangerous API usage 等静态分析特有漏洞类型的关注指引

### 修复方案

**在 PromptManager 中实现 `strip_conditional_blocks()`。**

改动清单：

#### 1. `packages/core/src/shannon_core/prompts/manager.py`

在 `load_sync()` 方法中，变量插值之后、返回结果之前，增加一步条件块处理：

```python
import re

def strip_conditional_blocks(text: str, has_web_url: bool) -> str:
    """根据是否有 WEB_URL 选择 <if-live> 或 <if-static> 内容块。"""
    if has_web_url:
        text = re.sub(r'<if-static>.*?</if-static>', '', text, flags=re.DOTALL)
        text = text.replace('<if-live>', '').replace('</if-live>', '')
    else:
        text = re.sub(r'<if-live>.*?</if-live>', '', text, flags=re.DOTALL)
        text = text.replace('<if-static>', '').replace('</if-static>', '')
    return text
```

调用时机：在 `_render()` 或 `load_sync()` 中，变量插值完成后调用：

```python
has_web_url = bool(variables.get('WEB_URL'))
text = strip_conditional_blocks(text, has_web_url)
```

#### 2. `prompts/shared/_target.txt`

恢复为：

```
<if-live>URL: {{WEB_URL}}</if-live><if-static>Mode: Offline static code analysis (no live target)</if-static>

Filesystem:
- {{REPO_PATH}}/ (read only)
- {{REPO_PATH}}/.shannon/deliverables/ (read-write)
- {{REPO_PATH}}/.shannon/scratchpad/ (read-write) - screenshots, scripts, scratch work, etc.
```

#### 3. `prompts/shared/_vuln-scope.txt`

恢复为：

```
<if-live>**EXTERNAL ATTACKER SCOPE:** Only report vulnerabilities exploitable via {{WEB_URL}} from the internet. Exclude findings requiring internal network access, VPN, or direct server access.</if-live><if-static>**STATIC ANALYSIS SCOPE:** Report all code-level vulnerabilities discoverable through source code analysis. Include unsafe data flows, missing input validation, insecure defaults, hardcoded secrets, and dangerous API usage. Classify each finding by the code path that would be exercised at runtime.</if-static>
```

### 测试

- 验证白盒流程（无 WEB_URL）时 `_target.txt` 渲染为 "Mode: Offline static code analysis"，`_vuln-scope.txt` 渲染为 STATIC ANALYSIS SCOPE
- 验证黑盒流程（有 WEB_URL）时行为与当前一致
- 验证不含条件标签的 prompt 文件不受影响

---

## 修复 B：补全 auth-exploit.txt

### 问题细节

重构前 `exploit-auth.txt` 有 423 行，重构后 `auth-exploit.txt` 只有 173 行。丢失的核心内容包括：

1. **Bypass Exhaustion Protocol** — 要求 agent 穷尽多种绕过技术后才判定不可利用。删除后 agent 可能过早放弃，导致 auth 漏洞漏报
2. **详细分类标准** — EXPLOITED/POTENTIAL/FALSE_POSITIVE 各有 3-5 条判定规则。被通用的 `_exploit-methodology.txt` 4 级分类替代，auth 领域专业性下降
3. **Task Agent 脚本模板** — 批量攻击（暴力破解、凭证填充）的结构化执行指引
4. **交付物模板** — 成功利用和潜在漏洞的完整报告结构
5. **Critical Errors vs Justification Gaps** — 区分"根本不可行"和"需要更多尝试"的决策指引
6. **Classification Decision Framework** — 分类判断的核心原则

### 修复方案

**从 TS 版本完整移植丢失内容。**

改动清单：

#### 1. `prompts/auth-exploit.txt`

从 `/root/shannon/apps/worker/prompts/exploit-auth.txt` 移植以下段落（按原文件顺序）：

| 段落 | 行范围 (TS) | 说明 |
|------|-----------|------|
| Critical Errors vs Justification Gaps | 48-51 | 停止/继续决策指引 |
| Classification Decision Framework | 71-80 | 分类判断原则 |
| `{{LOGIN_INSTRUCTIONS}}` 引用 | 96-108 | 登录指令 + rules + rules-of-engagement |
| Task Agent Scripting Requirements | 163-180 | 批量攻击脚本模板 |
| Methodology & Domain Expertise | 182-310 | Bypass Exhaustion Protocol + OWASP Auth Workflow + 分类标准 |
| Deliverable Instructions | 312-397 | 完整交付物模板 |
| Conclusion Trigger | 399-423 | 证据完整性检查 |

**注意事项：**
- 保留重构后已有的 `{{VULNERABILITY_ENTRIES}}`、`{{AUTH_CONTEXT}}`、`{{PLAYWRIGHT_SESSION}}` 变量
- 保留重构后已有的 `@include(shared/_exploit-methodology.txt)` 引用，但将 auth 专用的方法论内容内联回来
- 箭头符号保持重构后的 ASCII `->` 风格（不回退到 Unicode `→`）
- 工具引用适配 Python 版（如 TodoWrite → 对应 Python 工具名）

### 测试

- 对比重构前后 auth-exploit.txt 的结构化输出，确认分类体系一致
- 黑盒扫描中验证 auth exploit agent 的利用尝试穷尽程度

---

## 修复 C：激活 ExploitationChecker

### 问题细节

`packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py` 定义了 `ExploitationChecker` 类，但黑盒 workflow (`packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`) 未导入或调用它。Workflow 中直接用 `queue_file.exists()` 做文件级别检查：

```python
# 当前代码（workflows.py 第 94-98 行）
for vt in selected_classes:
    queue_file = deliverables / f"{vt}_exploitation_queue.json"
    if not queue_file.exists():
        continue
```

这意味着：
- 不验证 JSON 结构完整性（白盒 agent 写入中断时可产生截断的 JSON）
- 不检查 `vulnerabilities` 数组是否为空
- 不区分 "文件不存在（正常无漏洞）" 和 "文件损坏（异常需处理）"

### 修复方案

**增强 ExploitationChecker 并在 workflow 中调用。**

改动清单：

#### 1. `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py`

增强 `should_exploit()` 方法：

```python
@staticmethod
async def should_exploit(
    deliverables_path: Path,
    vuln_type: str,
    exploit_enabled: bool = True,
) -> bool:
    if not exploit_enabled:
        return False

    queue_path = deliverables_path / f"{vuln_type}_exploitation_queue.json"
    if not queue_path.exists():
        return False

    try:
        content = queue_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(
            "Queue file for %s is corrupted: %s. Skipping exploit.", vuln_type, e
        )
        return False

    vulnerabilities = data.get("vulnerabilities")
    if not isinstance(vulnerabilities, list):
        logger.warning(
            "Queue file for %s has invalid 'vulnerabilities' field. Skipping exploit.",
            vuln_type,
        )
        return False

    return len(vulnerabilities) > 0
```

#### 2. `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`

替换直接的文件存在性检查为 ExploitationChecker 调用：

```python
from ..services.exploitation_checker import ExploitationChecker

# 替换 workflows.py 中的 queue_file.exists() 检查
for vt in selected_classes:
    should_run = await ExploitationChecker.should_exploit(
        deliverables_path=deliverables,
        vuln_type=vt,
        exploit_enabled=input.exploit,
    )
    if not should_run:
        continue
    # ... 执行 exploit agent
```

### 测试

- 验证正常 queue 文件（有效 JSON + 非空 vulnerabilities）→ should_exploit 返回 True
- 验证空 queue 文件（vulnerabilities 为空数组）→ 返回 False
- 验证损坏 queue 文件（截断 JSON）→ 返回 False 并记录警告日志
- 验证不存在 queue 文件 → 返回 False
- 验证 exploit_enabled=False → 直接返回 False

---

## 修复 D：补全支出监控 pattern（可选）

### 问题细节

重构前 TypeScript 版有 11 个 API error patterns + 5 个 text patterns。Python 版 `billing.py` 只有 6 个 patterns，缺少以下关键模式：

- `billing_error`
- `credit balance is too low`
- `insufficient credits`
- `usage is blocked due to insufficient credits`
- `please visit plans & billing`
- `please visit plans and billing`
- `usage limit reached`
- `quota exceeded`
- `daily rate limit`
- `limit will reset`
- `billing limit reached`
- `monthly limit`
- `cap reached`
- `spending limit`

### 修复方案

从 TS 版本移植缺失的 pattern 到 `packages/core/src/shannon_core/utils/billing.py`。

---

## 实施顺序

```
修复 A（条件分支）→ 修复 C（queue 验证）→ 修复 B（auth-exploit）→ 修复 D（billing patterns）
     P0                    P1                    P1                  P2
```

- 修复 A 优先：影响 6 个白盒 vuln agent 的核心范围定义
- 修复 C 其次：改动最小（激活已有代码），且为后续测试提供安全网
- 修复 B 第三：内容移植工作量大，但不影响其他修复
- 修复 D 可选：影响最低

## 影响范围

| 修复 | 改动文件数 | 涉及 package | 有破坏性变更 |
|------|-----------|-------------|-------------|
| A | 3 | core + prompts | 无（纯增量） |
| B | 1 | prompts | 无（纯增量） |
| C | 2 | blackbox | 无（替换内部调用） |
| D | 1 | core | 无（纯增量） |

总计：7 个文件，无公共 API 变更，无数据库 schema 变更，无配置 breaking change。
