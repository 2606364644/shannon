# GitNexus 轨 LLM 环节进度日志（可观测性）

- 日期：2026-07-01
- 状态：设计获批，待出实现计划
- 分支：`feat/fork-py`
- 关联：
  - `2026-06-30-discover-sinks-llm-concurrency-design.md`（`map_llm_with_bounds` 并发骨架，本 spec 不改其签名）
  - `2026-06-26-gitnexus-llm-sink-discovery-design.md`（`discover_sinks_llm` / `analyze_taint_llm` 来源）
  - `2026-06-22-log-format-redesign.md`（DisplayEvent / symbols 单一来源 / pad_rule）
  - CLAUDE.md §1 双轨铁律（本 spec 不喂确定性产物给 LLM 轨、不改 LLM 轨）

## 1. 背景

白盒双轨下，GitNexus 轨的 sink/source/taint 补召回与 chain_verdict 都走 LLM，但这些环节跑起来是**黑盒**：

- `discover_sinks_llm` / `discover_sources_llm` / `analyze_taint_llm` 经 `map_llm_with_bounds` 并发跑 N 个函数的 LLM 判定（大仓可达上百函数 × 单次 60s 超时），**运行中零进度输出**；只有结束后 `code_index/__init__.py` 的一行汇总（`LLM sink discovery added N soft sinks`）+ `map_llm_with_bounds` 的超时/跳过 warning。
- `chain_verdict` 在三个 builder 的 for 循环里串行 `await judge_chain_verdict(...)`，候选链多时同样黑盒。
- 这些 LLM 调用都在 worker 进程的 Temporal activity 内（`run_code_index` / `run_gitnexus_chain_verdict` / `run_authz_gitnexus_judge`），CLI 主进程的 `ProgressIndicator`（`cli/progress.py`，固定文案转圈 spinner）**看不到 activity 内部**，用户无从知晓进度。

且现有 activity 内的 `logger.info` **不进 `workflow.log`**（默认走 stderr / `activity_failures.log`），也不是用户能 `tail -f` 的渠道。

## 2. 目标 / 非目标

**目标**

- GitNexus 轨**全环节** LLM 操作（sink/source/taint 补召回 + chain_verdict + authz IDOR 判定）在运行中向 `workflow.log` + CLI 终端实时输出进度，用户能 `tail -f` 看到走到第几个、判出了什么。
- 复用现有 DisplayEvent → dispatcher → `FileLogRenderer`（`workflow.log`）+ `rich_renderer`（终端）通道，与 LLM 轨的 `[LLM]`/`[TOOL]` 行观感统一、`grep` 友好。
- 进度是 **best-effort**：显示通道任何失败绝不影响扫描。

**非目标**

- 不改 `map_llm_with_bounds` 签名（进度在 per-item 闭包里发，不污染通用并发骨架）。
- 不改 LLM 轨（PRE_RECON / vuln agent）任何 prompt 或行为（守 CLAUDE.md §1 双轨铁律）。
- 不改 GitNexus 轨的判定逻辑 / 召回语义，只加可观测性。
- 不为采样频率引入新 env（K 固定，YAGNI；将来需要再 env 化）。
- 不改 `cli/progress.py` 的 spinner（那是 CLI 主进程层，与 activity 内进度是两条路）。

## 3. 现状：为什么看不到 + LLM 轨机制对比

**LLM 轨是事件驱动、每个语义动作全打、零采样**（不是"每隔 K 时间"）：

| 时机 | event | 证据（无条件 dispatch） |
|------|-------|------------------------|
| agent 起/止 | `AgentEvent` | `audit/session.py:55,85` |
| 每次工具调用 | `ToolCallEvent` → `[TOOL]` | `agents/message_dispatcher.py:88`、`agents/openai_stream_collector.py:56` |
| 每轮 LLM 输出 | `LlmTurnEvent` → `[LLM]` | `audit/session.py:64` |

