# 白盒 vuln 类选择控制（CLI / env + 修通 YAML 断链）

- 日期：2026-07-01
- 分支：`feat/fork-py`
- 状态：设计稿（待 review → writing-plans）

## 1. 背景与动机

微服务场景：Node.js 前端网关 + gRPC 后端。后端 gRPC 代码不直接面公网，扫 `auth` / `xss` 无意义且浪费 token；只需前端网关扫出 `auth`/`xss`（以及 `injection`/`ssrf` 的入口），后端只跑 `injection`/`ssrf`，前端 source → 后端 sink 的跨服务链由关联分析拼接。

用户希望：**白盒能通过 CLI 选项或 `.env` 指定跑哪些 vuln agent，CLI 选项优先级最高。**

### 关键现状澄清（重要）

经探索确认，用户的微服务场景**大部分已由 `shannon-multi` 包支持**，本特性**不碰多仓库**：

- `shannon-multi start -c multi-repo.yaml` 已支持「多仓库各自白盒扫 + 跨仓库关联分析」，产出 `cross-service-topology.json` / `trust-boundaries.json` / `correlation-report.md` / 合并后的 exploitation queue。
- 每个 repo 可在 `multi-repo.yaml` 的 `scan_config` 指定自己的白盒 YAML，**per-repo vuln 类已可区分**。
- `shannon-multi` 有两种模式：① repo 声明 `path` → 现扫 + 关联；② repo 声明 `workspace` → **复用已独立跑过的结果，不重扫**。
- 用户采用**模式 ②**：先分别 `shannon-whitebox start` 独立跑前端/后端（用 CLI/env 控制 vuln 类），再 `shannon-multi` 复用 workspace 做关联。

→ 因此本特性聚焦：**给 `shannon-whitebox` 单仓库入口加 CLI/env 控制 vuln 类的能力**，让"独立跑"这步能灵活指定。多仓库 / 关联分析不动。

## 2. 现状（含 pre-existing 断链）★

### 2.1 vuln 类清单

5 类，两处平行定义（内容相同）：
- `packages/core/src/shannon_core/models/agents.py:155` — `ALL_VULN_CLASSES`（白盒 workflow 用，`VulnType` Literal）
- `packages/core/src/shannon_core/models/config.py:75` — `ALL_VULN_CLASSES`（YAML `Config.vuln_classes` 的类型 `VulnClass` Literal）

值：`["injection", "xss", "auth", "authz", "ssrf"]`（顺序可能不同）。

### 2.2 白盒 CLI 无 vuln 类选项

`packages/whitebox/src/shannon_whitebox/cli/main.py:32-44` 的 `start` 子命令（click）**没有** `--vuln-classes` / `--only` / `--skip-vuln`。对比黑盒 `packages/blackbox/src/shannon_blackbox/cli/main.py:38` 有 `--vuln-classes`（`multiple=True`）。

### 2.3 ⚠️ Pre-existing 断链：白盒 YAML `vuln_classes` 当前不生效

证据链（逐一核实）：

1. `cli/main.py:51-60` 构造 `PipelineInput` 时**不设 `vuln_classes`**，也不解析 YAML。
2. `workflows.py:40` `selected_classes = input.vuln_classes or list(ALL_VULN_CLASSES)` — 只看 `input.vuln_classes`（继承自 `BasePipelineInput.vuln_classes`，`models/base.py:15`）。
3. `workflows.py:99-101` 虽 `cfg = parse_config(input.config_path)`，但后续**只用 `cfg.browser_engine` 和 `cfg.rules.avoid`，从不引用 `cfg.vuln_classes`**（`grep` 确认白盒全包仅 `workflows.py:40` 引用 `.vuln_classes`，无 `cfg.vuln_classes`）。
4. `distribute_config`（`parser.py:251-268`，会把 `config.vuln_classes` 转 `DistributedConfig`）只在 `executor.py:78` 与 `validate_authentication.py:116` 调用 — 仅用于给 prompt 注入 `{{VULN_CLASSES_TESTED}}` 占位符（信息性），**不控制 vuln agent 调度**。

