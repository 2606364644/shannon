# 入口识别

入口识别解决“哪些函数可从外部触达，以及它们的 HTTP method/route/认证语义是什么”。它同时服务两个层面：

- **EntryPoint**：候选入口函数，是调用链和 authz IDOR 候选的锚点。
- **SourcePoint**：入口函数体内具体的外部输入取用点，例如 `req.body.id`、`request.GET["q"]`，是 taint source。

二者不能混淆：入口是函数/路由级事实，source 是参数/表达式级事实。

## 识别来源

### 1. 语言/框架规则

`code_index/entry_points.py::detect_entry_points` 基于 tree-sitter 提取的函数块、装饰器和源码文本识别：

- Python：Flask/FastAPI/Django route、API view、Celery task；未知 `async def` 给低置信度候选。
- Go：Echo/Gin/Chi 等 route 注册、`main()` CLI 候选。
- TypeScript/JavaScript：NestJS 装饰器、Express handler、文件级/top-level Express route。
- Java：Spring `@GetMapping` 等、JAX-RS 注解。
- PHP：Laravel/Symfony 注解与路由风格。

规则输出 `EntryPoint`，核心字段：

- `func_block_id`
- `entry_type`: `http_route`、`message_consumer`、`webhook`、`cli`、`gitnexus_process`、`unknown`
- `route` / `http_method`
- `confidence` / `needs_llm_review`
- `authentication`: `public` / `required` / `unknown`
- `source`

`LLM_REVIEW_THRESHOLD = 0.8`：低于该置信度的候选需要复核；后续裁决阈值见下文。

### 2. GitNexus process trace

`gitnexus_call_graph.py` 读取 GitNexus 索引期预计算的 entry→terminal 调用链。链头被去重为 process entry。若链头未被语言规则识别，code index 会补：

```python
EntryPoint(
    entry_type="gitnexus_process",
    confidence=0.9,
    source="gitnexus",
)
```

当前融合语义是 **语言规则结果 ∪ GitNexus process entry**：同 id 时保留语言规则的 route/method 元信息；GitNexus 未被语言规则覆盖的链头补为 process entry。旧文档中的“GitNexus 交集守门”已经不是当前行为。

### 3. OpenAPI / Swagger schema

`schema_entry_parser.py` 扫描 `openapi.*` / `swagger.*` 文件，每个 `paths[method path]` 生成：

- `entry_type="http_route"`
- `confidence=0.80`
- `needs_llm_review=True`
- `authentication` 根据 operation/path/root `security` 继承推导

schema 是入口声明，不保证 handler 已实现，所以不直接达到 confirmed 阈值。

### 4. pre-recon LLM 发现

`pre-recon-code` prompt 要求 agent 自主寻找网络可达入口、webhook、上传、schema，并在 attack surface 章节写出 `METHOD /path — file:function`。`entry_point_fusion.parse_llm_entry_points` 解析该 Markdown，生成 confidence 0.60 的 `llm_pre_recon` 入口。

这是 **pre-recon LLM 输出单向补充确定性入口集合**；方向不是把 GitNexus source/sink/chain hints 喂给 vuln LLM 轨。

## 融合与裁决时序

`WhiteboxScanWorkflow` 中 code index 与 pre-recon 并行执行，随后：

```text
run_code_index
  -> code_index.json / parameter_graph.json
run_agent(PRE_RECON)
  -> pre_recon_deliverable.md
run_merge_sink_reports
run_entry_point_fusion
run_save_adjudication
```

`run_entry_point_fusion` 读取融合后的 `code_index.json`、pre-recon deliverable 和 OpenAPI 文件：

1. 保留现有 `index.entry_points`。
2. 追加 LLM-only 入口。
3. 追加 schema-only 入口。
4. 去重键是 `func_block_id`；已存在的确定性入口优先。
5. 原子写回 `whitebox/intermediate/code_index.json`。

`save_adjudication` 只按 confidence 做机器裁决：

| confidence | verdict |
|---|---|
| `>= 0.85` | `CONFIRMED` |
| `0.50 .. 0.84` | `NEEDS_REVIEW` |
| `< 0.50` | `REJECTED` |

产物是 `whitebox/intermediate/entry_points.json`。这是后续 authz、recon、报告接口表的路由表，不再要求 pre-recon agent 手写 `entry_points.json`。

## SourcePoint 识别

`source_detector.py` 只扫描入口函数集合，避免把所有内部函数参数都当成外部输入。规则外部化在：

```text
packages/core/src/supernova_core/code_index/data/source_rules.yml
```

当前共 68 条规则，覆盖 TypeScript/JavaScript、Python、Go、Java、PHP，来源类型包括 `path`、`query`、`body`、`form`、`header`、`cookie`、`file`。每条规则要求正则 group(1) 捕获参数名；未知 `source_type` 在加载期 fail-fast。

`SourcePoint` 保存：

- 参数名和精确 `ParameterSource`
- 原表达式、文件行号
- 附近简单 validation 线索（`parseInt`、escape/sanitize、regex）
- `rule_id`、confidence、去重键 `(entry_point_id, param_name, source_type)`

另有 GitNexus 轨内部的 source 补召回：source function 规则、LLM source hunter、storage read hunter。详见 [Sink 识别](sink-identification.md) 与 [调用链提取](call-chain-extraction.md)。

## 排除与边界

- 共享排除名单会跳过依赖、构建产物、测试目录、隐藏目录。
- CLI/本地开发脚本默认低置信度，只有明显 `main`/命令入口形状才保留候选；pre-recon prompt 明确要求排除本地开发工具。
- OpenAPI path 与代码 handler 未做强绑定；报告/验证阶段仍需源码或 live target 确认。
- `authentication` 是入口元信息，不等于漏洞判定中的 `externally_exploitable`。后者是网络可达性标签，不能被 verdict 覆写。

## 扩展方式

- 新框架入口：优先改 `entry_points.py` 的语言规则和对应测试；确属外部输入取用模式才改 `source_rules.yml`。
- 新 schema 形态：扩展 `schema_entry_parser.py`，保持 parse 失败非致命、confidence 不虚标。
- 新 source 取用方式：改 `source_rules.yml`，不改 detector 逻辑；加载器会校验枚举。
- 想提升召回时先确认融合语义：语言规则与 GitNexus process entry 已是并集，schema/LLM 只追加确定性集合没有的 id。

## 验证入口

- `packages/core/tests/code_index/test_entry_points.py`
- `packages/core/tests/code_index/test_entry_point_fusion.py`
- `packages/core/tests/code_index/test_schema_entry_parser.py`
- `packages/core/tests/code_index/test_source_detector.py`
- `packages/whitebox/tests/` 中 pre-recon adjudication / workflow wiring 相关测试