LLM 轨能"全打"是因为：1 个 agent × 几十次工具调用，每次 grep/Read 都富语义、量适中，全打有价值。

GitNexus 轨不能照搬"全打"：`discover_sinks_llm` 是大仓几百函数 × 同质 LLM 判定，多数"未命中"无信息，全打 = 几百行噪声。**这是工作负载性质不同，不是机制不同**——两条轨都走同一个 dispatcher → `FileLogRenderer`/`rich_renderer`。

因此本 spec 的频率策略继承 LLM 轨"有信息价值的全打"精神：**命中即时打、未命中采样打**。

## 4. 设计决策（已与用户确认）

| 维度 | 决策 | 理由 |
|------|------|------|
| **粒度** | 计数主线 + 命中细节 | 第 X/N + 累计 hits；判出真 sink/verdict=vulnerable 时额外打函数名/行号/slot。未命中只计数不刷屏。最贴合"看到 sink 块被识别出来"。 |
| **范围** | GitNexus 轨全环节 | sink/source/taint 补召回 + chain_verdict + authz IDOR 判定。同类黑盒一次做齐。 |
| **频率** | 采样 + 命中即时 + 汇总 | 每 K=10 个单位打计数；命中即时打；结束打汇总。大仓不刷屏、命中不漏。 |
| **注入机制** | 显式 `progress_cb` 注入 | core 定义纯协议、零新依赖；采样/格式化集中在 activity 层；全环节统一协议。不选 contextvar（隐式 + async 边界陷阱）。 |
| **观感/标签** | 新增专属 event + `GN-LLM` 标签 | 与 LLM 轨 `[LLM]` 行观感统一、`grep GN-LLM` 一键过滤；新增 1 个 DisplayEvent 类型 + 两个 renderer 分支。 |

## 5. 架构 & 数据流

```
run_code_index / run_gitnexus_chain_verdict / run_authz_gitnexus_judge   (whitebox activity)
  │  构造 progress_cb：采样 + 包装 session.log_gitnexus_progress(...)
  ▼
build_code_index_with_gitnexus(..., progress_cb=cb) / build_*_findings(..., progress_cb=cb)   (core)
  │  透传给三个 discover/analyze + builder 循环
  ▼
ProgressEmitter(phase, total, cb)    ← core 新增；per-item 完成时 tick(done++, hits+=Δ, detail)
  ▼
ProgressSample → progress_cb → session.log_gitnexus_progress → workflow_logger
  ▼
GitnexusLlmEvent → dispatcher
  ▼
FileLogRenderer 写 workflow.log   ∥   rich_renderer 滚到 CLI 终端
```

两个原则：
1. **进度计数在 core 的 `ProgressEmitter`**（per-item `tick`）；**采样/格式化在 activity 层包装**里。core 只发 raw 样本，将来调显示策略只动 activity 一处。
2. **`progress_cb=None` 时全程跳过**（测试 / 未注入 / `SHANNON_GITNEXUS_LLM_ENABLED=0` → LLM client=None → discover 早退 → emitter 不 tick）。

## 6. 组件细节

### 6.1 core 新增 `code_index/progress.py`

```python
"""GitNexus 轨 LLM 环节的进度计数与 best-effort 上报。

core 层只定义协议 + 计数器，不感知 whitebox 的 audit session；采样/格式化
由 activity 层注入的 progress_cb 负责。"""
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

Phase = Literal["sink-discovery", "source-discovery", "taint-analysis",
                "chain-verdict", "authz-judge"]


@dataclass(frozen=True)
class ProgressSample:
    phase: Phase
    done: int                 # 已完成单位数
    total: int
    hits: int                 # 累计命中（语义随 phase，见 §11）
    detail: str | None        # 命中细节（hit 行用）；None=未命中
    final: bool = False       # True=结束汇总行


ProgressCb = Callable[[ProgressSample], Awaitable[None]] | None


class ProgressEmitter:
    """并发安全的 per-item 进度计数器。tick 在 asyncio 单线程下原子递增。"""
    def __init__(self, phase: Phase, total: int, cb: ProgressCb):
        self._phase = phase
        self._total = total
        self._cb = cb
        self._done = 0
        self._hits = 0

    async def tick(self, detail: str | None = None, hits_delta: int = 0) -> None:
        self._done += 1
        self._hits += hits_delta
        if self._cb is None:
            return
        try:  # best-effort：显示失败绝不影响扫描
            await self._cb(ProgressSample(
                self._phase, self._done, self._total, self._hits, detail))
        except Exception:
            pass

    async def finalize(self, summary_detail: str) -> None:
        if self._cb is None:
            return
        try:
            await self._cb(ProgressSample(
                self._phase, self._done, self._total, self._hits,
                summary_detail, final=True))
        except Exception:
            pass
```

