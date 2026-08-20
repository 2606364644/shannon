# Codex Agent SDK 第三引擎设计

- **日期**：2026-08-21
- **状态**：设计定稿（brainstorming 四节全部获批），待 plan
- **动机**：openai-agents 是纯框架，运行时债持续累积（task 子代理非流式超时、tool_call arguments 消息清洗、strict 剥离、子代理 stall 兜底、L0/L1 结构化输出自补……均在 `providers_openai.py` 里手工打补丁）；claude / codex 是 CLI 运行时，这类问题天然不存在。新增 codex 引擎为 GLM 提供第二条 CLI 运行时链路，与 openai 引擎平级并存（非替代），用户按真机效果选用。
- **决策**：采用官方 Python SDK `openai-codex`（经 JSON-RPC 控制 Codex app-server，wheel 自带 pinned CLI runtime，0.144.x stable）。否决 `codex exec --json` 直调（自己管子进程=重新欠运行时债）与 Codex-as-MCP-server 编排（重新引入 openai-agents 框架层）。
- **线协议**：`wire_api = "responses"`（非 chat completions）。智谱官方为 Codex 提供专属 OpenAI Responses 协议端点 `https://open.bigmodel.cn/api/v1`（docs.bigmodel.cn/cn/coding-plan/tool/codex，官方明示 "`wire_api` 必须为 `responses`"，glm-5.3 模型元数据声明支持 reasoning summaries / parallel tool calls / freeform apply_patch）。**这使 codex 引擎完全避开 openai 引擎在 chat completions 线上踩的整类问题**（tool_call arguments 残缺 400、strict 剥离、多轮工具会话 400 家族——见 memory「openai 引擎 arguments 根类型 400」「deepsec 三轨避开 chat-completions」）。凭据用 Coding Plan 套餐 Key（与平台 API Key 不通用，团队套餐 Key 尤甚）。

---

## 1. 架构总览

新增 provider type **`codex_cli`**（命名对齐 `anthropic_api` = "CLI 运行时引擎"组）：

```
SUPERNOVA_AI_PROVIDER=codex_cli
    ↓ create_provider (providers.py)
CodexProvider(BaseProvider)          ← 新文件 providers_codex.py
    ↓ openai-codex SDK (AsyncCodex)
Codex app-server 子进程（SDK 自带 pinned CLI runtime）
    ↓ model_provider = "supernova" (wire_api="responses")
GLM 官方 Responses 端点 https://open.bigmodel.cn/api/v1
```

接线点（全部为现有扩展点，无侵入改动）：

| 接线点 | 改动 |
|---|---|
| `create_provider` | provider_map 加 `"codex_cli": CodexProvider` |
| `PROVIDER_SETTINGS` | 新条目：`SUPERNOVA_CODEX_BASE_URL` / `SUPERNOVA_CODEX_API_KEY` / `SUPERNOVA_CODEX_{SMALL,MEDIUM,LARGE}_MODEL`（required 同 openai_compatible 形态：base_url + api_key + 三个 tier model） |
| `DEFAULT_MODELS` | `codex_cli` 条目（GLM 模型名，profile 显式配置前的兜底） |
| `build_provider_config` | `_build_from_settings` 分支加 `codex_cli` |
| profile | `.env.profiles/glm-codex.env` + `.env.profiles.example/glm-codex.env.example`（定价共享 glm.pricing.json，`SUPERNOVA_PRICING_OVERRIDE` 同源） |
| 依赖 | `openai-codex` 进 core pyproject（uv workspace；版本 spike 后钉） |

**业务侧零感知**：whitebox/blackbox 只调 `run_claude_prompt(...)`，双轨 prompt 不改（CLAUDE.md §2「两引擎流程一样」约定扩展为三引擎）。

### 与现有两引擎的类别关系

