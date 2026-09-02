# 公司内部网关 llm-proxy thinking 参数支持矩阵（哪些模型能带 `thinking` 参数）

> 2026-09-02 实测盘点，sammydu `shorturl-20260901-103513` 扫描三连灭事故驱动。
> 一句话：**`thinking: {"type": "disabled"}` 不是全网关通用参数——coder 系自部署路由 / azure 路由 / glm-5.3 的 litellm 参数白名单直接 400 拒收，请求根本到不了模型；工作区模型 ∈ 拒绝列表且 `SUPERNOVA_ADAPTIVE_THINKING=false`（默认）时，首个 agent 即全灭、整场 failed。**

---

## 一、背景：为什么会错

- `b7076ded`（2026-09-01，NodeGoat-20260901-015018 诊断落地）给 openai 引擎接线「thinking 全局开关」：`ProviderConfig.adaptive_thinking=False` 时 client 包装层对**每个请求**注入 `thinking={"type":"disabled"}`（`packages/core/src/supernova_core/agents/providers_openai.py` 的 `_wrap_client_for_argument_sanitize`，经 `extra_body` 合并进请求体顶层）。新工作区默认关（`SUPERNOVA_ADAPTIVE_THINKING=false`）。
- 当时「llm-proxy 唯一有效关法」的 A/B 实测（单轮 19s→7s、completion -73%）**只在 `deepseek-v4-flash-0731` 直名路由上做过**，结论被泛化到整个网关。
- sammydu 工作区模型是 `deepseek-v4-flash-coder`（proxy 别名，路由到另一条 openai 兼容自部署，底层模型名也叫 deepseek-v4-flash-0731）。这条路由的 litellm 按 openai 参数白名单校验，`thinking` 不在白名单 → 400 → pre-recon 3 次重试全灭（确定性错误重试无用）→ 整场 failed。`__legacy__` 工作区同模型，同样会挂。
- 讽刺点：`deepseek-v4-flash-coder` 路由**默认就不 think**（实测数学题直答、无 reasoning token），注入纯属多余还致命。

## 二、矩阵（2026-09-02 逐模型 curl 实测）

### ❌ 拒绝（litellm 参数白名单拦截，HTTP 400，请求到不了模型）

| 模型 | 路由类型 | 错误 |
|---|---|---|
| **`deepseek-v4-flash-coder`** | openai 兼容自部署 | `litellm.UnsupportedParamsError: openai does not support parameters: ['thinking']` |
| **`glm-5.2-coder`** | openai 兼容自部署 | 同上 |
| `glm-5.3` | anthropic 风格路由 | `anthropic does not support parameters: ['thinking']`（拒绝 `disabled` 格式；anthropic 映射要求 `enabled` + budget） |
| `gpt-5.2` / `gpt-4.1` / `o3` / `o4-mini` | azure | `azure does not support parameters: ['thinking']` |
| `MiniMax-M2.5` | — | 裸 HTTP 400（无 litellm 错误体） |

### ✅ 放行（HTTP 200，参数被网关接受）

- **deepseek 官方直名**：`deepseek-v4-flash`、`deepseek-v4-flash-0731`、`deepseek-v4-pro`、`deepseek-v4-pro-0813`
- **glm**：`glm-5`、`glm-5.2`
- **claude（bedrock）**：`claude-haiku-4-5-20251001`、`claude-opus-4-8`
- **其他**：`kimi-k3`、`kimi-k2.7-code`、`minimax-m3`、`qwen3-coder-plus`、`qwen3.5-flash`、`doubao-seed-2-0-pro`、`gemini-3-pro-preview`

### ⚠️ 无法测试

- `deepseek-chat` / `deepseek-r1` / `deepseek-reasoner`：后端 503（底层 `ms-xxx` 部署不可用），连不带参数的基线请求都不通。

## 三、规律

1. **路由类型决定行为，与模型本身无关**——拒绝的都是 litellm 里按 `openai/`（openai 兼容自部署，`*-coder` 系列）和 `azure/` 配置的路由；官方 API 直连路由（deepseek 官方、bigmodel glm、moonshot、minimax、dashscope qwen、火山 doubao、google gemini、bedrock claude）基本都放行。
2. **放行 ≠ 生效**。放行只代表 litellm 接受该参数，可能透传生效、也可能静默忽略。只有 `deepseek-v4-flash-0731` 有生效实证（19s→7s、reasoning 归零）；`deepseek-v4-flash-coder` 路由默认不 think，「生效」无从谈起也无需。
3. **别名映射藏在报错里**：`claude-sonnet-5` 实为 `glm-5.3` 的别名（拒绝报错里 `for model=glm-5.3`）；`deepseek-v4-flash-coder` 底层即 `deepseek-v4-flash-0731`（同一底层名、两条不同配置的路由）。
4. 网关是 litellm proxy：错误体自带解法提示 `litellm_settings: drop_params: true` 或 `allowed_openai_params=['thinking']`——那是网关管理员的治本开关，本 repo 侧改不了。

## 四、对 supernova 的影响与操作指引

| 工作区模型 | `SUPERNOVA_ADAPTIVE_THINKING=false`（默认） | `=true` / 未设 |
|---|---|---|
| `deepseek-v4-flash-coder`、`glm-5.2-coder`、`glm-5.3`、azure 系 | **每请求 400，扫描秒 failed**（不注入即正常） | 正常（模型默认行为） |
| `deepseek-v4-flash-0731` 等放行路由 | 正常，且关掉 thinking 计费（生效实证） | 正常（0731 默认开 thinking，reasoning 计入 completion 计费，见 `docs/scan-time-gates.md` 模型敏感性） |

- **止血**：工作区把 `SUPERNOVA_ADAPTIVE_THINKING` 改 `true`（或删该行）——仅当模型在拒绝列表时；coder 路由默认不 think，零性能损失。
- **治本（代码防御，待排期）**：`providers_openai` 注入撞 `UnsupportedParamsError` 时自动去掉 `thinking` 重发一次，对所有路由一劳永逸，不再依赖网关配置。
- **网关侧**：管理员给 coder 路由加 `drop_params: true`。

## 五、复测方法（网关配置可能变化，矩阵有时效性）

每模型两发 A/B，唯一差异是 `thinking` 参数；A 挂 B 成 = 参数被拒：

```bash
KEY=<网关 key，如 .env.profiles/deepseek-ft-openai.env 的 SUPERNOVA_OPENAI_API_KEY>
URL=https://llm-proxy.futuoa.com/v1/chat/completions
# A：带 thinking（复现注入形态）
curl -sS "$URL" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"<模型名>","messages":[{"role":"user","content":"hi"}],"max_tokens":8,"thinking":{"type":"disabled"}}'
# B：不带（基线，确认模型本身可用）
curl -sS "$URL" -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"<模型名>","messages":[{"role":"user","content":"hi"}],"max_tokens":8}'
```

判定：A 400 `UnsupportedParamsError` = 拒绝；A 200 = 放行；B 也失败 = 模型不可用（503 等），参数未测。判定「放行且生效」（真关掉 thinking）需另发带推理负载的对比题（参考 19s→7s 实测法）。

---

关联：memory `llm-thinking-perf-diagnosis`（同矩阵速查版）；`docs/scan-time-gates.md`（thinking 计费对判链窗口的容量影响）；commit `b7076ded`（开关接线）、`09b93eb5`（extra_body 注入）。