### 6.2 display 层新增 `GitnexusLlmEvent`（`display/events.py`）

```python
@dataclass(frozen=True)
class GitnexusLlmEvent(DisplayEvent):
    """GitNexus 轨 LLM 环节的进度行 —— 与 LLM 轨 LlmTurnEvent 对偶：
    LLM 轨是单个 agent 的 turn 流，GitNexus 轨是批量函数/候选的并发判定。
    专属标签 GN-LLM 便于 grep 所有 LLM 活动。"""
    phase: str
    kind: Literal["progress", "hit", "summary"]
    done: int
    total: int
    hits: int
    detail: str | None = None
```

### 6.3 renderer（`file_renderer.py` 加 `_gitnexus`，`rich_renderer.py` 对偶）

`render()` 的 match 加 `case GitnexusLlmEvent(): await self._writer.write(self._gitnexus(e))`：

```python
def _gitnexus(self, e) -> str:
    tag = "[GN-LLM]"
    if e.kind == "hit":
        return f"[{e.timestamp}] {tag} {e.phase}  ✓ {e.detail}\n"
    if e.kind == "summary":
        return f"[{e.timestamp}] {tag} {e.phase}  done {e.done}/{e.total} → {e.detail}\n"
    # progress
    noun = _HITS_NOUN.get(e.phase, "hits")   # sink-discovery→"sinks", chain-verdict→"vulnerable"…
    return f"[{e.timestamp}] {tag} {e.phase}  {e.done}/{e.total}  · {e.hits} {noun} so far\n"
```

`GN-LLM` 标签按 `log-format-redesign` 的 pad_rule 对齐（与 `[LLM]`/`[TOOL]`/`[INFO]` 同宽，在 `symbols.py`/`formatters.py::tag` 注册）。

### 6.4 audit 层转发

`workflow_logger.py` 加：

```python
async def log_gitnexus_progress(self, phase, kind, done, total, hits, detail=None):
    if self._dispatcher is None:
        return
    await self._dispatcher.dispatch(GitnexusLlmEvent(
        timestamp=format_log_time(), category="GN-LLM", phase=phase, kind=kind,
        done=done, total=total, hits=hits, detail=detail))
```

`audit/session.py` + `session_registry.py`（Protocol）加同名转发方法（对齐现有 `log_llm_response`/`log_tool_start` 的 session→workflow_logger 转发模式）。

### 6.5 各环节接线（透传 + emitter）

