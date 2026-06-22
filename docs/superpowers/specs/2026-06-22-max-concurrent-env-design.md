# Spec:`SHANNON_MAX_CONCURRENT` —— 通过 .env 统一控制白盒/黑盒扫描并发

- **日期**:2026-06-22
- **状态**:Approved(设计已批准,待写实现计划)
- **分支**:feat/fork-py
- **作者**:brainstorming 会话产出

---

## 1. 背景与问题

当前两路扫描的并发控制不一致且都无法通过 `.env` 调整:

| 路径 | 现状 | 证据 |
|---|---|---|
| **白盒 vuln agents** | 5 个 vuln class **全开**(`asyncio.gather`,无任何限流) | `whitebox/pipeline/workflows.py:297` |
| **黑盒 exploit agents** | 已用 `asyncio.Semaphore(input.max_concurrent)` 限流,但 `max_concurrent` 只能由 CLI `--max-concurrent` 传入(默认 3) | `blackbox/pipeline/workflows.py:235`、`blackbox/cli/main.py:41`、`BlackboxPipelineInput.max_concurrent=3`(`blackbox/pipeline/shared.py:13`) |

代码实际读取的全部 `os.getenv` 键里**没有任何并发相关项**,`.env.example` 也无。用户需要把两路并发都限到 2,且要求通过 `.env` 调整(不每次传 CLI flag)。

## 2. 目标

1. 新增单一 env 变量 `SHANNON_MAX_CONCURRENT`,默认 `3`,作为白盒 vuln agents 与黑盒 exploit agents 的并发上限默认值。
2. 白盒从"全开"改为受 semaphore 限流,照搬黑盒已验证的 `asyncio.Semaphore` 模式。
3. 黑盒 CLI `--max-concurrent` 的默认值改为从 env 读(CLI 显式传仍覆盖)。
4. combined(白盒→黑盒串行)路径两边读同一个 env 值。

## 3. 非目标(YAGNI 边界)

- **不动** Temporal Worker 层 `max_concurrent_activity_tasks` 等参数(另一层语义,用户未要求)。
- **不给白盒新增 CLI flag**(用户要的是 `.env` 调;白盒无现成 flag 位置,加 flag 属 scope 蔓延)。
- **不做** exploit / validation 分级并发,只限 agent 总并发。
- **不引入** per-vuln-class 差异化并发。

## 4. 设计

### 4.1 env 变量与优先级

```
SHANNON_MAX_CONCURRENT=3   # ≥1 的整数;默认 3
```

| 运行方式 | 并发数来源(高 → 低) |
|---|---|
| 黑盒独立 | CLI `--max-concurrent` > `SHANNON_MAX_CONCURRENT` > 默认 3 |
| 白盒独立 | `SHANNON_MAX_CONCURRENT` > 默认 3 |
| combined | `SHANNON_MAX_CONCURRENT` > 默认 3(白盒、黑盒共用,串行不冲突) |

### 4.2 读取入口:`get_max_concurrent()`

新建 `packages/core/src/shannon_core/config/concurrency.py`,提供:

```python
import logging, os

_DEFAULT = 3
_log = logging.getLogger(__name__)

def get_max_concurrent() -> int:
    raw = os.environ.get("SHANNON_MAX_CONCURRENT")
    if raw is None:
        return _DEFAULT
    try:
        val = int(raw)
    except ValueError:
        _log.warning("SHANNON_MAX_CONCURRENT=%r not an int; falling back to %d", raw, _DEFAULT)
        return _DEFAULT
    if val < 1:
        _log.warning("SHANNON_MAX_CONCURRENT=%d must be >=1; falling back to %d", val, _DEFAULT)
        return _DEFAULT
    return val
```

**容错决策(故意偏离项目现有风格)**:现有 `SHANNON_LIVE_REFRESH_HZ`(`audit/display_lifecycle.py:14`)用裸 `float(os.environ.get(...))`,非数字会直接抛 `ValueError`。并发数选择**校验 + 回退默认 + warn**:一个 env typo 不应让整次扫描崩溃,且日志可察觉。这是有意的健壮性取舍。

### 4.3 改动落点(5 处)

**① 读取入口**:新建 `core/src/shannon_core/config/concurrency.py`(见 4.2)。

**② 白盒 `PipelineInput` 加字段** —— `whitebox/pipeline/shared.py:8` 的 dataclass 增加:
```python
max_concurrent: int = 3
```

**③ 白盒 workflow semaphore 限流** —— 改 `whitebox/pipeline/workflows.py:281-297`,照搬黑盒 `blackbox/pipeline/workflows.py:234-246` 的 `bounded_exploit` 模式:
```python
vuln_tasks = []
for vt in selected_classes:
    agent_name = AgentName(f"{vt}-vuln")
    if agent_name.value not in self._state.completed_agents:
        vuln_tasks.append((vt, agent_name, workflow.execute_activity(
            activities.run_vuln_agent,
            ActivityInput(**{**act_input.__dict__, "agent_name": agent_name.value}),
            start_to_close_timeout=timedelta(hours=2),
            retry_policy=RetryPolicy(...),  # 保持现有 retry_policy 不变
        )))

if vuln_tasks:
    semaphore = asyncio.Semaphore(input.max_concurrent)

    async def bounded(coro):
        async with semaphore:
            return await coro

    results = await asyncio.gather(
        *[bounded(t) for _, _, t in vuln_tasks],
        return_exceptions=True,
    )
    # 结果处理循环不变(仍按 results[i] ↔ selected_classes 对齐)
```

