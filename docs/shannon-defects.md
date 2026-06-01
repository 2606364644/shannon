# Shannon 白盒代码审计缺陷分析

> **核心结论**：Shannon 白盒审计在漏洞分析方法论深度上设计优秀（Slot Type 理论、Guard Dominance、Render Context 匹配），但在代码覆盖度可证明性和调用链完整性上存在结构性缺陷。根本原因是：代码发现、调用链追踪、参数枚举全部依赖 LLM 推理，缺乏确定性工具（如 AST 解析、调用图构建）作为基础层。

---

## 一、缺陷总览

| 编号 | 缺陷类别 | 严重程度 | 根因 | 影响 |
|-|-|-|-|-|
| D1 | 无完整调用链构建 | 高 | 无 AST / 调用图 | 深层间接调用可能被遗漏 |
| D2 | 参数覆盖度不可证明 | 高 | 无函数块提取 | 不知道"漏了多少参数" |
| D3 | 三层 LLM 信息衰减 | 高 | PRE_RECON → RECON → Vuln 三次传递 | 早期遗漏向后传播且不可恢复 |
| D4 | 错误状态覆盖 | 中 | state.error 是单值 str | 多个 agent 失败时只保留最后一个错误 |
| D5 | Vuln Agent 无重试策略 | 中 | 无 retry_policy | 网络抖动可能导致某类漏洞完全跳过 |
| D6 | 并行 Git 竞争风险 | 中 | 6 agent 共享 repo 目录 | git add -A && commit 可能冲突 |
| D7 | Misconfig Agent 不一致 | 低 | prompt 风格与其他 5 个差异大 | 可能为半成品或后加入的模块 |

---

## 二、D1：无完整调用链构建

**问题**：Shannon 没有 AST 解析、函数提取或调用图构建步骤。调用链的发现完全依赖 LLM Agent 自行探索代码。

### 2.1 Shannon 的调用链发现路径

Shannon 通过三个阶段"间接"获得调用链信息：

1. **PRE_RECON Agent**：用 6 个 Task Agent 并行扫描代码，输出 pre_recon_deliverable.md（含入口点列表、sink 列表）
2. **RECON Agent**：基于 PRE_RECON 结果 + 浏览器探索，输出 recon_deliverable.md（含 API 端点、输入向量）
3. **Vuln Agent**：基于 RECON 结果，用 Task Agent 自行追踪代码路径

每一步都是 LLM "尽力而为"，没有确定性保证。

### 2.2 与 SCR-AI 的对比

| 维度 | Shannon（依赖 LLM 推理） | SCR-AI（确定性工具） |
|-|-|-|
| AST 解析 | 无 | tree-sitter 提取所有函数块 |
| 函数列表 | 无完整列表 | 入口点检测分类每个函数 |
| 调用图 | 无 | BFS 构建调用图（深度 15/宽度 50） |
| 覆盖保证 | 无法证明"所有入口点都被发现" | 每条链都被审计到，覆盖度可量化 |

### 2.3 具体风险场景

```python
# routes/order.py
@router.delete("/orders/{id}")
def delete_order(id):
    return svc.cancel(id)

# services/order.py
def cancel_order(order_id):
    sql = "UPDATE orders SET status=cancelled WHERE id=" + order_id
    return db.raw_query(sql)

# utils/db.py
def raw_query(sql):
    cursor.execute(sql)  # ← SQL 注入 sink
```

- **SCR-AI**：tree-sitter 提取 3 个函数块 → BFS 追踪 delete_order → cancel_order → raw_query → 完整链路
- **Shannon**：需要 PRE_RECON 发现 delete_order，RECON 追踪到 cancel_order，Injection Agent 追踪到 raw_query。3 次 LLM 推理，任何一次遗漏都可能导致 sink 永远不被发现。

---

## 三、D2：参数覆盖度不可证明

**问题**：Shannon 没有"已审计参数 / 总参数"的度量。无法证明每个入口点的每个参数都被分析到。