**结论**：白盒无论 YAML `vuln_classes` 写什么，5 个 vuln agent 全跑。本特性须**顺手修通这环**，否则优先级链的 YAML 层是断的。

### 2.4 env 无 vuln 类控制

`SHANNON_*` env 变量里有 `SHANNON_LLM_TRACK_ENABLED` / `SHANNON_GITNEXUS_LLM_ENABLED` / `SHANNON_MAX_CONCURRENT`（`config/concurrency.py`），**无任何控制 vuln 类的 env**。

### 2.5 report assemble 硬编码（无害）

`activities.py:830` `vuln_classes = list(ALL_VULN_CLASSES)` 硬编码。但 `ReportAssembler.assemble`（`services/report_assembler.py:17-29`）对缺失文件 **tolerant**（`if await async_path_exists(...)`，缺失即跳过），故没跑的类无产物时自动跳过，**实际无害**。

## 3. 目标 / 非目标

### 目标
1. 给 `shannon-whitebox start` 加 `--vuln-classes` CLI 选项（**逗号分隔**，如 `--vuln-classes injection,xss`）。
2. 加 `SHANNON_VULN_CLASSES` env 变量（逗号分隔）。
3. 优先级链：**CLI > env > YAML `vuln_classes` > `ALL_VULN_CLASSES` 默认全跑**。
4. **修通 YAML 断链**：让 `cfg.vuln_classes` 真正参与 `selected_classes` 决定。
5. 非法类名 **fail fast**（CLI/env 传不存在的类 → 报错退出）。

### 非目标（YAGNI）
- 不动 `shannon-multi`（per-repo vuln 类已由 scan_config YAML 支持）。
- 不动黑盒（已有 CLI；黑盒补 env 对齐留作 follow-up，可复用本特性的 `resolve_vuln_classes`）。
- 不引入 `--skip-vuln` / disable 黑名单语义、不引入 preset 枚举。
- 不改 vuln agent 内部行为、不改双轨、不改 GitNexus 轨。
- 不改 `distribute_config` / `{{VULN_CLASSES_TESTED}}` 占位符注入路径。

## 4. 详细设计

### 4.1 两个纯函数（集中优先级逻辑，可单测、可复用）

新增 `packages/core/src/shannon_core/config/vuln_selection.py`：

```python
from typing import Sequence
from shannon_core.models.config import ALL_VULN_CLASSES

class InvalidVulnClass(ValueError):
    """CLI/env 指定了不存在的 vuln 类。"""

def _parse_and_validate(raw: str, allowed: Sequence[str]) -> list[str]:
    """逗号分隔 → trim → 去空串 → 保序去重 → 校验每个 ∈ allowed。"""
    items: list[str] = []
    seen: set[str] = set()
    for token in raw.split(","):
        v = token.strip()
        if not v:
            continue
        if v not in allowed:
            raise InvalidVulnClass(
                f"未知的 vuln 类 {v!r}；合法值：{', '.join(allowed)}"
            )
        if v not in seen:
            seen.add(v)
            items.append(v)
    return items

def resolve_vuln_classes(
    cli_str: str | None,
    env_str: str | None,
    *,
    allowed: Sequence[str] = ALL_VULN_CLASSES,
) -> list[str] | None:
    """合并「字符串来源」：CLI > env。两者都空 → None（由调用方兜底 YAML/默认）。"""
    for raw in (cli_str, env_str):
        if raw and raw.strip():
            return _parse_and_validate(raw, allowed)
    return None

def select_vuln_classes(
    override: list[str] | None,
    yaml_vuln: list[str] | None,
    *,
    default: Sequence[str] = ALL_VULN_CLASSES,
) -> list[str]:
    """合并「list 来源」：override（CLI/env 已解析）> YAML > 默认全跑。"""
    if override:
        return list(override)
    if yaml_vuln:
        return list(yaml_vuln)
    return list(default)
```

设计要点：
- `resolve_vuln_classes` 专管「字符串来源（CLI/env）解析 + 校验 + 去重」，黑盒将来补 env 时可直接复用。
- `select_vuln_classes` 专管「list 来源（CLI/env override vs YAML vs 默认）」优先级。
- env 必须在 **CLI 层**读（尊重 workflow sandbox 不变量：`workflow.run()` 内禁 env 解析，见 memory `blackbox-workflow-sandbox-paths-invariant`）。resolve 出的 override 以 `list` 形式进 `PipelineInput.vuln_classes`，workflow 不再碰 env 字符串。

