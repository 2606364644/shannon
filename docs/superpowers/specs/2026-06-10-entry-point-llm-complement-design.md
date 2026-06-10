# 入口点识别优化：确定性 + LLM 独立扫描 + Fusion

> 优化重构项目的入口点识别，从"纯确定性"升级为"确定性做精度 + LLM 做全面独立扫描 + Fusion 合并去重"。

**日期**：2026-06-10

---

## 1. 背景

### 1.1 当前问题

重构项目的入口点识别存在两个问题：

1. **确定性层覆盖窄**：`entry_points.py` 通过 AST 模式匹配覆盖 ≥8 框架，但无法检测 API Schema 文件、Webhook、文件上传、未知框架、配置文件路由、动态路由注册
2. **LLM 层断路**：pre-recon prompt 中的 Entry Point Mapper 子 agent 被削弱为"补充角色"（仅找确定性漏掉的），且 Phase 0 裁定依赖不存在的 `save-deliverable --type ENTRY_POINTS` 工具

### 1.2 设计原则

- **确定性做精度**：可复现、可单测、零 token、毫秒级
- **LLM 做召回**：全面独立扫描、智能搜索策略、覆盖确定性无法检测的类别
- **两层完全独立**：LLM 不看确定性结果，确定性不依赖 LLM
- **Fusion 合并去重**：确定性优先，LLM 独有发现保留（降权）
- **最小改动**：激活已有代码 + prompt 修改，不重新设计架构

### 1.3 目标

达到或超过原始项目 Phase 1 并行子 agent 的入口点识别能力。

---

## 2. 架构

```
                  ┌─────────────────────────┐
                  │   确定性层（现有，不变）    │
                  │   entry_points.py        │
                  │   AST 模式匹配           │
                  │   覆盖 ≥8 框架           │
                  │   产出：EntryPoint[]      │
                  │   confidence: 0.30-0.95  │
                  └──────────┬──────────────┘
                             │
                             │ （两条线完全独立运行）
                             │
                  ┌──────────┴──────────────┐
                  │   LLM 层（改动）          │
                  │   pre-recon Phase 1       │
                  │   Entry Point Mapper      │
                  │   全面独立扫描            │
                  │   不看确定性结果          │
                  │   产出：写入 deliverable  │
                  └──────────┬──────────────┘
                             │
                             ▼
                  ┌─────────────────────────┐
                  │   Fusion 层（激活）       │
                  │   entry_point_fusion.py  │
                  │   1. parse deliverable   │
                  │      → EntryPoint[]      │
                  │   2. merge_entry_points  │
                  │      去重：file:name     │
                  │      确定性优先           │
                  │   3. save_adjudication   │
                  │      按 confidence 裁定  │
                  └──────────┬──────────────┘
                             │
                             ▼
                     entry_points.json
                   （合并+裁定后的最终产物）
```

---

## 3. 改动详情

### 3.1 改动 A：升级 Entry Point Mapper prompt

**文件**：`prompts/pre-recon-code.txt`

**改动位置**：Phase 1 中的 Entry Point Mapper Agent 指令（约 line 152-153）

**当前 prompt**：

> "Find entry points that the deterministic code_index may have MISSED. Focus on patterns the AST analysis cannot detect: configuration-file routes (e.g., urls.py, routes.yaml, router configs), dynamic route registration, unknown framework conventions, and implicitly registered handlers. Do NOT report entry points already detected by code_index — only report genuinely new discoveries."

**替换为**（参考原始项目，全面独立扫描）：

> "Find ALL network-accessible entry points in the codebase. Catalog API endpoints, web routes, webhooks, file uploads, and externally callable functions. Also identify and catalog API schema files that document these endpoints (OpenAPI/Swagger \*.json/\*.yaml/\*.yml, GraphQL \*.graphql/\*.gql, JSON Schema \*.schema.json, Protocol Buffers \*.proto). Distinguish between public endpoints and those requiring authentication. Exclude local-only development tools, CLI scripts, and build processes. Provide exact file paths and route definitions for both endpoints and schemas."

**关键变化**：
- 从"找确定性漏掉的"→"找所有入口点"（全面独立扫描）
- 明确覆盖：API Schema、Webhook、文件上传
- 明确要求：认证标注（public/auth）、网络可达性过滤
- 不提及 code_index，不受确定性层影响

### 3.2 改动 B：简化 Phase 0 入口点裁定

**文件**：`prompts/pre-recon-code.txt`

**改动位置**：Phase 0: Entry Point Adjudication（约 line 114-143）

**当前问题**：Phase 0 要求用 `save-deliverable --type ENTRY_POINTS` 写 `entry_points.json`，但该工具在代码库中不存在。如果 LLM 严格遵循流程，会在 Phase 0 卡住不进入 Phase 1。

**改动**：将 Phase 0 中关于入口点裁定的部分改为**可选**，不阻塞 Phase 1：

- 移除 Phase 0 对 `save-deliverable --type ENTRY_POINTS` 的硬性要求
- 改为：Phase 0 审查 code_index 的调用链统计和文件覆盖数据（保留 `<phase0_data>` 中的非入口点数据），入口点的裁定由下游 fusion 层完成
- Phase 1 Entry Point Mapper 独立运行，不受 Phase 0 入口点数据影响

**具体改动**：

1. 从 Phase 0 指令中移除入口点裁定步骤（step 1-4）
2. 保留 Phase 0 对调用链统计和文件覆盖数据的审查
3. 移除 `entry_points.json` 的生成要求
4. 在 `<phase0_data>` 中保留 Call Chain Statistics 和 File Coverage，移除 Entry Points 表格（避免影响 LLM 独立扫描）

### 3.3 改动 C：激活 entry_point_fusion.py