| 维度 | claude 引擎 | codex 引擎（本设计） | openai 引擎 |
|---|---|---|---|
| 本质 | SDK 管 Claude Code CLI 子进程 | SDK 管 Codex app-server 子进程 | in-process 纯框架 |
| 内置工具/子代理 | CLI 原生 | CLI 原生 | 自维护 tools_openai/ |
| **线协议** | Anthropic Messages | **OpenAI Responses**（GLM 官方端点） | OpenAI Chat Completions |
| 事件协议 | `query()` SDK 事件流 | `run_streamed()` ThreadEvent 流 | `Runner.run_streamed` SDK 事件 |
| 结构化输出 | 原生 `--json-schema` 信封 | **无** → L0+L1 兜底 | 无 → L0+L1 兜底 |
| collector 桥 | in-process MCP server | **stdio MCP 子进程 + JSONL 回放**（新形态） | FunctionTool |
| 成本自算 | `pricing.compute_cost` | 同左 | 同左 |

（协议/结构化输出/桥的结论均有 deepsec 实战佐证，见 §6。）

---

## 2. CodexProvider 组件

### `call()` 主流程（`providers_codex.py`，对齐 BaseProvider 契约）

```
CodexProvider.call(prompt, cwd, model_tier, output_format, collector, ...)
 1. resolve_tier_model() → model（复用现有 tier 解析）
 2. _build_invocation():
    · CODEX_HOME = mkdtemp()（每次 call 独立，finally 清理——并发踩踏教训）
    · 生成 $CODEX_HOME/models.json（GLM 模型元数据：context_window /
      supports_parallel_tool_calls / apply_patch_tool_type 等，官方模板内置于
      仓库 data/），config 里 model_catalog_json 指向它——Codex 不认识 GLM
      模型，缺元数据则能力面（context 窗口/并行工具调用）回落错误默认
    · options.config = {
        model_provider: "supernova",           ← 自定义名（内置 provider 不可覆写，deepsec 教训）
        model_providers: {supernova: {
            name: "supernova",
            base_url: config.base_url,          ← 全前缀含 /v1（Codex 直接在后面拼 /responses，deepsec 教训）
            env_key: "SUPERNOVA_CODEX_API_KEY", ← 凭据走 env（官方文档用 experimental_bearer_token 内联，
                                                  env_key 更安全且与 deepsec 一致；spike 确认 GLM 端点两者皆收）
            wire_api: "responses",              ← 官方明示必须 responses（GLM 专属端点）
            supports_websockets: false,         ← Codex 默认 WS transport，GLM 无 WS 会 404 重连死循环（deepsec 教训）
        }},
        model_catalog_json: "<$CODEX_HOME/models.json>",
        model_max_output_tokens: <解析值>,     ← 对齐 claude 引擎 CLAUDE_CODE_MAX_OUTPUT_TOKENS：
                                                  回落链 config.max_output_tokens(P3c 字段) >
                                                  SUPERNOVA_CODEX_MAX_OUTPUT_TOKENS > 64000。
                                                  对 codex 引擎是正确性保障而非仅调参：Codex 内置
                                                  model family 不认识 GLM，未显式设置时 output 上限
                                                  回落不受控默认，过低则长 JSON 分析截断(max_tokens) →
                                                  L0 解析失败(exploitation_queue 概率性漏盘同族坑)。
                                                  注：openai 引擎现状漏此调参(ModelSettings 未设
                                                  max_tokens)，非本次范围
        features: {plugins: false, remote_plugin: false},  ← Codex 0.143+ 默认开 remote_plugin，
                                                             扫描 worker 绝不允许装 marketplace 插件（deepsec 教训）
      }                                          ← 编程注入，不写 ~/.codex/config.toml
    · options.env = 最小 env（PATH/HOME/TMPDIR + env_key 凭据变量 +
      proxy_url 非空时 HTTPS_PROXY/HTTP_PROXY/NO_PROXY=loopback）
      —— SDK env 是整体替换不是合并，必须显式构造；最小化同时是
      prompt-injection 防外泄边界（agent 的 Bash 看不到无关凭据，deepsec 教训）
    · cwd = repo_path；sandbox = danger-full-access（无条件，对齐 claude 引擎
      permission_mode=bypassPermissions 无条件的三引擎一致语义）；approval_policy
      = never；skip_git_repo_check = True
    · 网络不封禁（与 deepsec 的 networkAccessEnabled=false 有意分歧）：
      blackbox / PoC agent 需要出网探测靶场，claude 引擎同样不封
 3. collector 非空 → 注入 config.mcp_servers（stdio 桥子进程，见 §3）
 4. await thread.run_streamed(prompt) 消费事件流 → CodexStreamCollector
 5. L0 结构化输出（复用 openai_output_schema._extract_json_payload）
 6. L0 失败 → L1 修复（resume 原 thread + toolless + 低 effort 重问 JSON-only；
    spike 不通则回落 AsyncOpenAI 单 completion——openai 引擎 _lightweight_reparse 同款）
 7. finally: 读 collector JSONL 回放 + rm CODEX_HOME tempdir
```