### 3.1 致命的遗漏传播链

> PRE_RECON 漏掉参数 X → RECON 的 §9 也没有参数 X → Injection Agent 的 TodoWrite 没有 X 的任务 → 参数 X 永远不被审计 → **且没有任何机制能发现这个遗漏**

### 3.2 Shannon 的补偿机制及其局限

| 补偿机制 | 说明 | 局限 |
|-|-|-|
| Prompt 说 "Thoroughness is Non-Negotiable" | 约束 Agent 不要提前结束 | 只是 prompt engineering，不是结构保证；LLM 可能受 token 限制跳过内容 |
| 6 个 Task Agent 并行扫描 | 增加覆盖面 | 子进程也有 context 限制；无法证明覆盖度 |
| Vuln Agent 可自行探索代码 | 不局限于 recon.md 内容 | Agent 不知道"还有什么没看过" |
| TodoWrite 追踪进度 | 列出分析任务 | 只追踪已知任务，不会提示"有 30 个参数但只发现了 20 个" |

### 3.3 SCR-AI 的确定性基础

SCR-AI 通过 tree-sitter 提取 `FuncBlock` 数据结构，每个函数块的 `parameters` 字段列出所有参数。入口点检测后，BFS 调用链包含每层参数传递。覆盖度可度量：`total_blocks=N, entry_points=M, chains=K`，每个数字都是确定的。

---

## 四、D3：三层 LLM 信息衰减

**问题**：源代码的信息经过 PRE_RECON → RECON → Vuln Agent 三次 LLM 传递，每次都可能丢失信息。

### 4.1 信息衰减可视化

```text
源代码
  │
  ├─(LLM 理解)──▶ pre_recon_deliverable.md    ← 信息损失 #1
  │                     │
  │                (LLM 理解)──▶ recon_deliverable.md  ← 信息损失 #2
  │                                       │
  │                                  (LLM 理解)──▶ Vuln Agent  ← 信息损失 #3
  │                                                     │
  └───────────────────────────────────────────────▶ 最终审计结果
                                                     (遗漏无人知)
```

### 4.2 关键代码位置

- PRE_RECON 写入：`prompts/pre-recon-code.txt`（417 行 prompt）
- RECON 读取并写入：`prompts/recon.txt`（392 行 prompt）
- Vuln Agent 读取：`prompts/vuln-injection.txt`（373 行 prompt）等

每一步的 prompt 都说"以 XX deliverable 为唯一真相源"，但真相源本身可能不完整。

### 4.3 对比 SCR-AI 的单层传递

SCR-AI 的信息流：`源代码 ──(tree-sitter)──▶ 函数块 ──(BFS)──▶ 调用链 ──(LLM)──▶ 审计结果`

LLM 只在最后一步介入，只负责"判断是否是漏洞"，前面所有步骤都是确定性的。

---

## 五、D4-D7：工程层缺陷

### 5.1 D4：错误状态覆盖

代码位置：`packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:117`

```python
for i, result in enumerate(results):
    vt = selected_classes[i]
    agent_name = AgentName(f"{vt}-vuln")
    if isinstance(result, Exception):
        self._state.error = f"{agent_name.value}: {result}"
        # ↑ 单值 str，多次失败只保留最后一个
```

**影响**：如果 injection-vuln 和 xss-vuln 都失败，只看到 xss-vuln 的错误信息。

### 5.2 D5：Vuln Agent 无重试策略

PRE_RECON 有 `RetryPolicy(maximum_attempts=50, ...)`，但 Phase 5 的 vuln agents 完全没有 retry_policy：

```python
vuln_tasks.append(
    workflow.execute_activity(
        activities.run_vuln_agent, vuln_input,
        start_to_close_timeout=timedelta(hours=2),
        # ← 没有 retry_policy
    )
)
```

**影响**：一次网络超时就可能导致某类漏洞（如 auth）完全不被分析。

### 5.3 D6：并行 Git 竞争风险

