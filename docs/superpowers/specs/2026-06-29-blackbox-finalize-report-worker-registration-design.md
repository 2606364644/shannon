# 黑盒 finalize_report 漏注册修复 + activity 注册完整性护栏

> 日期 2026-06-29 ｜ 分支 `feat/fork-py` ｜ 状态 design（待 review）

## 1. 背景

2026-06-29 对 NodeGoat 跑黑盒 `--rerun` 扫描（`NodeGoat_20260628-023125`），reporting 阶段
`report` agent 在 01:00:39 Completed 后，程序**卡住约 14 分钟不退出**，footer 一直显示
`reporting · 2 done`。用户疑问「为什么 report 完成了还在 reporting、程序还在运行吗」。

## 2. 根因

### 2.1 直接根因：`finalize_report` activity 漏注册到 worker

黑盒 reporting phase 实际有 3 步（见 `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:391-421`）：

| 步骤 | activity | 状态 |
|---|---|---|
| ① | `assemble_report`（组装各 vuln 产物） | ✓ |
| ② | `run_report_agent`（日志里的 "report"） | ✓ Completed 01:00:39 |
| ③ | `finalize_report`（注入模型信息 + NoOp 收尾） | ✗ **失败重试中** |

`finalize_report` 在 `pipeline/activities.py:386` 有 `@activity.defn` 定义、在 workflow `run()` 内被
`workflow.execute_activity(activities.finalize_report, ...)` 调用，**但未在
`worker.py` 的 `Worker(..., activities=[...])` 列表里注册**（import 段也缺）。

证据链：
- `activity_failures.log`：`NotFoundError: Activity function finalize_report ... is not registered
  on this worker, available activities: assemble_report, ...（无 finalize_report）`
- AST 比对：blackbox `@activity.defn` 定义 **12 个**，worker 注册 **11 个**，差的就是 `finalize_report`
- `git log -S finalize_report -- worker.py` 为空 → 该 activity **从未被注册过**

temporal 找不到 activity → 抛 `ApplicationError` → 按 retry policy 重试。失败间隔
`01:00:39 → 01:05:39 → 01:15:39`（5→10min 指数退避）。

### 2.2 为什么没被拦住：blackbox worker 缺 activity 注册完整性测试

这是该 bug 第 **3** 次复发（前两次：`assemble_report`、`authz judge`）。memory
`temporalio-activity-worker-registration` 的既定对策是「每加 activity 配 count≥2 anchor
test」。但 `packages/blackbox/tests/test_worker.py` 现有 5 个测试（task queue 前缀 / 失败摘要 /
cancel / rerun 归档 / 自建 session），**没有一个断言 activity 注册完整性**。故 `finalize_report`
漏注册**零拦截**地溜进真机。

根因不是「护栏没更新」，而是**这个护栏在 blackbox 根本不存在**；且即便存在，「硬编码 count ≥ N」
也脆弱——新增 activity 不改 N 则新漏照漏。

### 2.3 为什么卡这么久

`finalize_report` 用生产 retry policy（`packages/core/src/shannon_core/models/retry.py:29-33`
`PRODUCTION_RETRY`）：`maximum_attempts=50`、`initial_interval=5min`、`maximum_interval=30min`、
`backoff_coefficient=2.0`。重试序列 5→10→20→30(封顶)…，总计约 **23~24 小时**才会最终失败退出。
即「等下去无意义」。

## 3. 设计

修复分两层：**修当前 bug**（worker.py 补注册）+ **补缺失的护栏**（集合相等断言，黑白盒共享）。

### 3.1 Bug 修复 — `packages/blackbox/src/shannon_blackbox/worker.py`

- import 段（`from .pipeline.activities import (...)`）加 `finalize_report`
- `Worker(..., activities=[...])` 列表加 `finalize_report`（11 → 12）

### 3.2 集合相等护栏 helper — core 新增

新模块 `packages/core/src/shannon_core/testing/activity_registration.py`（新建 `testing/` 子包；
跨包测试经 `shannon_core.testing.activity_registration` import；放 src 以便其他包 import）。