- `discover_sinks_llm(suspicious, llm_client, *, progress_cb=None)`：内部 `emitter = ProgressEmitter("sink-discovery", len(by_func), progress_cb)`；`_discover_one` 末尾 `await emitter.tick(detail=hit_detail, hits_delta=len(out))`；末尾 `await emitter.finalize(f"{hits} soft sinks · {gaps} rule gaps · {skipped} timeouts")`。
- `discover_sources_llm`：对称（phase=`source-discovery`，hits=source 数）。
- `analyze_taint_llm` 调用处（`code_index/__init__.py` 的 `_taint_one`）：每函数 `emitter.tick(detail=taint_flow_desc, hits_delta=<该函数产出的 taint_flow 计数>)`（phase=`taint-analysis`；计数取值与 `detail` 文案的具体字段名实现时按 `analyze_taint_llm` 实际返回结构定，本 spec 不臆测）。
- `build_{injection,xss,ssrf}_findings(pgraph, *, llm_client, progress_cb=None)`：候选链 for 循环里每条 `judge_chain_verdict` 后 `emitter.tick(detail=vuln_line_if_vulnerable, hits_delta=1 if verdict=="vulnerable" else 0)`（phase=`chain-verdict`）；末尾 `finalize`。
- `build_code_index_with_gitnexus(..., progress_cb=None)`：透传给 sink/source/taint 三个调用点。
- builder 调度处：透传 `progress_cb` 给三个 builder。
- authz：`run_authz_gitnexus_judge` 内 IDOR 候选判定循环 `emitter.tick(...)`（phase=`authz-judge`，hits=confirmed IDOR）。

### 6.6 activity 层包装（`whitebox/pipeline/activities.py`）

三个 activity 各自构造 `progress_cb`，封装**采样 + 转发**：

```python
def _make_gitnexus_progress_cb(phase, session):
    async def cb(sample: ProgressSample):
        # 采样：summary 必发；hit（detail 非空）必发；progress 仅 done==1 或 done%10==0
        if sample.final:
            kind, detail = "summary", sample.detail
        elif sample.detail:
            kind, detail = "hit", sample.detail
        elif sample.done == 1 or sample.done % 10 == 0:
            kind, detail = "progress", None
        else:
            return  # 未命中且非采样点 → 静默，零开销
        try:
            await session.log_gitnexus_progress(phase, kind, sample.done,
                                                sample.total, sample.hits, detail)
        except Exception:
            pass  # best-effort
    return cb
```

## 7. 日志格式样例

```
[14:32:05] [GN-LLM] sink-discovery  1/87   · 0 sinks so far
[14:32:11] [GN-LLM] sink-discovery  ✓ 'pg.executeQuery' @ src/api/users.py:42 slot=args
[14:32:38] [GN-LLM] sink-discovery  ✓ 'requests.post' @ src/svc/notify.py:88 slot=url
[14:32:40] [GN-LLM] sink-discovery  10/87  · 2 sinks so far
[14:33:15] [GN-LLM] sink-discovery  20/87  · 5 sinks so far
[14:35:20] [GN-LLM] sink-discovery  done 87/87 → 12 soft sinks · 5 rule gaps · 2 timeouts

[14:35:30] [GN-LLM] chain-verdict   1/34   · 0 vulnerable so far
[14:35:35] [GN-LLM] chain-verdict   ✓ INJ-GN-03 vulnerable: source=userId → sink=executeQuery
[14:35:50] [GN-LLM] chain-verdict   10/34  · 2 vulnerable so far
[14:36:10] [GN-LLM] chain-verdict   done 34/34 → 5 vulnerable · 4.2s/chain avg
```

grep 友好：`grep GN-LLM` = 所有 GitNexus 轨 LLM 活动；`grep 'sink-discovery'`/`grep 'chain-verdict'` 按 phase 细分；`grep '✓'` 只看命中。

## 8. 采样规则（K=10 固定）

| kind | 触发条件 |
|------|---------|
| `progress` | `done == 1`（尽早确认"开始了"）或 `done % 10 == 0` |
| `hit` | 每次命中（`detail` 非空）即时打 |
| `summary` | 环节 `finalize` 时一次（含总耗时、命中数、超时/跳过数） |

未命中单位只递增计数、不发 hit 行。K=10 不开 env（YAGNI）。

## 9. 错误处理 / 边界