### 4.2 CLI 改动（`whitebox/cli/main.py`）

`start` 子命令新增 option 与合并逻辑：

```python
@click.option(
    "--vuln-classes", "vuln_classes_cli", default=None,
    help="逗号分隔的 vuln 类（如 injection,xss）；优先于 SHANNON_VULN_CLASSES env 与 YAML。"
)
def start(..., vuln_classes_cli, ...):
    from shannon_core.config.vuln_selection import resolve_vuln_classes, InvalidVulnClass
    ...
    try:
        override = resolve_vuln_classes(
            vuln_classes_cli,
            os.environ.get("SHANNON_VULN_CLASSES"),
        )
    except InvalidVulnClass as e:
        raise click.UsageError(str(e)) from e

    input = PipelineInput(
        ...,
        vuln_classes=override,   # None 时落回 YAML/默认（workflow 层 select_vuln_classes 处理）
    )
```

> 注：`os` 需在文件顶部 import（当前未 import）。

### 4.3 workflow 改动（`whitebox/pipeline/workflows.py`）— 修通 YAML 断链 ★

当前第 40 行（`selected_classes` 计算）在第 99 行（`cfg = parse_config`）**之前**。需调整顺序：把 `cfg` 解析提前，再用 `select_vuln_classes` 合并。

```python
# 把现有的 cfg 解析块（96-101）提到 selected_classes 之前
cfg = None
if input.config_path:
    from shannon_core.config.parser import parse_config
    cfg = parse_config(input.config_path)

from shannon_core.config.vuln_selection import select_vuln_classes
selected_classes: list[VulnType] = select_vuln_classes(
    input.vuln_classes,
    cfg.vuln_classes if cfg else None,
)
```

后续 `cfg.browser_engine`（103 行起）、`cfg.rules.avoid`（121 行）逻辑不变（只是 `cfg` 来源提前算好了）。

> import 位置：`select_vuln_classes` 是纯函数无 I/O，放顶部 import（与第 8 行 `ALL_VULN_CLASSES` 同级）即可，无需 `unsafe.imports_passed_through`。

### 4.4 report assemble（`activities.py:830`）— 可选优化（Minor）

因 `ReportAssembler.assemble` tolerant，当前硬编码 `ALL_VULN_CLASSES` 无害。为语义一致，可改为用实际 `selected_classes`：给 `ActivityInput` 加 `vuln_classes: list[str] | None = None`，workflow 调 `assemble_report` 时传 `selected_classes`，activity 里 `vuln_classes = input.vuln_classes or list(ALL_VULN_CLASSES)`。

**降级为 Minor / 可选**：不阻塞主特性，实现时可一并做或留 follow-up。

### 4.5 用户流程示例

```bash
# 前端网关：默认全跑（或不传 = 全跑）
shannon-whitebox start -r ./node-gateway -w fe-ws --url http://localhost:3000

# 后端 gRPC：env 控制，只跑 injection/ssrf
SHANNON_VULN_CLASSES=injection,ssrf \
  shannon-whitebox start -r ./grpc-svc -w be-ws

# 或 CLI 控制（优先于 env）
shannon-whitebox start -r ./grpc-svc -w be-ws --vuln-classes injection,ssrf

# 关联：multi-repo.yaml 里 fe/be 声明 workspace（复用，不重扫）
shannon-multi start -c multi-repo.yaml
```

对应 `multi-repo.yaml`（复用模式，**不需要 scan_config 的 vuln_classes**）：
```yaml
repos:
  frontend: { role: entrypoint, workspace: fe-ws }
  backend:  { role: backend,     workspace: be-ws }
relations:
  - { from: frontend, to: backend, protocol: grpc }
correlation: { out_workspace: corr-ws }
```

## 5. 边界情况