### 事件流消费：`CodexStreamCollector`（新，参考 openai_stream_collector.py 结构）

| ThreadEvent | 处理 |
|---|---|
| `item.completed(agent_message)` | 累积文本；final 文本选择策略 = 挑含 ```json 围栏的**最后一条** agent_message（Codex 夹叙夹议，末条常是 "Done."），全无围栏则回落拼接 |
| `item.completed(command_execution / file_change / mcp_tool_call)` | → audit_logger 逐轮审计 + tool 计数 |
| `turn.completed(usage)` | TokenUsage（Responses 约定：input_tokens 含 cached，映射 `input = max(raw - cached, 0)`，对齐 openai_result_mapper）→ `pricing.compute_cost` |
| `turn.failed` / `error` | 记 error，success=False |

**不复用 `MessageDispatcher`**：它以 isinstance 匹配 claude SDK 事件类型（SDK 事件无 `.type` 属性的坑），新写轻量 collector 吃 codex 事件。

### 关键防御（deepsec 三教训落进代码）

1. **静默失败检测**：`output_tokens==0 && 无 agent_message` → success=False + retryable=True（配额/认证错误时 codex CLI exit=0 空响应、stderr 被吞）。
2. **usage 归一**：`input = max(raw - cached, 0)`（防 cost 双计）。
3. **CODEX_HOME per-call tempdir + finally 清理**（多 codex 进程共享 `~/.codex/sessions` 互踩 session DB → 静默 no-op；/tmp 塞满后 bootstrap 静默失败）。

### 暂不做的兜底（对齐 claude 引擎哲学）

不加 in-process wall-clock `wait_for`：CLI 运行时自带 HTTP 超时/重试，deepsec 未报 hang 只报静默 exit，activity 层 2h timeout 仍是最后兜底。spike 若观察到 hang 再补。

---

## 3. collector stdio MCP 桥 + 数据流

### 桥（新入口 `supernova_core/collectors/codex_mcp_server.py`）

claude 引擎的 in-process MCP server 依赖 claude_agent_sdk SDK 侧挂载，collector 对象无法跨进程共享，故走**文件回传**：

```
CodexProvider.call(collector=...) 时：
 1. 序列化 collector.section_schemas → tmp schemas.json
 2. config.mcp_servers["shannon-collector"] = {
      command: sys.executable,
      args: ["-m", "supernova_core.collectors.codex_mcp_server",
             "--schemas-file", schemas_json, "--out", out_jsonl] }
 3. agent 调 set_* / append_* → server 进程内：
      · write-once 判重保留：重复调即时返 "DuplicateError — first call wins"
        错误串（模型看得见，行为与 in-process 桥一致）
      · 非法 JSON 返错让模型重发（repair_json_arguments 的跨进程版）
      · 追加写 out.jsonl（每行 {tool, payload}）
 4. run 结束，parent 读 out.jsonl → 回放进真正的 CollectorBase
    （set_section / append_section）