**文件**：`packages/core/src/shannon_core/code_index/entry_point_fusion.py`

**当前状态**：已实现三源合并逻辑（GitNexus > Schema > Framework），但 `build_code_index` 从不调用。

**改动**：

#### C.1 新增 LLM 结果解析函数

在 `entry_point_fusion.py` 中新增：

```python
def parse_llm_entry_points(deliverable_text: str) -> list[EntryPoint]:
    """从 pre_recon_deliverable.md 的 Attack Surface Analysis section
    中提取 LLM 发现的入口点，映射为 EntryPoint 对象。"""
```

- 解析 deliverable 中的入口点 section（Section 5: Attack Surface Analysis）
- 用正则匹配文件路径、函数名、路由、HTTP 方法等
- 映射到 `EntryPoint` 模型：
  - `confidence=0.60`（LLM 发现默认低于确定性）
  - `source=LLM_PRE_RECON`
  - `needs_llm_review=False`（LLM 已经审查过）
- 解析失败的入口点静默跳过，不影响确定性结果

#### C.2 扩展融合逻辑

`merge_entry_points` 增加第四个来源 `LLM_PRE_RECON`：

- 去重 key：`{file_path}:{function_name}`
- 优先级：确定性（confidence ≥ 0.85）> GitNexus > Schema > LLM_PRE_RECON
- LLM 独有的入口点保留，confidence 保持 0.60
- 确定性和 LLM 都找到的同一个入口点：取确定性版本（confidence 更高、字段更精确），但可以从 LLM 版本补充 `authentication` 标注（如果确定性版本缺失）

#### C.3 接线到管线

在 `__init__.py` 中新增函数 `run_entry_point_fusion`：

```python
def run_entry_point_fusion(
    code_index: CodeIndex,
    deliverable_path: str,
) -> CodeIndex:
    """合并确定性入口点与 LLM 入口点。"""
```

- 读取 `code_index.json` 获取确定性 `EntryPoint[]`
- 读取 `pre_recon_deliverable.md`，调用 `parse_llm_entry_points`
- 调用 `merge_entry_points` 合并
- 更新 `code_index.json` 的 `entry_points` 字段

### 3.4 改动 D：修复 save_adjudication

**文件**：`packages/core/src/shannon_core/code_index/__init__.py`

**当前**：无条件全置 `verdict=CONFIRMED, source=CODE_INDEX`。

**改为**：

```python
for ep in merged_entry_points:
    if ep.confidence >= 0.85:
        verdict = "CONFIRMED"
    elif ep.confidence < 0.50:
        verdict = "REJECTED"
    else:
        verdict = "NEEDS_REVIEW"
    adjudicated.append(...)
```

### 3.5 改动 E：管线编排

**文件**：`packages/core/src/shannon_core/workflows.py`（或对应管线文件）

在 PRE_RECON 之后、RECON 之前插入 fusion 步骤：

```
当前：
  run_code_index → run_agent(PRE_RECON) → run_save_adjudication → run_rebuild_call_chains → run_agent(RECON)

改为：
  run_code_index → run_agent(PRE_RECON) → run_entry_point_fusion → run_save_adjudication → run_rebuild_call_chains → run_agent(RECON)
```

`run_entry_point_fusion` 必须在 PRE_RECON 之后（需要 deliverable）且在 RECON 之前（RECON 需要完整入口点列表）。

---

## 4. 数据模型

### 4.1 EntryPoint 扩展

当前 `EntryPoint` 模型没有 `authentication` 和 `source` 字段。本次改动需要扩展：

```python
class EntryPoint(BaseModel):
    # ... 现有字段 ...
    authentication: str | None = None  # "public" | "required" | "unknown"
    source: str = "code_index"         # "code_index" | "gitnexus" | "schema" | "llm_pre_recon"
```

`authentication` 是 LLM 独立扫描的核心价值之一（确定性层无法提供），必须包含在本次实现中。`source` 用于 fusion 层判断优先级。

---

## 5. 测试策略

| 测试 | 内容 | 文件 |
|---|---|---|
| 单元测试 | `parse_llm_entry_points` 解析各种 deliverable 格式 | `tests/test_entry_point_fusion.py` |
| 单元测试 | `merge_entry_points` 四源去重、确定性优先、LLM 补充 | `tests/test_entry_point_fusion.py` |
| 单元测试 | `save_adjudication` 按 confidence 阈值裁定 | `tests/test_code_index.py` |
| 集成测试 | 确定性 + LLM → fusion → 裁定 全流程 | `tests/test_entry_point_pipeline.py` |

---

## 6. 改动文件清单

| 文件 | 改动类型 | 改动量 |
|---|---|---|
| `prompts/pre-recon-code.txt` | 修改 | ~30 行（Entry Point Mapper prompt 替换 + Phase 0 简化） |
| `entry_point_fusion.py` | 修改 | ~40 行（新增 `parse_llm_entry_points` + 扩展 `merge_entry_points`） |
| `__init__.py` | 修改 | ~20 行（新增 `run_entry_point_fusion` + 修复 `save_adjudication`） |
| `workflows.py` | 修改 | ~5 行（插入 fusion 步骤） |
| `parameter_models.py` | 修改 | ~3 行（EntryPoint 增加 authentication/source 字段） |
| `tests/test_entry_point_fusion.py` | 新增/扩展 | ~80 行 |

**总改动量估计**：~180 行，其中大部分是测试代码。

---

## 7. 交叉参考

- `docs/entry-sink-gap-analysis.md` — 入口点差距矩阵（EP-1 到 EP+4）
- `docs/whitebox-refactoring-assessment.md` — 全维度评估
- 原始项目 `apps/worker/prompts/pre-recon-code.txt` — Entry Point Mapper 原始 prompt 参考