| 场景 | 行为 |
|------|------|
| CLI/env 都未设、YAML 未设 | 全跑 `ALL_VULN_CLASSES`（默认不变） |
| 只设 CLI | 用 CLI（忽略 env 与 YAML） |
| 只设 env（无 CLI） | 用 env（忽略 YAML） |
| CLI/env 都设 | CLI 胜 |
| CLI/env 都空、YAML 有 | 用 YAML（**修通后的新行为**） |
| 传非法类（如 `--vuln-classes foo`） | `click.UsageError`，列出合法值 |
| 传空字符串 / 仅逗号 / 重复值 | `resolve` trim + 去空 + 保序去重，结果为空则视同未设 |
| 大小写 | 当前 vuln 类全小写；`_parse_and_validate` 不做大小写归一（严格匹配），避免静默接受拼写变体 |

## 6. 测试策略（TDD）

新增 `packages/core/tests/config/test_vuln_selection.py`（纯函数单测）：
- `resolve_vuln_classes`：CLI 优先 env / 只 CLI / 只 env / 都空返回 None / 逗号解析 / trim / 去空串 / 保序去重 / 非法值抛 `InvalidVulnClass` 且消息含合法清单。
- `select_vuln_classes`：override 优先 / override 空走 YAML / 都空走默认。

新增 / 扩展 `packages/whitebox/tests/cli/test_main.py`（或对应 cli 测试）：
- `--vuln-classes injection,xss` → `PipelineInput.vuln_classes == ["injection","xss"]`。
- `SHANNON_VULN_CLASSES` env → `PipelineInput.vuln_classes` 正确。
- CLI > env 优先（同时设两者）。
- 非法值 → `click.UsageError`（用 `click.testing.CliRunner`）。

新增 workflow 优先级防回退锚点（关键，守住 §2.3 修通成果）：
- `input.vuln_classes` 有 → `selected_classes` 用它。
- `input.vuln_classes` 空 + `cfg.vuln_classes` 有 → `selected_classes == cfg.vuln_classes`（**YAML 真生效**）。
- 都空 → `ALL_VULN_CLASSES`。
- 若 workflow 测试受 temporalio sandbox 限制难直接跑，则把 `select_vuln_classes` 当作受测单元、workflow 只做一行调用（已满足）。

> 测试纪律（CLAUDE.md / memory `pytest-whitebox-hang`）：只跑改动相关测试文件，勿广跑全套。

## 7. 不变量（本特性不得违反）

- **双轨独立性**（CLAUDE.md §1）：本特性只动 vuln 类「调度选择」，不动 LLM 轨 / GitNexus 轨的内部逻辑、不喂确定性产物给 LLM 轨 prompt。
- **workflow sandbox 不变量**：env 解析只在 CLI 层，`workflow.run()` 内不读 env（override 以 list 进 `PipelineInput`）。
- **黑盒不受影响**：黑盒 `--vuln-classes`（multiple）行为不变；本特性只加白盒。
- **多仓库不受影响**：`shannon-multi` / `MultiRepoConfig` / `RepoSpec.scan_config` 一字不改。

## 8. 风险与权衡

- **顺序调整副作用**：把 `cfg` 解析提前到 `selected_classes` 之前，原 96-101 块移位。需确认 `parse_config` 无副作用依赖特定时序（它纯解析 YAML + 校验，无副作用，安全）。
- **YAML 修通后的行为变化**：修通后，已有 YAML 写了 `vuln_classes` 但之前被忽略的用户，会观察到 vuln agent 数量变化（从全跑变成只跑指定类）。这是**修 bug 的预期后果**，需在 PR/commit 说明里高亮。
- **CLI 逗号语法与黑盒不一致**：黑盒是 `multiple=True`（多次 flag），白盒用逗号。接受不一致（用户偏好逗号更顺手）；黑盒对齐留 follow-up。

## 9. Follow-up（不在本特性范围）

- 黑盒补 `SHANNON_VULN_CLASSES` env 并复用 `resolve_vuln_classes`（黑白盒一致）。
- 黑盒 CLI 改逗号语法（或白盒补 multiple，二选一对齐）。
- `activities.py:830` report assemble 用 `selected_classes`（§4.4，若 deemed worth it）。
- 考虑 `--list-vuln-classes` 辅助选项（列出合法值，便于发现）。