```

**MCP 协议实现**：优先用环境内已有的 `mcp` python 包（若 claude-agent-sdk / agents 依赖树已带，零新依赖）；没有则手写极简 stdio JSON-RPC（`initialize` / `tools/list` / `tools/call` 三方法，~百行）。spike 确认。

**progress（`log_milestone`）MVP 不接**：仅 validate-auth agent 用、需实时回传（文件回传做不到），该 agent 继续走另两引擎。**已知边界，非遗漏。**

### 端到端数据流（一次 whitebox vuln agent call）

```
whitebox activity
  → run_claude_prompt(prompt, repo_path, model_tier, output_format, collector)
    → create_provider(ProviderConfig(type="codex_cli"))
    → CodexProvider.call
        mkdtemp CODEX_HOME
        thread.run_streamed(prompt)
          ├─ GLM 经 codex 多轮：内置工具读码/追链 + 原生 subagent 委派
          ├─ set_* → stdio MCP 桥 → out.jsonl
          └─ ThreadEvent 流 → CodexStreamCollector（审计/文本/usage/cost）
        L0 parse（```json 围栏提取）→ 失败则 L1 thread 内修复
        读 out.jsonl 回放进 CollectorBase
        finally: rm CODEX_HOME
    ← ClaudeRunResult（text/success/turns/cost/tokens/structured_output）
  → renderer 确定性渲染 deliverables（不变）
  → dual_track_merger（不变）
```

**不变量**：`ClaudeRunResult` 字段语义不变（A2 契约——success 真实反映完成/失败；error_code+retryable 分类；structured_output 传入 output_format 时有产出义务；cost best-effort）；双轨合并器 / 报告链路对引擎无感知。

---

## 4. 错误处理

| 场景 | 处理 | 对齐 |
|---|---|---|
| `turn.failed` / `error` 事件 | success=False，文案进 error；字符串走 `classify_error_for_temporal` 集中分类（quota/billing→`BillingError`、429→RateLimit、5xx→Transient） | A2 契约 + 两引擎现有语义 |
| 静默失败（0 output token 且无 agent_message） | success=False + retryable=True；附 stderr 尾巴（若可捕获） | deepsec 教训 |
| L0+L1 结构化输出全败 | structured_output=None，caller 自行降级（不 fail run） | openai 引擎同款 |
| CODEX_HOME 泄漏 | finally 必清理（所有退出路径） | deepsec 教训 |
| proxy_url | 进 options.env | Task 4 穿线先例 |

---

## 5. 测试策略与验收

### 单元测试（TDD，先红后绿）

只跑新增/相关文件（pytest 全套会 hang 的纪律）：

- `_build_invocation`：config 编程注入正确（`wire_api="responses"` / `supports_websockets=false` / plugin lockdown / `model_catalog_json` 指向生成的 models.json / `model_max_output_tokens` 回落链）、env 整体替换语义、CODEX_HOME 每次 call 独立
- 事件映射：fake `ThreadEvent` 流 → 文本/turns/usage/审计断言
- usage 归一：`input = max(raw - cached, 0)`
- final 文本选择：夹叙夹议多 agent_message 时挑含 ```json 的最后一条
- 静默失败 / 错误分类矩阵
- collector JSONL 回放：write-once 判重、append 累积、非法 JSON 返错

### 真机探针（验收标准，本次范围）

`scripts/validate_codex_task_probe.py`（对齐 `validate_openai_task_probe.py` 形态）——GLM 经 codex 跑真实 vuln prompt，断言：

1. 子代理委派发生（事件流可见 subagent 痕迹）
2. ```json 最终输出 L0 解析成功
3. token usage 非零
4. 并发 2 个 call 无 CODEX_HOME 踩踏

**NodeGoat 全流程冒烟不在本次范围**（另起任务）。

### 分阶段（→ plan 骨架）

1. **Phase 1 spike**：Python SDK API 面确认（`run_streamed` / thread resume / config 注入的 Python 对应物）+ GLM Responses 端点（`wire_api="responses"` + models.json 元数据 + env_key 凭据）最小跑通 + 子代理委派实测（探针雏形；不通则回炉设计）
2. **Phase 2**：CodexProvider 完整契约 + 单测
3. **Phase 3**：collector stdio MCP 桥 + profile/pricing/validator 接线 + 探针完整化