- **三层静默防护**：① `ProgressEmitter.tick` 内 `try/except` 吞 cb 异常；② session/workflow_logger 内部 `if dispatcher is None: return`；③ activity 包装里采样不满足直接 return（不构造 event、不调 session）。
- **`progress_cb=None` 全程跳过**：测试 / 未注入 / `SHANNON_GITNEXUS_LLM_ENABLED=0` 时。
- **超时/失败单位**：`map_llm_with_bounds` 已有降级（跳过+warning）不变；emitter 仍对它们 `done++`，`summary` 体现"X timeouts/skipped"。
- **并发计数安全**：asyncio 单线程，`tick` 内 `done+=1`/`hits+=Δ` 在 `await cb` 之前完成，无跨 await 竞态。

## 10. 测试策略

| 层次 | 覆盖点 |
|------|--------|
| `code_index/test_progress.py` 🆕 | emitter 计数递增；tick 样本内容；finalize 发 summary；`cb=None` no-op；`cb raise` 被吞；并发 tick（gather）不丢数 |
| `display/test_events*.py` | `GitnexusLlmEvent` frozen 构造 |
| `display/test_file_renderer.py` | `_gitnexus` 三态（progress/hit/summary）输出格式快照 |
| `display/test_rich_renderer.py` | 对应分支渲染（轻测） |
| `test_{sink,source}_discovery_llm.py` | mock cb：tick 次数=函数数、命中样本带 detail、未命中只计数 |
| `chain_verdict` builders 测试 | mock cb：每条候选 tick、vulnerable 带 detail（含 INJ-GN-NN id） |
| whitebox activity 测试 | mock session：`done==1`/`done%10==0`/hit/summary 才触发 `log_gitnexus_progress` |
| 采样单测 | done=1..25，断言只在 1/10/20/hit/summary 触发 |

## 11. 各环节 `hits` 语义

| phase | 单位 | hits 语义 |
|-------|------|-----------|
| sink-discovery | 含可疑 call 的函数 | soft sink 数 |
| source-discovery | 候选 source 函数 | source 数 |
| taint-analysis | 有 sink 的函数 | taint_flow 数 |
| chain-verdict | 候选链 | `verdict=="vulnerable"` 数 |
| authz-judge | IDOR 候选 | confirmed IDOR 数 |

## 12. 完整改动文件清单

```
core:
  🆕 code_index/progress.py              ProgressSample + ProgressEmitter
  ✏  display/events.py                   + GitnexusLlmEvent
  ✏  display/{symbols,formatters}.py     + GN-LLM 标签（对齐 pad_rule）
  ✏  display/file_renderer.py            + _gitnexus 分支
  ✏  display/rich_renderer.py            + 对应分支
  ✏  audit/workflow_logger.py            + log_gitnexus_progress
  ✏  audit/{session,session_registry}.py + log_gitnexus_progress 转发
  ✏  code_index/__init__.py              build_code_index_with_gitnexus +progress_cb 透传
  ✏  code_index/sink_discovery_llm.py    +progress_cb + emitter
  ✏  code_index/source_discovery_llm.py  +progress_cb + emitter（对称）
  ✏  code_index/llm_taint_analyzer 调用  _taint_one +emitter.tick
  ✏  code_index/vuln_chain_builders/{injection,xss,ssrf}_builder.py  +progress_cb + emitter
  ✏  builder 调度处                       透传 cb 给三 builder
whitebox:
  ✏  pipeline/activities.py              run_code_index / run_gitnexus_chain_verdict / run_authz_gitnexus_judge
                                          各构造 progress_cb（采样 + 包装 session.log_gitnexus_progress）
tests:
  🆕 core/tests/code_index/test_progress.py
  ✏  core/tests/display/test_file_renderer.py
  ✏  core/tests/code_index/test_{sink,source}_discovery_llm.py
  ✏  whitebox activity 注入用例
```

## 13. 非目标 / YAGNI / 未来

- 采样 K 不 env 化（YAGNI）；如大仓实测后发现 K=10 不合适，再加 `SHANNON_PROGRESS_EVERY`。
- 不改 `map_llm_with_bounds` 签名。
- 未来可考虑：把 `chain_verdict` 改并发（走 `map_llm_with_bounds`）时，进度天然复用本 spec 的 emitter。