6 个 vuln agent 并行运行，都往同一个 repo 的 `.shannon/deliverables/` 写文件。每个 agent 执行时都调用 `GitManager.create_checkpoint()`（git add -A && git commit）和 `GitManager.commit()`。

多个 agent 同时执行 `git add -A` 可能导致：一个 agent 的 deliverable 被另一个 agent 的 commit 包含，或者 index.lock 冲突。

### 5.4 D7：Misconfig Agent 不一致

| 维度 | vuln-misconfig.txt（89 行） | 其他 5 个 vuln prompt（290-370 行） |
|-|-|-|
| TodoWrite 追踪 | 无 | 有任务管理 |
| conclusion_trigger | 无 | 有严格触发条件 |
| 共享 session 引用 | 无 | 有 |
| 入口点引用机制 | 无 | 有 pre_recon 章节引用 |
| false_positives_to_avoid | 无 | 有详细列表 |

**推断**：misconfig-vuln 可能是后来加入的模块，尚未按其他 5 个 agent 的标准对齐。

---

## 六、根因分析

> **所有缺陷的共同根源**：Shannon 把"发现什么代码需要审计"和"审计代码"都交给了 LLM，但 LLM 无法证明自己没有遗漏。代码发现是确定性问题（每个函数都在/不在），不应该依赖概率性的 LLM 推理。

| 维度 | Shannon 的职责划分 | SCR-AI 的职责划分 |
|-|-|-|
| 确定性工具 | 无 | 代码提取 + 调用链构建 |
| LLM | 代码发现 + 调用链追踪 + 漏洞判断 | 漏洞判断（最后一步） |

---

## 七、改进建议

> **核心思路**：在 PRE_RECON 之前加入确定性基础层

1. **加入 tree-sitter 函数提取**：在 Phase 1 之前，用 AST 解析提取所有函数块（含参数、装饰器、源码位置），生成结构化函数清单
2. **加入 BFS 调用图构建**：基于函数清单，构建完整的调用图（或利用 GitNexus），确保每条调用链被追踪到
3. **覆盖度度量**：输出 total_functions / analyzed_functions 比率，让系统知道"还有多少没看"
4. **修复 state.error 为 list**：`PipelineState.errors: list[str]` 收集所有失败
5. **为 Vuln Agent 添加 RetryPolicy**：至少 3 次重试
6. **Git 并行隔离**：每个 agent 使用独立的 git worktree 或纯文件写入替代 git commit
7. **对齐 misconfig prompt**：按其他 5 个 agent 的标准重写

### 7.1 理想的融合架构

```text
┌──────────────────────────────────────────────────────┐
│ Phase 0: 确定性基础层 (新增)                          │
│  ├─ tree-sitter 提取所有函数块 + 参数                │
│  ├─ BFS 构建完整调用图                               │
│  └─ 输出: functions.json + call_graph.json           │
├──────────────────────────────────────────────────────┤
│ Phase 1: PRE_RECON (保留)                            │
│  ├─ 基于函数清单进行语义分析                         │
│  └─ 输出: pre_recon_deliverable.md                   │
├──────────────────────────────────────────────────────┤
│ Phase 2: RECON (保留)                                │
│  ├─ 基于调用图 + PRE_RECON 结果做攻击面映射          │
│  └─ 输出: recon_deliverable.md                       │
├──────────────────────────────────────────────────────┤
│ Phase 3: Vuln Analysis (保留)                        │
│  ├─ 6 个 agent 并行，但每个 agent 可参考调用图       │
│  ├─ 覆盖度度量: "已分析 K/N 条调用链"                │
│  └─ 输出: deliverables + queue JSON                  │
└──────────────────────────────────────────────────────┘
```

> **关键洞察**：确定性工具保证**不遗漏**（覆盖度），LLM 保证**深度分析**（准确度）。两者结合才能实现既完整又深入的白盒审计。

---

*来源：[飞书文档](https://futu.feishu.cn/wiki/BsMLwZqbiiY4WvkSpPmcxcQ6nxg)*