---

## 6. 参考与风险

### deepsec 参考（`/Users/mango/project/shannon-refactor/deepsec`，TS 版 `@openai/codex-sdk` 实战）

- `packages/processor/src/agents/codex-sdk.ts` 全部已吸收项：
  - `runStreamed` 事件流消费（ThreadEvent → 进度/审计/usage）
  - config 编程注入（`model_providers` + `wire_api` + **自定义 provider 名，内置 provider 不可覆写**）
  - **`supports_websockets: false`**（Codex 默认 WS transport，网关类端点无 WS → 404 重连死循环）
  - **base_url 全前缀规范**（Codex 直接拼 `/responses`，须含 `/v1`）
  - CODEX_HOME per-invocation tempdir + finally 清理（并发踩踏 session DB → 静默 no-op）
  - env 整体替换 + allowlist（**prompt-injection 防凭据外泄边界**：agent 的 Bash 看不到无关 secret）
  - 静默失败检测（0 output token + 无 agent_message）+ stderr wrapper（`codex-wrapper.sh` + `RUST_LOG=info`，SDK 吞 exit=0 的 stderr）
  - `chooseFinalText`（挑含 json 围栏的最后一条 agent_message）
  - thread 内 JSON 修复循环（resume + toolless + 低 effort）
  - usage 映射（Responses 约定 input 含 cached，映射时减）
  - **plugin lockdown**（`features: {plugins, remote_plugin} = false`，Codex 0.143+ 默认开 remote_plugin）
  - 订阅模式 auth.json 镜像（`findCodexSubscriptionAuth` / symlink 回真 auth.json）——本设计 MVP 不需要（GLM 走 API key），留作未来 OpenAI 官方后端的扩展路径
- 与 deepsec 的**有意分歧**（须保持，勿"对齐"回去）：
  - 不禁网（`networkAccessEnabled` 不设 false）：blackbox / PoC agent 需出网探测
  - sandbox 不做 local/workspace-write 分档：无条件 danger-full-access，对齐 claude 引擎 bypassPermissions
  - 不做 refusal follow-up / 字段级修复循环：与现有两引擎行为对齐，L0+L1 已覆盖
- `packages/processor/src/agents/claude-agent-sdk.ts`：`query()` 事件流 + env allowlist 对照

### GLM 官方 Codex 接入参考

- docs.bigmodel.cn/cn/coding-plan/tool/codex：专属 Responses 端点 `https://open.bigmodel.cn/api/v1`、`wire_api` 必须 `responses`、`~/.codex/models.json` 模型元数据（glm-5.3：context 1M / parallel tool calls / reasoning summaries / freeform apply_patch）、Coding Plan 套餐 Key（与平台 Key 不通用）

### 已知风险（spike 验证点）

1. **凭据形态**：官方文档用 `experimental_bearer_token` 内联，deepsec 用 `env_key` 走环境变量——GLM 端点对两者支持面以 spike 实测（预期都收，`env_key` 优先）。同批确认 `model_max_output_tokens` 顶层注入对自定义 provider 的生效性与默认值。
2. **worker Docker 内 codex runtime**：wheel 为 py3-none-any（76KB），runtime 二进制获取/安装方式待确认（npm `@openai/codex` 渠道 or SDK 自带解析）；worker 是 linux/amd64（ARM64 有 tree-sitter/gitnexus 前科）。⚠️ 生效须 rebuild worker。
3. **Python SDK API 面与成熟度**：TS 有 `runStreamed` / `resumeThread` / `config` 注入，Python 对应物以 spike 实测为准；L1 修复保留 AsyncOpenAI 回落路径。
4. **stderr 可见性**：Python SDK 是否同样吞 exit=0 的 stderr（deepsec 为此写了 wrapper sh）；spike 确认，必要时移植 wrapper 策略。