```python
def assert_all_activities_registered(worker_module, activities_modules) -> None:
    """断言 worker 注册的 activity 集合 == activities 模块定义的 @activity.defn 集合。

    用 AST 解析各模块 __file__ 源码（不依赖运行 import、不连 temporal）：
      expected   = ∪ activities_modules 里所有 @activity.defn 装饰的函数名
      registered = worker_module 源码里 Worker(..., activities=[...]) 的 Name 列表
    registered != expected 时报 missing / extra diff（pytest assertion 友好）。
    """
```

设计要点：
- AST 解析而非 import 反射：`activities=[...]` 是 `run_scan` 函数体内的局部变量，import worker
  拿不到；AST 解析源码既稳又不触发副作用（worker 顶层只有 import + def）。
- 接收模块对象（用 `__file__` 定位源码），重构友好；`activities_modules` 为 list 支持定义分散在
  多模块的场景（当前 blackbox/whitebox 各自单模块，但接口留余地）。
- 既抓「漏注册」(missing) 也抓「幽灵注册」(extra，注册了已删的 activity)。

helper 自带 core 单测（`packages/core/tests/test_activity_registration.py`，顶层平铺，对齐
`test_paths.py`/`test_progress.py` 等 utils 测试惯例）：用合成最小 worker/activities 源码字符串
验证三种情形——相等通过、missing 报错、extra 报错。

### 3.3 blackbox 护栏测试

`packages/blackbox/tests/test_worker.py` 加：

```python
def test_all_activities_registered():
    from shannon_core.testing.activity_registration import assert_all_activities_registered
    from shannon_blackbox import worker
    from shannon_blackbox.pipeline import activities
    assert_all_activities_registered(worker, [activities])
```

修复前 **fail（missing finalize_report）**，修复后 green。这就是缺了 3 次的护栏。

### 3.4 whitebox 护栏测试（复用同一 helper）

`packages/whitebox/tests/test_worker.py` 加同款测试。whitebox 当前 23/23 全齐，测试**应直接
pass**，作为防未来漏注册的保护（无行为变更，纯护栏）。

### 3.5 TDD 顺序

对齐 `superpowers:test-driven-development`：

1. 加 blackbox `test_all_activities_registered` → 跑，确认 **fail**（证明护栏能抓到 bug）
2. 修 `worker.py`（import + 注册 `finalize_report`）→ 跑，转 **green**
3. 加 core helper 单测（missing/extra/相等三情形）→ green
4. 加 whitebox 护栏测试 → green

### 3.6 测试范围

只跑改动相关测试，**不跑全套**（memory `pytest-whitebox-hang`：全套会 hang）：
- `packages/blackbox/tests/test_worker.py`
- `packages/whitebox/tests/test_worker.py`
- core helper 新测试

## 4. 善后：卡住的旧 workflow

当前 `NodeGoat_20260628-023125-rerun-20260629-003708` 仍在后台重试。黑盒 worker 与 client 同进程
（`run_scan` 内），**Ctrl+C 杀进程**即停掉该 worker；worker 消失后 activity 无法执行，旧 workflow
最终因 retry 耗尽而失败。重跑会用新 `run_ts` 起新 workflow id（`<base>-rerun-<新ts>`），与旧的互不
干扰。重跑前确认旧进程已退出即可，无需额外清理。

> 报告其实已生成：`comprehensive_security_assessment_report.md`（54KB，01:00 写好）。`finalize_report`
> 只做「注入模型信息」+ NoOp 输出，不影响报告主体，本次扫描产物可用。

## 5. 非目标（out of scope）

- 不改生产 retry policy（50 次重试是另一议题，与本次 bug 无关）。
- 不改 reporting phase 的 3 步结构。
- 不引入「worker 自动收集 @activity.defn」的结构性方案（评估过，blackbox 单 worker 全注册场景
  收益有限，且改变注册语义、评审成本高；集合相等护栏已足够堵死漏注册）。
- 不追溯修复 blackbox test_worker.py 历史上缺失的其他护栏（本次只补 activity 注册完整性）。

## 6. 验收标准

- [ ] `worker.py` 注册 `finalize_report`；blackbox `test_all_activities_registered` pass
- [ ] core `assert_all_activities_registered` helper + 单测（missing/extra/相等）pass
- [ ] whitebox `test_all_activities_registered` pass（23/23 不变）
- [ ] 真机：黑盒 `--rerun` reporting 阶段 `finalize_report` 正常执行，workflow 正常 `return` 退出，
      不再 `NotFoundError` 重试（人工冒烟）