**④ 黑盒 CLI default 从 env 读** —— `blackbox/cli/main.py:41`:
```python
@click.option("--max-concurrent", default=get_max_concurrent(), type=int,
              help="Max concurrent exploit agents (env: SHANNON_MAX_CONCURRENT, default: 3)")
```
`BlackboxPipelineInput.max_concurrent=3`(`shared.py:13`)保留作为 dataclass 兜底默认;实际值由 CLI default(已读 env)注入。

**⑤ combined orchestrator** —— `combined/orchestrator.py` 构造 `wb_input`(:31)与 `bb_input`(:59)时各填:
```python
wb_input = PipelineInput(..., max_concurrent=get_max_concurrent())
bb_input = BlackboxPipelineInput(..., max_concurrent=get_max_concurrent())
```
读一次 env、两边一致。

**⑥ 文档** —— 在 `.env.example`(共享、引擎无关配置区)加。**不改** `.env.profiles*/`(那是引擎/账号 profile,并发数与其无关):
```
# 并发上限:白盒 vuln agents + 黑盒 exploit agents(默认 3,combined 串行不冲突)
SHANNON_MAX_CONCURRENT=3
```

**实现时务必 `grep -rn "PipelineInput(\|BlackboxPipelineInput("` 确认所有构造点已覆盖**(已知:`combined/orchestrator.py:31/59`、`whitebox/worker.py:282`、`blackbox/worker.py:151`、blackbox/whitebox CLI),遗漏点会让该路径回落到 dataclass 默认 3。

### 4.4 数据流

```
.env (SHANNON_MAX_CONCURRENT=2)
  └─ get_max_concurrent() 读取一次
       ├─ whitebox CLI / orchestrator → PipelineInput(max_concurrent=2)
       │      └─ workflow: asyncio.Semaphore(2) 包住 vuln gather
       └─ blackbox CLI default / orchestrator → BlackboxPipelineInput(max_concurrent=2)
              └─ workflow: asyncio.Semaphore(2) 包住 exploit gather(已存在)
```

## 5. 关键技术决策

- **Temporal workflow 里用 `asyncio.Semaphore` 是否安全?** 安全。黑盒 `workflows.py:235` 已在生产路径这么做并跑通;白盒照搬,结构几乎一致,零新增风险。
- **为什么通过 `PipelineInput` 传参,而非在 workflow 内 `os.getenv`?** Temporal workflow 必须满足 deterministic replay,workflow 内读 env 会在 replay 时拿到不同值导致非确定性。把值放进 input 是 Temporal 规范做法,也与黑盒现状一致。
- **为什么单变量而非白盒/黑盒分开?** combined 是白盒→黑盒串行,两边不会同时运行,共用一个值语义无冲突,且对应用户"调为 2"的单数表述(用户已确认选此方案)。

## 6. 测试策略

1. **`get_max_concurrent` 单测**(新建 `core/tests/test_concurrency_config.py`):
   - 合法值(`"2"` → 2)
   - 未设置 → 默认 3
   - 非整数(`"abc"`)→ 回退 3 + warn
   - `<1`(`"0"`、`"-1"`)→ 回退 3 + warn
   - 用 `monkeypatch.setenv` / `delenv`
2. **白盒 workflow 并发上限测试**:照搬 `core/tests/test_concurrency.py:67-83` 的峰值计数模式 —— 5 个 mock vuln activity,`max_concurrent=2`,断言任意时刻并发执行数 ≤ 2。
3. **黑盒 CLI default 回归**:env 设 `SHANNON_MAX_CONCURRENT=4` 时,`--max-concurrent` 的 default 解析为 4;显式传 `--max-concurrent 5` 仍覆盖为 5。
4. **`PipelineInput` / `BlackboxPipelineInput` 字段默认值**断言(`=3`),防止意外回归。

## 7. 验收标准

- [ ] `.env` 设 `SHANNON_MAX_CONCURRENT=2`,白盒扫描时 vuln agents 峰值并发 = 2(不再 5 个全开)。
- [ ] 同一 env,黑盒 exploit agents 峰值并发 = 2。
- [ ] 黑盒 `--max-concurrent N` 仍能覆盖 env。
- [ ] 不设 env 时,白盒默认并发 = 3(行为从"全开 5"变为 3,属预期变化)、黑盒仍 = 3。
- [ ] env 填非法值不崩溃,扫描正常以默认 3 运行并打 warning。
- [ ] combined 路径白盒、黑盒都读到同一并发值。
- [ ] 第 6 节全部测试通过。

## 8. 行为变化(需知晓)

- **白盒默认并发从"全开(5)"变为 3**。这是 intended —— 当前白盒无任何限流本就是缺失项,引入后默认值与其他层一致。若需恢复全开,设 `SHANNON_MAX_CONCURRENT=5`(或 ≥ vuln class 数)。
- 黑盒默认仍是 3,无变化。

## 9. 风险

| 风险 | 评估 | 缓解 |
|---|---|---|
| 白盒 semaphore 在 Temporal workflow 里行为异常 | 低(黑盒已验证) | 第 6.2 节峰值并发测试直接验证 |
| 漏改某个 `PipelineInput(` 构造点 → 该路径回落默认 3 | 中 | 实现时 `grep` 全构造点;验收标准含 combined 路径 |
| 并发降到 2 会拉长扫描 wall-clock | 已知取舍 | 用户主动要求;可通过 env 调回 |
