# Sink 识别

Sink 识别产出精确到调用点的 `SinkCallSite`，为调用链提取和调用链研判提供终点。当前实现有四个层次：确定性规则、候选表 + LLM 判定、入口函数 LLM sink hunter、pre-recon 报告合并。

## 数据模型

`SinkCallSite` 的关键契约：

- `id = "{file}:{caller_function}:{callee}:{line}:{column}"`，下游 `TaintFlow.sink_call_site_id` 必须精确匹配。
- `category`: SQL、command、SSRF、XSS、template、file、deserialization、redirect、log。
- `sink_subtype` 细分如 `sql_raw`、`command_shell`。
- `dangerous_slots` 描述危险参数位与上下文，如 `SQL_VALUE`、`SQL_IDENTIFIER`、`CMD_ARGUMENT`、`FILE_PATH`、`TEMPLATE_EXPR`、`URL`。
- `rule_id` 标记来源：确定性规则 id、`llm-discovered`、`llm-discovered-sink`、`llm-sink-hunter`。
- `needs_review` 表示静态层精度不足，终审在 chain verdict。

`SlotContext` 是安全语义，不是参数名：SQL value 需要参数绑定，SQL identifier 需要白名单，URL 需要协议/主机控制，模板表达式需要安全渲染。

## 1. 确定性规则

规则库外部化在：

```text
packages/core/src/supernova_core/code_index/data/sink_rules.yml
```

当前 117 条，按类别分布：

| category | 数量 |
|---|---:|
| SQL | 45 |
| command | 22 |
| SSRF | 27 |
| deserialization | 8 |
| XSS | 5 |
| redirect | 4 |
| template | 3 |
| file | 3 |

`sink_detector.py` 的工作方式：

1. tree-sitter parser 遍历调用点。
2. 以 `(language, callee)` 建 O(1) 规则索引。
3. bare-function 规则只匹配无 receiver 调用；qualified 调用需 `receiver_pattern` 匹配。
4. 依据规则声明提取危险参数位。
5. SQL sink 的实参若呈 f-string、`fmt.Sprintf`、`.format`、printf、字符串拼接等动态构建形态，将槽位改标 `SQL_IDENTIFIER`，因为参数绑定无法保护标识符/DDL 片段。
6. 对静态精度不足规则（如任意 receiver 的 `query`）设置 `needs_review=True`。

未知 category/slot 在 YAML 加载期 fail-fast，避免规则拼错后静默失效。

## 2. 候选表 + LLM 判定

`sink_discovery_llm.py` 先用候选模式表筛出“规则未命中的可疑 call”，再交给多轮 discovery agent 自主 read/grep 验证。候选表：

```text
packages/core/src/supernova_core/code_index/data/sink_candidates.yml
```

当前 24 组。每组可声明：

- `languages`
- `callees`：精确 callee 名，Go/Java 保持大小写语义
- `receivers_any`：receiver 白名单；`None` 表示任意/裸调用
- `context_patterns`：调用周边文本必须命中
- `arg_patterns`：某参数表达式必须命中
- `exclude_patterns`：命中即排除

候选表只决定“是否值得问 LLM”，不直接生成 sink。典型例子：

- MongoDB `find/update/aggregate`：操作符注入取决于参数结构，直接规则化会造成海量误报。
- logger 调用：限定显式 logger receiver，避免所有 `console.log`。
- 文件读写：参数必须呈拼接、请求引用、路径 join 等风险形态。
- `Object.assign/merge`：上下文必须出现请求对象/body。
- `JSON.parse/json.loads`：上下文必须包含用户输入线索。

LLM verdict 要求返回 `call_ref/is_sink/category/slot/arg_index/rationale`。命中者转换为软 `SinkCallSite`：

- `rule_id="llm-discovered"`
- `needs_review=True`
- 无法识别的 category/slot 容错到保守值，仍会进入终审

同函数/文件候选会按 token 与 call 数双上限聚合，受 `SUPERNOVA_CHUNK_MAX_CALLS`、chunk token threshold、并发和单调用超时约束。

## 3. 入口函数 sink hunter

`discover_sinks_by_entry` 对入口 handler 中规则/候选层未覆盖的框架特有调用做自由补召回。agent 拿到函数定位线索，自主读源码，可发现本地 wrapper 或框架方法背后的真实危险操作。产出：

- `rule_id="llm-discovered-sink"`
- `needs_review=True`

该层只跑入口函数集合，避免全仓自由 LLM 扫描的调用量失控。

## 4. pre-recon 报告合并

`run_merge_sink_reports` 在 code index 与 pre-recon 完成后执行：

1. 读取 `code_index.json` 中的确定性/软 sink。
2. 读取 `pre_recon_deliverable.md`。
3. `sink_merger.py` 提取 backtick 包裹的 `file:line`，从上下文推断 category。
4. 按 `(file_path, line)` 去重，只追加 LLM-only 位置。
5. 写回 `code_index.json`。

这类 sink 的 `rule_id="llm-sink-hunter"`、`dangerous_slots=[]`、`needs_review=True`。它保留位置和类别线索，具体槽位交给后续判定，不由报告文本虚构。

## LLM 不可用与降级

`SUPERNOVA_GITNEXUS_LLM_ENABLED=0` 只关闭 GitNexus 轨的 LLM 补充，不影响纯 LLM vuln track：

- 确定性 `sink_rules.yml` 仍完整执行。
- LLM discovery / entry hunter / source/storage hunter 不产出软 sink。
- 规则 sink 的 `is_entry_hint` 仍提供保守入口参数线索，供确定性 intra fallback 和后续传播使用。
- pre-recon 本身仍运行，因为 recon/authz/auth 输入不受该开关关闭；但其 sink 报告只作为允许的 pre-recon → GitNexus 轨补充，不会反向注入 vuln prompt。

## 与其他组件的关系

- 入口识别提供 handler 集合，source 规则提供输入锚点；sink 是终点锚点。
- `call-chain-extraction` 将 source/sink/调用链合成 `TaintFlow`。
- `call-chain-verdict` 是唯一终审层，软 sink 与 `needs_review` 不应直接当漏洞结论。
- `rule_gap_report.json` 聚合 LLM 确认的规则缺口，用于反哺 YAML。

## 扩展方式

- 确定性、无歧义的调用：改 `sink_rules.yml`。
- 危险取决于 receiver、上下文或参数结构：改 `sink_candidates.yml`，保持候选表降噪。
- 完全新的框架/本地 wrapper：先看 entry hunter 能否覆盖，再考虑 prompt/agent 层。
- 修改规则时同步更新规则测试与 wheel force-include 说明，避免“源码环境可见、安装包缺 YAML”的回归。

## 验证入口

- `packages/core/tests/code_index/test_sink_detector.py`
- `packages/core/tests/code_index/test_sink_rules_hardening.py`
- `packages/core/tests/code_index/test_sink_discovery_llm.py`
- `packages/core/tests/code_index/test_sink_hunter_llm.py`
- `packages/core/tests/code_index/test_sink_merger.py`
